"""CLI 飞书登录（P2，方案 §5.2）：sid-code auth login / logout。

    GET  /auth/feishu/cli/start?port=&challenge=&cli_state=&device_id=
        → 建 state（kind=cli，记下端口 / CLI challenge / cli_state / device_id）
        → 与 web 流程相同：nonce cookie + 302 到飞书
    GET  /auth/feishu/callback   （与 web 共用，见 login.py，按 state.kind 分流）
        → kind=cli：签发 60 秒一次性登录码 → 302 http://127.0.0.1:<port>/callback?code=&state=<cli_state>
    POST /auth/cli/exchange {code, verifier, device_id, platform, ver}
        → 校验登录码、S256(verifier)==challenge、device_id 一致
        → 设备已绑给另一个在职用户 → 409；否则绑定并签发设备凭据
    POST /auth/cli/logout       （挂 require_device）
        → 吊销本设备凭据并解绑，换人登录同一台电脑不再 409

start 与 exchange 是免鉴权入口（还没有凭据才来），登记在 AUTH_PUBLIC_ROUTES。
不在 /ctl/ 下：这是签发身份的入口，不是给客户端下发策略的控制面。
"""

from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.config import settings
from app.core.db import get_db
from app.core.logging import bind_context, get_logger
from app.modules.auth.model import STATUS_ACTIVE
from app.modules.auth.router.login import set_nonce_cookie
from app.modules.auth.schemas import CliExchangeRequest, CliExchangeResponse, CliUser
from app.modules.auth.service import feishu, states
from app.modules.auth.service import users as users_service
from app.modules.identity.service import login as device_login

auth_logger = get_logger("agent.auth")
logger = get_logger("agent.auth.cli")

router = APIRouter(prefix="/auth", tags=["auth"])


def _reject_exchange() -> None:
    # 码不存在 / 过期 / 用过 / verifier 错 / device_id 错，对外与日志都不区分。
    auth_logger.warning("credential rejected", event="auth_rejected", reason="cli_code_rejected")
    raise HTTPException(status_code=401, detail="Invalid login code")


@router.get("/feishu/cli/start")
async def feishu_cli_start(
    request: Request,
    port: int = Query(...),
    challenge: str = Query(..., max_length=64),
    cli_state: str = Query(..., max_length=128),
    device_id: str = Query(..., max_length=128),
    db: AsyncSession = Depends(get_db),
):
    if not settings.login.feishu_enabled:
        raise HTTPException(status_code=503, detail="feishu login is not configured")
    cli = states.validate_cli_params(port, challenge, cli_state, device_id)
    if cli is None:
        # 参数不可信时没有地方可回跳，只能 400。
        raise HTTPException(status_code=400, detail="invalid cli login parameters")
    # 与 web 流程同理：nonce cookie 按主机隔离，先到 PUBLIC_BASE_URL 的主机再建 state。
    canonical = urlsplit(settings.login.PUBLIC_BASE_URL)
    if canonical.netloc and request.url.netloc != canonical.netloc:
        query = urlencode(
            {"port": cli.port, "challenge": cli.challenge, "cli_state": cli.cli_state, "device_id": cli.device_id}
        )
        target = (
            f"{canonical.scheme}://{canonical.netloc}{canonical.path.rstrip('/')}"
            f"/api/v1/auth/feishu/cli/start?{query}"
        )
        return RedirectResponse(target, status_code=302)
    state, nonce = await states.create_state(db, "/", cli=cli)
    url = feishu.authorize_url(state, states.pkce_challenge(states.pkce_verifier(state)))
    resp = RedirectResponse(url, status_code=302)
    set_nonce_cookie(resp, nonce)
    return resp


@router.post("/cli/exchange", response_model=CliExchangeResponse)
async def cli_exchange(payload: CliExchangeRequest, db: AsyncSession = Depends(get_db)):
    if not settings.login.feishu_enabled:
        raise HTTPException(status_code=503, detail="feishu login is not configured")

    code = await states.consume_login_code(db, payload.code, payload.verifier, payload.device_id)
    if code is None:
        _reject_exchange()

    user = await users_service.get_user(db, code.user_id)
    if user is None or user.status != STATUS_ACTIVE:
        # 回调到兑换之间（60 秒内）被吊销。码已消费，提交掉。
        await db.commit()
        _reject_exchange()

    # device_id 是客户端自报的：已绑给另一个在职用户就拒绝，不重绑、不吊销对方凭据。
    # 否则知道别人的 device_id 就能把人踢下线。绑给已吊销的人则允许接手。
    owner_id = await device_login.device_owner(db, payload.device_id)
    if owner_id is not None and owner_id != user.id:
        owner = await users_service.get_user(db, owner_id)
        if owner is not None and owner.status == STATUS_ACTIVE:
            users_service.audit(
                db, event=users_service.EVENT_CLI_CONFLICT, actor=f"user:{user.union_id}",
                user_id=user.id, detail={"device_id": payload.device_id, "owner_id": owner_id},
            )
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail="device is bound to another user; run `sid-code auth logout` on it first",
            )

    issued = await device_login.issue_login_credential(
        db, device_id=payload.device_id, user_ref=user.id, platform=payload.platform, ver=payload.ver
    )
    users_service.audit(
        db, event=users_service.EVENT_CLI_EXCHANGE, actor=f"user:{user.union_id}",
        user_id=user.id, detail={"device_id": payload.device_id},
    )
    await db.commit()

    bind_context(auth="cli_login", actor=f"user:{user.union_id}", device_id=payload.device_id)
    # 不记登录码、不记凭据明文。
    logger.info("cli login", event="cli_login", target_id=str(user.id), device_id=payload.device_id)
    return CliExchangeResponse(
        credential=issued.credential,
        expires_at=issued.expires_at,
        device_id=issued.device_id,
        org_id=issued.org_id,
        team_id=issued.team_id,
        user=CliUser(id=user.id, name=user.name, union_id=user.union_id),
    )


@router.post("/cli/logout")
async def cli_logout(ctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """吊销本设备凭据并解绑。凭据本身就是鉴权，用完即作废。"""
    previous = await device_login.logout_device(db, ctx.device_id)
    if previous is not None:
        user = await users_service.get_user(db, previous)
        users_service.audit(
            db, event=users_service.EVENT_CLI_LOGOUT,
            actor=f"user:{user.union_id}" if user is not None else f"device:{ctx.device_id}",
            user_id=previous, detail={"device_id": ctx.device_id},
        )
    await db.commit()
    return {"ok": True}
