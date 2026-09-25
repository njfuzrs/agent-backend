"""预算求值、下发组装与管理台 CRUD。

下发（`evaluate` / `delivery_body`）与管理（create / update / disable）在同一个
文件里，是为了让「写进去的和发出去的是同一份口径」一眼可见 —— 尤其是
disabled 的预算不进下发、used_usd 现算这两条，分两个文件写容易一边改一边忘。

失败语义（契约 §6）：`GET /ctl/budget` 是 fail-open。客户端拉不到就当没配远程
预算，本地 costLimit 仍硬停。管理台写入是 fail-closed：`reason` 空、未知
enforcement 一律 422。

求值抄 policy：device > team > org，**不合并**。team 匹配必须带 org_id。
求值预览「这个 device 命中哪一层」走 identity.service.admin，**不 import
identity.model**。

used_usd 只读本模块的 usage_ledger，不读 trajectories.total_cost_usd。
两份数对不上时以账本为准。命中层的设备集合也只按账本上的 org_id / team_id
文本副本筛 —— 不 join identity 的表（跨模块直接查表会被边界测试打红）。
账本行的归属来自上报时的 DeviceContext，与设备当前所属可能漂移，这是有意的：
成本归属讲的是「上报当时那台设备」，不是「现在这台设备换了 team」。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext
from app.core.logging import current_actor, current_request_id, get_logger
from app.core.timeutil import utc_now_iso
from app.modules.cost.model import Budget, BudgetAudit, UsageLedger
from app.modules.cost.schemas import (
    BudgetAuditItem,
    BudgetAuditListResponse,
    BudgetCreate,
    BudgetDeleteResponse,
    BudgetItem,
    BudgetListResponse,
    BudgetUpdate,
)
from app.modules.cost.service.guard import (
    current_period_key,
    period_bounds,
    validate_enforcement,
    validate_period,
    validate_scope_type,
)
from app.modules.identity.service import admin as identity_admin

logger = get_logger("agent.cost")


def _dumps(value: Any) -> str:
    """存库用紧凑 JSON。ensure_ascii=False 让中文 reason 在库里可读。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: Optional[str]) -> Any:
    """出库还原。坏数据不能让整个下发端点 500（fail-open 通道）。"""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def snapshot_of(budget: Budget) -> dict[str, Any]:
    """审计用的预算快照。不含 used_usd（那是现算的，不是配置）。"""
    return {
        "scope_type": budget.scope_type,
        "scope_id": budget.scope_id,
        "org_id": budget.org_id,
        "period": budget.period,
        "limit_usd": budget.limit_usd,
        "enforcement": budget.enforcement,
        "disabled_at": budget.disabled_at,
    }


def delivery_body(budget: Budget, used_usd: float, period_key: str) -> dict[str, Any]:
    """组装客户端下发 body。source 写死 remote。"""
    return {
        "source": "remote",
        "scope_type": budget.scope_type,
        "scope_id": budget.scope_id,
        "period": budget.period,
        "period_key": period_key,
        "limit_usd": budget.limit_usd,
        "used_usd": used_usd,
        "enforcement": budget.enforcement or "alert",
        "updated_at": budget.updated_at,
    }


def etag_of(body: dict[str, Any]) -> str:
    """规范化 JSON 的 sha256 前 16 hex，带引号。

    ETag 必须纳入 used_usd。用量在变，客户端要用 If-None-Match 感知「该告警了」。
    代价：用量每次 upsert 都可能让下次 GET 不再 304。第一版可接受。
    """
    canonical = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


# ---------------------------------------------------------------------------
# used_usd 现算
# ---------------------------------------------------------------------------
async def sum_used_usd(
    db: AsyncSession,
    *,
    scope_type: str,
    scope_id: str,
    org_id: str,
    period: str,
    period_key: Optional[str] = None,
) -> float:
    """从 usage_ledger 按命中层的设备集合、按 period_key 对 cost_usd 求和。

    含影子调用（cost_usd 已经含）。NULL 的 side_cost_usd 不另加。

    period=session 的语义有点拧：服务端看不到「这一轮会话还在不在」。第一版就用
    该设备最新一行，注释写明。真正的 session 级硬停继续由本地 costLimit 负责。
    """
    key = period_key or current_period_key(period)
    filters = _scope_filters(scope_type, scope_id, org_id)
    if filters is None:
        return 0.0

    if period == "session":
        # 该设备最新一行。team/org 的 session 预算几乎没人会配；仍按集合里
        # received_at 最新的那一行算，避免 SUM 把多会话加起来冒充「当前会话」。
        latest = (
            select(UsageLedger.cost_usd)
            .where(*filters)
            .order_by(UsageLedger.received_at.desc())
            .limit(1)
        )
        row = await db.execute(latest)
        value = row.scalar()
        return float(value or 0.0)

    start, end = period_bounds(period, key)
    stmt = (
        select(func.coalesce(func.sum(UsageLedger.cost_usd), 0.0))
        .where(*filters)
        .where(UsageLedger.ts >= start, UsageLedger.ts < end)
    )
    row = await db.execute(stmt)
    return float(row.scalar() or 0.0)


