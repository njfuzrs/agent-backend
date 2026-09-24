"""账本列表 + 一个固定聚合（by-scope）。

形状见契约 §4 / §5。**不接受参数化扩展**：只做这一个视图 + 下钻列表。加第二个
聚合端点等于把本模块做成通用 BI，那是主规划明确不做的。

by-scope 响应不得返回单价字段。cost_usd 与 prompt_total 口径不同源
（前者含影子、后者不含），相除是错数。门禁扫 schema。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cost.model import UsageLedger
from app.modules.cost.schemas import (
    UsageByScopeItem,
    UsageByScopeResponse,
    UsageLedgerItem,
    UsageLedgerListResponse,
)
from app.modules.cost.service.guard import current_period_key, period_bounds, validate_period


def _to_item(row: UsageLedger) -> UsageLedgerItem:
    return UsageLedgerItem(
        id=row.id,
        device_id=row.device_id,
        org_id=row.org_id,
        team_id=row.team_id or "",
        session_id=row.session_id,
        ts=row.ts,
        received_at=row.received_at,
        model=row.model,
        provider=row.provider,
        prompt_total=row.prompt_total or 0,
        cache_hit=row.cache_hit or 0,
        cache_write=row.cache_write or 0,
        uncached_input=row.uncached_input or 0,
        output=row.output or 0,
        cost_usd=row.cost_usd,
        savings_usd=row.savings_usd or 0,
        duration_ms=row.duration_ms or 0,
        side_input_tokens=row.side_input_tokens,
        side_output_tokens=row.side_output_tokens,
        side_cost_usd=row.side_cost_usd,
        endpoint_host=row.endpoint_host,
        app_version=row.app_version,
        peak_ratio=row.peak_ratio,
    )


async def list_ledger(
    db: AsyncSession,
    *,
    org_id: Optional[str] = None,
    device_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> UsageLedgerListResponse:
    """按 received_at 倒序。limit 默认 100，上限 500（路由层钳）。"""
    filters = []
    if org_id:
        filters.append(UsageLedger.org_id == org_id)
    if device_id:
        filters.append(UsageLedger.device_id == device_id)
    if since:
        filters.append(UsageLedger.received_at >= since)

    count_stmt = select(func.count(UsageLedger.id))
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = int((await db.execute(count_stmt)).scalar_one())

    stmt = select(UsageLedger).order_by(UsageLedger.received_at.desc()).limit(limit)
    if filters:
        stmt = stmt.where(*filters)
    rows = await db.execute(stmt)
    return UsageLedgerListResponse(
        total=total,
        items=[_to_item(r) for r in rows.scalars().all()],
    )


async def by_scope(
    db: AsyncSession,
    *,
    period: str = "monthly",
    period_key: Optional[str] = None,
    org_id: Optional[str] = None,
) -> UsageByScopeResponse:
    """按 device 一行：谁在烧钱。period 缺省 monthly；period_key 缺省当前 UTC 周期。

    session 周期对 by-scope 没有产品意义（管理台默认 monthly），仍按「每个 device
    最新一行」聚合，避免 SUM 把历史会话加起来。
    """
    period = validate_period(period)
    key = period_key or current_period_key(period)

    sessions = func.count(UsageLedger.id)
    cost = func.coalesce(func.sum(UsageLedger.cost_usd), 0.0)
    side = func.sum(UsageLedger.side_cost_usd)
    prompt = func.coalesce(func.sum(UsageLedger.prompt_total), 0)
    cache = func.coalesce(func.sum(UsageLedger.cache_hit), 0)
    output = func.coalesce(func.sum(UsageLedger.output), 0)
    last_at = func.max(UsageLedger.received_at)

    stmt = select(
        UsageLedger.device_id,
        UsageLedger.org_id,
        UsageLedger.team_id,
        sessions,
        cost,
        side,
        prompt,
        cache,
        output,
        last_at,
    ).group_by(UsageLedger.device_id, UsageLedger.org_id, UsageLedger.team_id)

    if org_id:
        stmt = stmt.where(UsageLedger.org_id == org_id)

    if period == "session":
        # 每个 device 最新一行。用窗口不好在 SQLite 上写；改成子查询取 max(received_at)。
        latest = (
            select(
                UsageLedger.device_id.label("device_id"),
                func.max(UsageLedger.received_at).label("last_at"),
            ).group_by(UsageLedger.device_id)
        )
        if org_id:
            latest = latest.where(UsageLedger.org_id == org_id)
        latest = latest.subquery()
        stmt = stmt.join(
            latest,
            (UsageLedger.device_id == latest.c.device_id)
            & (UsageLedger.received_at == latest.c.last_at),
        )
    else:
        start, end = period_bounds(period, key)
        stmt = stmt.where(UsageLedger.ts >= start, UsageLedger.ts < end)

    stmt = stmt.order_by(cost.desc())
    rows = await db.execute(stmt)
    items = [
        UsageByScopeItem(
            device_id=r[0],
            org_id=r[1] or "",
            team_id=r[2] or "",
            sessions=int(r[3] or 0),
            cost_usd=float(r[4] or 0),
            side_cost_usd=None if r[5] is None else float(r[5]),
            prompt_total=int(r[6] or 0),
            cache_hit=int(r[7] or 0),
            output=int(r[8] or 0),
            last_received_at=r[9],
        )
        for r in rows.all()
    ]
    return UsageByScopeResponse(period=period, period_key=key, items=items)
