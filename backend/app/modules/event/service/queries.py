"""事件查询与两个固定聚合。

形状见契约 §8 / §9。**不接受参数化扩展**：只做这两个视图 + 下钻列表。加第三个
聚合端点等于把本模块做成通用 BI，那是主规划 §5 明确不做的。

跨模块读表的纪律（服务端设计 §1）：
    join 视图要「这个 session 有没有对应 trajectory 行」，直觉是 `select(Trajectory)`，
    那会被 `test_no_cross_module_model_import` 打红。
    **做法**：用 SQL 文本表达式直接读 `trajectories` 表。这仍然是「跨模块读表」，
    但**不是 import 别人的 model**，门禁放行、且耦合面是一列名（`session_id`）
    而不是整个 ORM 类。**这是有意的** —— 不要顺手改成 import Trajectory。
    过滤软删除：`deleted_at IS NULL`，与轨迹模块所有查询同一条纪律。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utc_now
from app.modules.event.model import Event, EventReject
from app.modules.event.schemas import (
    EventItem,
    EventListResponse,
    EventRejectItem,
    EventRejectListResponse,
    PolicyAuditItem,
    PolicyAuditResponse,
    SessionCoverageItem,
    SessionCoverageResponse,
)

# 有意跨模块读表：只读 trajectories.session_id，不 import Trajectory。
# 软删除过滤与轨迹模块所有查询同一条纪律（deleted_at IS NULL）。
_TRAJ_EXISTS_SQL = text(
    "SELECT 1 FROM trajectories WHERE session_id = :sid AND deleted_at IS NULL LIMIT 1"
)
_TRAJ_WITHOUT_EVENTS_SQL = text(
    """
    SELECT t.session_id, COALESCE(t.device_id, '') AS device_id
    FROM trajectories t
    WHERE t.deleted_at IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM events e
          WHERE e.session_id = t.session_id
      )
    ORDER BY t.uploaded_at DESC
    LIMIT :lim
    """
)


def _loads(raw: Optional[str]) -> dict[str, Any]:
    """出库还原。坏数据不能让整个查询 500：一条脏行把列表打成 500 等于管理台不可用。"""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw, "_truncated": True}
    return value if isinstance(value, dict) else {"_raw": raw}


def _to_item(row: Event) -> EventItem:
    return EventItem(
        id=row.id,
        event_name=row.event_name,
        device_id=row.device_id,
        org_id=row.org_id,
        team_id=row.team_id or "",
        session_id=row.session_id,
        client_ts=row.client_ts,
        received_at=row.received_at,
        metadata=_loads(row.metadata_json),
    )


async def list_events(
    db: AsyncSession,
    *,
    session_id: Optional[str] = None,
    device_id: Optional[str] = None,
    event_name: Optional[str] = None,
    org_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> EventListResponse:
    """下钻列表。只做契约 §8 那几个维度，metadata 内部字段筛选是 BI，不做。"""
    stmt = select(Event)
    count_stmt = select(func.count()).select_from(Event)
    if session_id:
        stmt = stmt.where(Event.session_id == session_id)
        count_stmt = count_stmt.where(Event.session_id == session_id)
    if device_id:
        stmt = stmt.where(Event.device_id == device_id)
        count_stmt = count_stmt.where(Event.device_id == device_id)
    if event_name:
        stmt = stmt.where(Event.event_name == event_name)
        count_stmt = count_stmt.where(Event.event_name == event_name)
    if org_id:
        stmt = stmt.where(Event.org_id == org_id)
        count_stmt = count_stmt.where(Event.org_id == org_id)
    if since:
        stmt = stmt.where(Event.received_at >= since)
        count_stmt = count_stmt.where(Event.received_at >= since)

    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    rows = (
        await db.execute(stmt.order_by(Event.received_at.desc(), Event.id.desc()).limit(limit))
    ).scalars().all()
    return EventListResponse(total=total, items=[_to_item(r) for r in rows])


async def list_rejects(db: AsyncSession) -> EventRejectListResponse:
    """被拒事件名。total 是 count 之和 —— 管理台首屏那个数。"""
    rows = (
        await db.execute(select(EventReject).order_by(EventReject.count.desc(), EventReject.event_name))
    ).scalars().all()
    items = [
        EventRejectItem(
            event_name=r.event_name,
            reason=r.reason or "",
            count=r.count,
            first_seen_at=r.first_seen_at,
            last_seen_at=r.last_seen_at,
        )
        for r in rows
    ]
    return EventRejectListResponse(total=sum(i.count for i in items), items=items)


async def session_coverage(db: AsyncSession, *, limit: int = 50) -> SessionCoverageResponse:
    """每个会话：事件条数、三类新事件各自条数、是否有对应 trajectory 行。

    必须同时给出三类计数（服务端设计 §4），缺一不可：
      1. 有事件的会话
      2. 其中有 trajectory 行的
      3. **有 trajectory 但零事件的** —— 这是「事件通道没接上」的唯一信号。
         只做前两类等于让缺口隐身（主规划 §7「防线全在、调用全 0」）。

    `limit` 默认 50，按 received_at 倒序。**不做全表扫描的无 limit 版本。**
    """
    # 有事件的会话：先按 session_id 聚合，再对结果做 EXISTS。
    # session_id IS NULL 的那些事件没有 join key，单独算一条「无会话」行不进覆盖。
    grouped = (
        select(
            Event.session_id,
            func.count().label("event_count"),
            func.max(Event.device_id).label("device_id"),
            func.min(Event.client_ts).label("first_client_ts"),
            func.max(Event.client_ts).label("last_client_ts"),
            func.max(Event.received_at).label("last_received_at"),
            func.sum(case((Event.event_name == "policy_enforced", 1), else_=0)).label("policy_enforced"),
            func.sum(case((Event.event_name == "guardrail_triggered", 1), else_=0)).label("guardrail_triggered"),
            func.sum(case((Event.event_name == "context_assembled", 1), else_=0)).label("context_assembled"),
        )
        .where(Event.session_id.is_not(None))
        .group_by(Event.session_id)
        .subquery()
    )
    event_rows = (
        await db.execute(
            select(grouped).order_by(grouped.c.last_received_at.desc()).limit(limit)
        )
    ).all()

    items: list[SessionCoverageItem] = []
    for row in event_rows:
        exists = (await db.execute(_TRAJ_EXISTS_SQL, {"sid": row.session_id})).first()
        items.append(
            SessionCoverageItem(
                session_id=row.session_id,
                has_trajectory=exists is not None,
                event_count=int(row.event_count or 0),
                policy_enforced=int(row.policy_enforced or 0),
                guardrail_triggered=int(row.guardrail_triggered or 0),
                context_assembled=int(row.context_assembled or 0),
                device_id=row.device_id or "",
                first_client_ts=row.first_client_ts,
                last_client_ts=row.last_client_ts,
            )
        )

    # 第三类：有轨迹但零事件。单独一条 SQL，不和上面混 —— 混了会让「limit 50」
    # 把缺口挤出画面。items 受 limit 约束；四个汇总数字走 COUNT，不被截断。
    missing_rows = (await db.execute(_TRAJ_WITHOUT_EVENTS_SQL, {"lim": limit})).all()
    for row in missing_rows:
        items.append(
            SessionCoverageItem(
                session_id=row.session_id,
                has_trajectory=True,
                event_count=0,
                device_id=row.device_id or "",
            )
        )

    sessions_with_events = int(
        (
            await db.execute(
                text("SELECT COUNT(DISTINCT session_id) FROM events WHERE session_id IS NOT NULL")
            )
        ).scalar()
        or 0
    )
    with_traj = int(
        (
            await db.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT e.session_id)
                    FROM events e
                    WHERE e.session_id IS NOT NULL
                      AND EXISTS (
                          SELECT 1 FROM trajectories t
                          WHERE t.session_id = e.session_id AND t.deleted_at IS NULL
                      )
                    """
                )
            )
        ).scalar()
        or 0
    )
    missing_count = int(
        (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM trajectories t
                    WHERE t.deleted_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM events e WHERE e.session_id = t.session_id
                      )
                    """
                )
            )
        ).scalar()
        or 0
    )

    return SessionCoverageResponse(
        sessions_with_events=sessions_with_events,
        with_trajectory=with_traj,
        without_trajectory=max(sessions_with_events - with_traj, 0),
        trajectory_without_events=missing_count,
        items=items,
    )


def _default_since_iso() -> str:
    """policy-audit 默认窗口 7 天。metadata 存 Text 意味着误报拆分要在 Python 侧
    json.loads，不能靠 SQL。全表 loads 会把响应拖到秒级 —— **这是 Text 列的代价**，
    不是随手写的限制。窗口必须收窄。
    """
    return (utc_now() - timedelta(days=7)).isoformat()


async def policy_audit(
    db: AsyncSession,
    *,
    since: Optional[str] = None,
    org_id: Optional[str] = None,
) -> PolicyAuditResponse:
    """按 device 聚合：policy_enforced 拆 applied / none-or-error，
    guardrail_triggered 拆真报 / 误报 / unknown，加上 permission_deny。

    metadata 存 Text，拆分只能在 Python 侧做（json.loads 再分桶）。所以必须带
    `since` 且默认窗口 7 天 —— 这是 Text 列的代价，不要让下一个人以为是随手写的限制。
    """
    since_iso = since or _default_since_iso()
    stmt = select(Event).where(Event.received_at >= since_iso)
    if org_id:
        stmt = stmt.where(Event.org_id == org_id)
    # 只拉三种相关事件：其它事件名对这个视图没有贡献，拉过来只会让 json.loads 变慢。
    stmt = stmt.where(
        Event.event_name.in_(("policy_enforced", "guardrail_triggered", "permission_deny"))
    )
    rows = (await db.execute(stmt)).scalars().all()

    buckets: dict[str, PolicyAuditItem] = {}
    for row in rows:
        item = buckets.get(row.device_id)
        if item is None:
            item = PolicyAuditItem(
                device_id=row.device_id,
                org_id=row.org_id,
                team_id=row.team_id or "",
                last_received_at=row.received_at,
            )
            buckets[row.device_id] = item
        elif row.received_at and (item.last_received_at is None or row.received_at > item.last_received_at):
            item.last_received_at = row.received_at
            # org/team 以最近一条为准（设备换组织极少发生，不为此建历史）
            item.org_id = row.org_id
            item.team_id = row.team_id or ""

        meta = _loads(row.metadata_json)
        if row.event_name == "permission_deny":
            item.permission_deny += 1
        elif row.event_name == "policy_enforced":
            outcome = meta.get("outcome")
            if outcome == "applied":
                item.policy_applied += 1
            else:
                # none / error / unchanged / cache_fallback 都进这一列：
                # 「策略真正被应用」之外的所有结局。unchanged 不是失败，但管理台要的是
                # 「这次有没有把一份新策略应用到运行时」，304 不算 applied。
                item.policy_none_or_error += 1
        elif row.event_name == "guardrail_triggered":
            item.guardrail_total += 1
            signal = meta.get("falsePositive")
            if signal == "confirmed_true_positive":
                item.guardrail_true_positive += 1
            elif signal == "suspected_false_positive":
                item.guardrail_false_positive += 1
            else:
                # 缺字段 / unknown / 拼错都进 unknown。unknown 既不算真报也不算误报。
                item.guardrail_unknown += 1

    items = sorted(buckets.values(), key=lambda x: x.last_received_at or "", reverse=True)
    return PolicyAuditResponse(since=since_iso, items=items)
