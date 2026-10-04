"""管理台只读查询：飞书调用审计。"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.feishu.model import FeishuCallAudit


async def list_calls(
    db: AsyncSession,
    *,
    user_id: Optional[int],
    device_id: Optional[str],
    outcome: Optional[str],
    since: Optional[str],
    limit: int,
) -> list[FeishuCallAudit]:
    q = select(FeishuCallAudit).order_by(FeishuCallAudit.id.desc()).limit(limit)
    if user_id is not None:
        q = q.where(FeishuCallAudit.user_id == user_id)
    if device_id:
        q = q.where(FeishuCallAudit.device_id == device_id)
    if outcome:
        q = q.where(FeishuCallAudit.outcome == outcome)
    if since:
        q = q.where(FeishuCallAudit.created_at >= since)
    rows = await db.execute(q)
    return list(rows.scalars())
