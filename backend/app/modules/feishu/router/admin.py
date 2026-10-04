"""管理台：飞书调用审计 / 授权状态。cookie 会话（admin），只读。

不在 /ctl/ 下。审计只追加，管理台没有删除口。
"""

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.config import settings
from app.core.db import get_db
from app.modules.feishu.schemas import (
    FeishuCallItem,
    FeishuCallListResponse,
    FeishuGrantItem,
    FeishuGrantListResponse,
)
from app.modules.feishu.service import queries, tokens

router = APIRouter(
    prefix="/feishu",
    tags=["feishu-admin"],
    dependencies=[Depends(require_web_session)],
)


@router.get("/calls", response_model=FeishuCallListResponse)
async def list_calls(
    user_id: Optional[int] = None,
    device_id: Optional[str] = None,
    outcome: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = await queries.list_calls(
        db, user_id=user_id, device_id=device_id, outcome=outcome, since=since, limit=limit
    )
    return FeishuCallListResponse(
        items=[
            FeishuCallItem(
                id=r.id, created_at=r.created_at, user_id=r.user_id, device_id=r.device_id, tool=r.tool,
                target_token=r.target_token, outcome=r.outcome, error_code=r.error_code,
                latency_ms=r.latency_ms, request_id=r.request_id,
            )
            for r in rows
        ]
    )


@router.get("/grants", response_model=FeishuGrantListResponse)
async def list_grants(db: AsyncSession = Depends(get_db)):
    items = [FeishuGrantItem(**asdict(g)) for g in await tokens.grant_status(db)]
    return FeishuGrantListResponse(enabled=settings.login.delegation_enabled, items=items)
