"""GET /api/v1/ctl/whoami —— 挂 require_device，用来自证凭据有效。

这是第一个真正的控制面读端点：无凭据必须 401（不再是 501）。
"""

from fastapi import APIRouter, Depends

from app.core.auth.control_plane import DeviceContext, require_device
from app.modules.identity.schemas import WhoAmIResponse

router = APIRouter(prefix="/ctl", tags=["control-plane"])


@router.get("/whoami", response_model=WhoAmIResponse)
async def whoami(ctx: DeviceContext = Depends(require_device)):
    return WhoAmIResponse(
        device_id=ctx.device_id,
        org_id=ctx.org_id,
        team_id=ctx.team_id,
        user_id=ctx.user_id,
    )