# ---------------------------------------------------------------------------
# 下发求值（device > team > org，不合并）
# ---------------------------------------------------------------------------
async def evaluate(db: AsyncSession, ctx: DeviceContext) -> Optional[Budget]:
    """按 device > team > org 返回至多一份 enabled 预算。不合并字段。

    team 匹配带 org_id：防止跨组织同名 team 串台。ctx.team_id 为空时跳过 team 层。
    """
    device = await _find_enabled(db, "device", ctx.device_id)
    if device is not None:
        return device
    if ctx.team_id:
        team = await _find_enabled(db, "team", ctx.team_id, org_id=ctx.org_id)
        if team is not None:
            return team
    return await _find_enabled(db, "org", ctx.org_id, org_id=ctx.org_id)


async def evaluate_for_device(db: AsyncSession, device_id: str) -> Optional[Budget]:
    """管理台预览用。走 identity service 查设备，不 import identity.model。"""
    ref = await identity_admin.get_device(db, device_id)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"device {device_id!r} 不存在")
    ctx = DeviceContext(device_id=ref.device_id, org_id=ref.org_id, team_id=ref.team_id)
    return await evaluate(db, ctx)


async def to_evaluate_payload(db: AsyncSession, budget: Budget) -> dict[str, Any]:
    """管理台 evaluate 响应。layer = 命中的 scope_type。"""
    key = current_period_key(budget.period)
    used = await sum_used_usd(
        db,
        scope_type=budget.scope_type,
        scope_id=budget.scope_id,
        org_id=budget.org_id,
        period=budget.period,
        period_key=key,
    )
    return {
        "layer": budget.scope_type,
        "budget_id": budget.id,
        "scope_id": budget.scope_id,
        "org_id": budget.org_id,
        "period": budget.period,
        "period_key": key,
        "limit_usd": budget.limit_usd,
        "used_usd": used,
        "enforcement": budget.enforcement,
    }


