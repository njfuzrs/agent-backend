"""管理台 bridge API。走 cookie 会话（`require_web_session`），不走设备凭据。

路径在 /api/v1/bridge/sessions/**，**不在 /ctl/ 下**——签发是设备凭据的事，
列表、签发控制端 token、强制断开是人的事。两条链不能混。

现有门禁 ② 按路径含 `/ctl/` 筛，扫不到这里。漏挂 cookie 等于匿名签发
controller token，能直接遥控别人的机器。`test_bridge_admin_requires_web_session`
盯的就是这一点。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.db import get_db
from app.modules.bridge.schemas import DisconnectRequest, SessionIssued, SessionListResponse
from app.modules.bridge.service import sessions as session_service

router = APIRouter(
    prefix="/bridge/sessions",
    tags=["bridge-admin"],
    dependencies=[Depends(require_web_session)],
)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    state: Optional[str] = Query(default=None),
    org_id: Optional[str] = Query(default=None),
    device_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """在线 / 等待 / 已结束。不含 token，不含 hash。

    state / org_id / device_id 只收窄列表。响应里的 counts 始终是全集，
    页首三个数字不跟着下拉框变。
    """
    return await session_service.list_sessions(
        db, state=state, org_id=org_id, device_id=device_id
    )


@router.post("/{session_id}/controller-token", response_model=SessionIssued, status_code=201)
async def controller_token(
    session_id: str,
    actor: str = Depends(require_web_session),
    db: AsyncSession = Depends(get_db),
):
    """签发控制端 token。明文只此一次；同一 session 的旧控制端 token 立即作废。"""
    return await session_service.issue_controller_token(db, session_id, actor)


@router.post("/{session_id}/disconnect", status_code=204)
async def disconnect(
    session_id: str,
    payload: DisconnectRequest,
    actor: str = Depends(require_web_session),
    db: AsyncSession = Depends(get_db),
):
    """强制断开。reason 必填，进审计。连接由 sidecar 在轮询到后关闭。"""
    await session_service.disconnect_session(db, session_id, actor, payload.reason)
