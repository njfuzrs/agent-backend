"""POST /api/v1/ctl/bridge/sessions —— CLI 侧签发，挂 `require_device`。

路径在 `/ctl/` 下，门禁 ② 会扫到它。仍另有 `test_bridge_session_create_requires_device`
专门盯：失败信息要说人话，不能只报「某个 /ctl/ 端点没挂依赖」。

失败语义（契约 §4）：签发是授予信任，fail-closed。
    无凭据 / 坏凭据 / 吊销 / 过期 → 401。禁止 X-Upload-Token 当鉴权。
    同组织在线 session 达到上限 → 429。

`device_id` / `org_id` / `team_id` 一律从 `DeviceContext` 取，忽略 body 里的
任何同名字段。body 只有两个可选的展示字段。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.db import get_db
from app.modules.bridge.schemas import SessionCreate, SessionIssued, SessionWhoami
from app.modules.bridge.service import sessions as session_service

router = APIRouter(prefix="/ctl/bridge", tags=["bridge-serve"])


@router.post("/sessions", response_model=SessionIssued, status_code=201)
async def create_session(
    payload: SessionCreate | None = None,
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    """创建 session。响应里的 session_token 只此一次，服务端不留明文。

    body 可省略。身份在凭据里，元数据只是给管理台列表看的装饰。
    """
    meta = payload or SessionCreate()
    return await session_service.issue_cli_session(
        db, ctx, ver=meta.ver, cwd_basename=meta.cwd_basename
    )


@router.get("/sessions/whoami", response_model=SessionWhoami)
async def session_whoami(
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    """当前设备有没有未过期的 session。调试用，不返回 token。"""
    return await session_service.whoami(db, ctx)