# ---------------------------------------------------------------------------
# 管理台 CRUD（cookie 会话）
# ---------------------------------------------------------------------------
async def list_budgets(
    db: AsyncSession,
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> BudgetListResponse:
    stmt = select(Budget).order_by(Budget.id.desc())
    if scope_type:
        stmt = stmt.where(Budget.scope_type == scope_type)
    if scope_id:
        stmt = stmt.where(Budget.scope_id == scope_id)
    if org_id:
        stmt = stmt.where(Budget.org_id == org_id)
    rows = await db.execute(stmt)
    items = []
    for b in rows.scalars().all():
        items.append(await _to_item(db, b))
    return BudgetListResponse(items=items)


async def get_budget(db: AsyncSession, budget_id: int) -> BudgetItem:
    return await _to_item(db, await _require(db, budget_id))


async def create_budget(db: AsyncSession, payload: BudgetCreate, actor: str) -> BudgetItem:
    reason = _require_reason(payload.reason)
    scope_type = validate_scope_type(payload.scope_type)
    period = validate_period(payload.period)
    enforcement = validate_enforcement(payload.enforcement)
    scope_id = payload.scope_id.strip()
    org_id = payload.org_id.strip()
    if not scope_id or not org_id:
        raise HTTPException(status_code=422, detail="scope_id / org_id 不能为空")

    org_id = await _resolve_scope(db, scope_type, scope_id, org_id)

    existing = await _find_enabled(
        db,
        scope_type,
        scope_id,
        org_id=org_id,
        period=period,
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"同层同周期已有生效预算（{scope_type}:{scope_id}/{period}）。"
                "改内容请更新那一条，或先停用再新建。"
            ),
        )

    now_iso = utc_now_iso()
    budget = Budget(
        scope_type=scope_type,
        scope_id=scope_id,
        org_id=org_id,
        period=period,
        limit_usd=payload.limit_usd,
        enforcement=enforcement,
        created_at=now_iso,
        updated_at=now_iso,
        updated_by=actor,
    )
    db.add(budget)
    await db.flush()
    _audit(
        db,
        budget_id=budget.id,
        scope_type=scope_type,
        scope_id=scope_id,
        action="create",
        old_json=None,
        new_json=_dumps(snapshot_of(budget)),
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("create", str(budget.id))
    return await _to_item(db, budget)


async def update_budget(db: AsyncSession, budget_id: int, payload: BudgetUpdate, actor: str) -> BudgetItem:
    reason = _require_reason(payload.reason)
    budget = await _require(db, budget_id)
    now_iso = utc_now_iso()
    old_json = _dumps(snapshot_of(budget))

    if payload.limit_usd is not None:
        budget.limit_usd = payload.limit_usd
    if payload.enforcement is not None:
        budget.enforcement = validate_enforcement(payload.enforcement)
    if payload.period is not None:
        new_period = validate_period(payload.period)
        if new_period != budget.period and budget.disabled_at is None:
            clash = await _find_enabled(
                db,
                budget.scope_type,
                budget.scope_id,
                org_id=budget.org_id,
                period=new_period,
            )
            if clash is not None and clash.id != budget.id:
                raise HTTPException(
                    status_code=409,
                    detail=f"同层同周期已有生效预算（{budget.scope_type}:{budget.scope_id}/{new_period}）",
                )
        budget.period = new_period

    budget.updated_at = now_iso
    budget.updated_by = actor
    _audit(
        db,
        budget_id=budget.id,
        scope_type=budget.scope_type,
        scope_id=budget.scope_id,
        action="update",
        old_json=old_json,
        new_json=_dumps(snapshot_of(budget)),
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("update", str(budget.id))
    return await _to_item(db, budget)


async def set_budget_enabled(
    db: AsyncSession,
    budget_id: int,
    enabled: bool,
    actor: str,
    reason: str,
) -> BudgetItem:
    reason = _require_reason(reason)
    budget = await _require(db, budget_id)
    if enabled:
        existing = await _find_enabled(
            db,
            budget.scope_type,
            budget.scope_id,
            org_id=budget.org_id,
            period=budget.period,
        )
        if existing is not None and existing.id != budget.id:
            raise HTTPException(
                status_code=409,
                detail=f"同层同周期已有生效预算（{budget.scope_type}:{budget.scope_id}/{budget.period}）",
            )
    now_iso = utc_now_iso()
    old_json = _dumps(snapshot_of(budget))
    budget.disabled_at = None if enabled else now_iso
    budget.updated_at = now_iso
    budget.updated_by = actor
    _audit(
        db,
        budget_id=budget.id,
        scope_type=budget.scope_type,
        scope_id=budget.scope_id,
        action="enable" if enabled else "disable",
        old_json=old_json,
        new_json=_dumps(snapshot_of(budget)),
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("enable" if enabled else "disable", str(budget.id))
    return await _to_item(db, budget)


async def delete_budget(db: AsyncSession, budget_id: int, actor: str, reason: str) -> BudgetDeleteResponse:
    """真删。审计行留下（budget_id 走 SET NULL），scope 文本副本保住「删了哪一层」。"""
    reason = _require_reason(reason)
    budget = await _require(db, budget_id)
    now_iso = utc_now_iso()
    _audit(
        db,
        budget_id=budget.id,
        scope_type=budget.scope_type,
        scope_id=budget.scope_id,
        action="delete",
        old_json=_dumps(snapshot_of(budget)),
        new_json=None,
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.flush()
    await db.delete(budget)
    await db.commit()
    _admin_write("delete", str(budget_id))
    return BudgetDeleteResponse(id=budget_id, deleted=True)


async def list_audit(
    db: AsyncSession,
    budget_id: Optional[int] = None,
    limit: int = 100,
) -> BudgetAuditListResponse:
    stmt = select(BudgetAudit).order_by(BudgetAudit.id.desc()).limit(limit)
    if budget_id is not None:
        stmt = stmt.where(BudgetAudit.budget_id == budget_id)
    rows = await db.execute(stmt)
    return BudgetAuditListResponse(
        items=[
            BudgetAuditItem(
                id=a.id,
                budget_id=a.budget_id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                action=a.action,
                old=_loads(a.old_json) if a.old_json else None,
                new=_loads(a.new_json) if a.new_json else None,
                reason=a.reason,
                actor=a.actor,
                created_at=a.created_at,
                request_id=a.request_id,
            )
            for a in rows.scalars().all()
        ]
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _scope_filters(scope_type: str, scope_id: str, org_id: str):
    """命中层 → usage_ledger 过滤。未知 scope 返回 None。"""
    if scope_type == "device":
        return (UsageLedger.device_id == scope_id,)
    if scope_type == "team":
        return (UsageLedger.org_id == org_id, UsageLedger.team_id == scope_id)
    if scope_type == "org":
        return (UsageLedger.org_id == org_id,)
    return None


def _require_reason(reason: str) -> str:
    text = (reason or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="reason 必填（会进审计；空预算变更无法追溯）")
    return text


async def _resolve_scope(db: AsyncSession, scope_type: str, scope_id: str, org_id: str) -> str:
    """校验 scope 存在。调 identity service，不 import identity.model。

    device 行用设备当前所属 org 覆盖客户端乱填，防止把设备预算写到别人的 org 名下。
    """
    if scope_type == "org":
        found = await identity_admin.get_organization(db, org_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"组织 {org_id!r} 不存在")
        if scope_id != org_id:
            raise HTTPException(status_code=422, detail="scope_type=org 时 scope_id 必须等于 org_id")
        return org_id
    if scope_type == "team":
        found = await identity_admin.get_team(db, org_id, scope_id)
        if found is None:
            raise HTTPException(
                status_code=404,
                detail=f"团队 {scope_id!r} 在组织 {org_id!r} 下不存在",
            )
        return org_id
    if scope_type == "device":
        ref = await identity_admin.get_device(db, scope_id)
        if ref is None:
            raise HTTPException(status_code=404, detail=f"设备 {scope_id!r} 不存在")
        return ref.org_id
    raise HTTPException(status_code=422, detail=f"未知 scope_type {scope_type!r}")


async def _find_enabled(
    db: AsyncSession,
    scope_type: str,
    scope_id: str,
    org_id: Optional[str] = None,
    period: Optional[str] = None,
) -> Optional[Budget]:
    stmt = select(Budget).where(
        Budget.scope_type == scope_type,
        Budget.scope_id == scope_id,
        Budget.disabled_at.is_(None),
    )
    if org_id is not None:
        stmt = stmt.where(Budget.org_id == org_id)
    if period is not None:
        stmt = stmt.where(Budget.period == period)
        row = await db.execute(stmt)
        return row.scalar_one_or_none()
    # 同层可以同时有 monthly + daily（唯一约束带 period）。下发只给一份，
    # 按约束从紧到松取：session > daily > weekly > monthly。
    stmt = stmt.order_by(
        case(
            (Budget.period == "session", 0),
            (Budget.period == "daily", 1),
            (Budget.period == "weekly", 2),
            else_=3,
        ),
        Budget.id.desc(),
    ).limit(1)
    row = await db.execute(stmt)
    return row.scalar_one_or_none()


async def _require(db: AsyncSession, budget_id: int) -> Budget:
    row = await db.execute(select(Budget).where(Budget.id == budget_id))
    budget = row.scalar_one_or_none()
    if budget is None:
        raise HTTPException(status_code=404, detail=f"budget {budget_id} 不存在")
    return budget


def _admin_write(action: str, target_id: str) -> None:
    """管理台写操作完成一条。actor 从上下文取，与审计行同一值（方案 §3.6、§3.9）。

    reason 不进日志，它在审计表里。提交失败时到不了这里，那一条归兜底。
    """
    logger.info(
        "admin write",
        event="admin_write",
        action=action,
        target_id=target_id,
        actor=current_actor(),
    )


def _audit(
    db: AsyncSession,
    *,
    budget_id: Optional[int],
    scope_type: str,
    scope_id: str,
    action: str,
    old_json: Optional[str],
    new_json: Optional[str],
    reason: str,
    actor: str,
    now_iso: str,
) -> None:
    db.add(
        BudgetAudit(
            budget_id=budget_id,
            scope_type=scope_type,
            scope_id=scope_id,
            action=action,
            old_json=old_json,
            new_json=new_json,
            reason=reason,
            actor=actor or "",
            created_at=now_iso,
            # 取不到就留空。不为没有请求的写入编造编号（方案 §3.9）。
            request_id=current_request_id(),
        )
    )


async def _to_item(db: AsyncSession, budget: Budget) -> BudgetItem:
    used = await sum_used_usd(
        db,
        scope_type=budget.scope_type,
        scope_id=budget.scope_id,
        org_id=budget.org_id,
        period=budget.period,
    )
    return BudgetItem(
        id=budget.id,
        scope_type=budget.scope_type,
        scope_id=budget.scope_id,
        org_id=budget.org_id,
        period=budget.period,
        limit_usd=budget.limit_usd,
        enforcement=budget.enforcement,
        used_usd=used,
        disabled=budget.disabled_at is not None,
        created_at=budget.created_at,
        updated_at=budget.updated_at,
        updated_by=budget.updated_by,
    )
