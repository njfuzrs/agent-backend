"""管理台 event API。走 cookie 会话（`require_web_session`），不走设备凭据。

路径在 /api/v1/events/**，**不在 /ctl/** —— 上报是 POST /events（设备凭据），
查询是同前缀不同方法/鉴权。边界测试必须能区分这两条链，见
`test_events_read_requires_web_session` / `test_events_ingest_requires_device`。

管理台对 events **只读**。events 表没有 UPDATE / DELETE 路径（服务端设计 §2）：
审计表能删就不是审计表。`test_events_has_no_write_endpoints_besides_ingest`
盯的就是这一点 —— 给本路由加 PUT/PATCH/DELETE 那条会红。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.db import get_db
from app.modules.event.schemas import (
    EventListResponse,
    EventRejectListResponse,
    PolicyAuditResponse,
    SessionCoverageResponse,
)
from app.modules.event.service import queries as query_service

router = APIRouter(
    prefix="/events",
    tags=["events-admin"],
    dependencies=[Depends(require_web_session)],
)


@router.get("", response_model=EventListResponse)
async def list_events(
    session_id: Optional[str] = None,
    device_id: Optional[str] = None,
    event_name: Optional[str] = None,
    org_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """下钻列表。只做契约 §8 那几个维度，metadata 内部字段筛选是 BI，不做。"""
    return await query_service.list_events(
        db,
        session_id=session_id,
        device_id=device_id,
        event_name=event_name,
        org_id=org_id,
        since=since,
        limit=limit,
    )


@router.get("/rejects", response_model=EventRejectListResponse)
async def list_rejects(db: AsyncSession = Depends(get_db)):
    """被拒事件名计数。管理台首屏那个 `rejected` 数从这里来。"""
    return await query_service.list_rejects(db)


@router.get("/stats/session-coverage", response_model=SessionCoverageResponse)
async def session_coverage(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """每个会话的事件条数 / 三类新事件 / 是否有对应 trajectory 行。

    第三类「有轨迹但零事件」是「事件通道没接上」的唯一信号，不能漏。
    """
    return await query_service.session_coverage(db, limit=limit)


@router.get("/stats/policy-audit", response_model=PolicyAuditResponse)
async def policy_audit(
    since: Optional[str] = None,
    org_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """按 device 聚合：策略是否真的在拦东西。默认窗口 7 天（Text 列 json.loads 的代价）。"""
    return await query_service.policy_audit(db, since=since, org_id=org_id)
