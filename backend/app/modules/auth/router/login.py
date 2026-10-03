"""管理台登录：飞书 OAuth（主入口）+ 共享口令（应急入口）。

本文件的端点都是**免鉴权入口**（还没登录才来这里），由 tests/test_boundaries.py
的 AUTH_PUBLIC_ROUTES 逐条登记。路径在 /api/v1/auth/**，不在 /ctl/ 下：
浏览器回调不是给客户端下发策略的控制面。

飞书 web 流程（方案 §5.7）：

    GET /auth/feishu/start?redirect=/devices
        → 建 state（库里只存 hash）+ 下发 HttpOnly nonce cookie（10 分钟）
        → 302 到飞书授权页（PKCE S256）
    GET /auth/feishu/callback?code=&state=        （或 ?error=access_denied&state=）
        → 校验 state 未用、未过期、与 nonce cookie 匹配 → 原子消费
        → 立即用 code + verifier 换 token → 取 user_info → 校验租户
        → upsert 用户（新人是 member）→ 被吊销则拒绝 → 签发会话 → 302 回管理台

失败一律 302 回 /login?error=<枚举>，不签发会话。错误枚举见 LOGIN_ERRORS。

callback 与 CLI 登录（router/cli.py）共用：state.kind=cli 时不签会话，改签一次性登录码
并 302 回 http://127.0.0.1:<port>/callback；state 通过之后的失败也回 CLI（?error=<同一枚举>）。
"""

from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import (
    KIND_USER,
    SessionPrincipal,
    clear_session,
    issue_password_session,
    issue_user_session,
    read_session,
    require_session,
    verify_credentials,
)
from app.core.config import settings
from app.core.db import get_db
from app.core.logging import bind_context, get_logger
from app.core.timeutil import utc_now_iso
from app.modules.auth.model import STATUS_ACTIVE
from app.modules.auth.schemas import LoginOptions, LoginRequest, MeResponse
from app.modules.auth.service import feishu, states
from app.modules.auth.service import users as users_service

auth_logger = get_logger("agent.auth")
logger = get_logger("agent.auth.login")

router = APIRouter(prefix="/auth", tags=["auth"])

# 回到登录页时带的 ?error=。前端按它显示提示文案，枚举外的值一律当未知错误。
LOGIN_ERRORS = (
    "access_denied",  # 用户在飞书授权页点了拒绝
    "invalid_state",  # state 缺失 / 过期 / 已用过 / 与浏览器 nonce 不匹配
    "feishu_failed",  # 换 token 或取 user_info 失败
    "tenant_mismatch",  # 不是 FEISHU_TENANT_KEY 指定的租户
    "revoked",  # 用户已被管理员吊销
)


def set_nonce_cookie(resp: Response, nonce: str) -> None:
    """web 与 CLI 两条流程共用。CLI 打开的浏览器同样要绑定 state，防登录 CSRF。"""
    resp.set_cookie(
        key=states.NONCE_COOKIE,
        value=nonce,
        max_age=states.STATE_TTL_MINUTES * 60,
        httponly=True,
        # Lax：从飞书授权页回跳是顶层 GET 导航，会带上 cookie；跨站 POST 不带。
        samesite="lax",
        secure=settings.data_plane.SESSION_COOKIE_SECURE,
        path="/",
    )


def _cli_page(auth_state, *, error: str = "", reason: str = "", code: str = "") -> RedirectResponse:
    """回跳 CLI 本地回调。主机写死 127.0.0.1，端口来自 state 行（start 时已校验）。

    失败也回 CLI（带 ?error=），让终端立刻知道结果，而不是干等到超时。
    """
    params = {"state": auth_state.cli_state or ""}
    if code:
        params["code"] = code
    else:
        params["error"] = error
        auth_logger.warning("credential rejected", event="auth_rejected", reason=reason)
    resp = RedirectResponse(f"{states.cli_redirect_url(auth_state.cli_port)}?{urlencode(params)}", status_code=302)
    resp.delete_cookie(states.NONCE_COOKIE, path="/")
    return resp


def _login_page(error: str) -> RedirectResponse:
    url = f"{settings.login.ui_base_url}/login?{urlencode({'error': error})}"
    resp = RedirectResponse(url, status_code=302)
    resp.delete_cookie(states.NONCE_COOKIE, path="/")
    return resp


def _reject(reason: str, error: str) -> RedirectResponse:
    # 只记枚举，不记 code / state / 飞书响应。
    auth_logger.warning("credential rejected", event="auth_rejected", reason=reason)
    return _login_page(error)


@router.get("/options", response_model=LoginOptions)
async def login_options():
    """登录页据此决定显示「飞书登录」还是口令表单。不泄露任何配置值。"""
    return LoginOptions(
        feishu_enabled=settings.login.feishu_enabled,
        password_enabled=settings.login.AUTH_PASSWORD_LOGIN_ENABLED,
    )


@router.post("/login", response_model=MeResponse)
async def password_login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """共享口令登录（应急入口）。关闭后返回 404，像端点不存在一样。

    每次成功都写一条 break_glass 审计：飞书上线后，用口令进来就是例外事件。
    """
    if not settings.login.AUTH_PASSWORD_LOGIN_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    if not verify_credentials(payload.username, payload.password):
        # 不区分「用户名错」与「口令错」—— 避免用户名枚举
        auth_logger.warning("credential rejected", event="auth_rejected", reason="password_rejected")
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    issue_password_session(response, payload.username)
    bind_context(auth="session", actor=payload.username)
    users_service.audit(db, event=users_service.EVENT_BREAK_GLASS, actor=payload.username)
    await db.commit()
    return MeResponse(
        username=payload.username, kind="password", role="admin", name=payload.username, is_admin=True
    )


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    principal = await read_session(request)
    clear_session(response)
    if principal is not None and principal.kind == KIND_USER:
        users_service.audit(
            db, event=users_service.EVENT_LOGOUT, actor=principal.actor, user_id=principal.user_id
        )
        await db.commit()
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(principal: SessionPrincipal = Depends(require_session)):
    """前端启动时调它判断「是否已登录、是不是管理员」。member 也能调（返回 is_admin=false）。"""
    return MeResponse(
        username=principal.actor,
        kind=principal.kind,
        role=principal.role,
        name=principal.name,
        is_admin=principal.is_admin,
    )


@router.get("/feishu/start")
async def feishu_start(
    request: Request,
    redirect: str = Query("/", max_length=512),
    db: AsyncSession = Depends(get_db),
):
    if not settings.login.feishu_enabled:
        raise HTTPException(status_code=503, detail="feishu login is not configured")
    # nonce cookie 按主机隔离。从 sid-code.cc 发起、回调落在 www.sid-code.cc 时，
    # 回调带不上 nonce，必然 invalid_state（2026-10-04 线上实测）。
    # 先把浏览器送到 PUBLIC_BASE_URL 的主机上再发起，cookie 与回调才在同一个主机。
    canonical = urlsplit(settings.login.PUBLIC_BASE_URL)
    if canonical.netloc and request.url.netloc != canonical.netloc:
        target = (
            f"{canonical.scheme}://{canonical.netloc}{canonical.path.rstrip('/')}"
            f"/api/v1/auth/feishu/start?{urlencode({'redirect': redirect})}"
        )
        return RedirectResponse(target, status_code=302)
    state, nonce = await states.create_state(db, redirect)
    url = feishu.authorize_url(state, states.pkce_challenge(states.pkce_verifier(state)))
    resp = RedirectResponse(url, status_code=302)
    set_nonce_cookie(resp, nonce)
    return resp


@router.get("/feishu/callback")
async def feishu_callback(
    request: Request,
    code: str = Query("", max_length=1024),
    state: str = Query("", max_length=256),
    error: str = Query("", max_length=64),
    db: AsyncSession = Depends(get_db),
):
    if not settings.login.feishu_enabled:
        raise HTTPException(status_code=503, detail="feishu login is not configured")

    auth_state = await states.consume_state(db, state, request.cookies.get(states.NONCE_COOKIE))
    if auth_state is None:
        # state 不可信，连 CLI 的端口都不可信：一律回管理台登录页。
        return _reject("state_rejected", "invalid_state")
    is_cli = auth_state.kind == states.KIND_CLI

    def fail(reason: str, err: str) -> RedirectResponse:
        return _cli_page(auth_state, error=err, reason=reason) if is_cli else _reject(reason, err)

    if error:
        # 用户拒绝授权不是攻击，但也没有身份可签发。state 已消费，不能重放。
        return fail("feishu_denied", "access_denied")
    if not code:
        return fail("state_rejected", "invalid_state")

    try:
        access_token = await feishu.exchange_code(code, states.pkce_verifier(state))
        info = await feishu.fetch_user_info(access_token)
    except feishu.FeishuError:
        return fail("feishu_failed", "feishu_failed")
    # P1 不保存 token：登录只为拿身份。显式丢掉引用，免得后来人顺手存了。
    del access_token

    expected_tenant = settings.login.FEISHU_TENANT_KEY
    if expected_tenant and info.tenant_key != expected_tenant:
        users_service.audit(
            db, event=users_service.EVENT_LOGIN_REJECTED, actor=f"user:{info.union_id}",
            detail={"reason": "tenant_mismatch"},
        )
        await db.commit()
        return fail("tenant_mismatch", "tenant_mismatch")

    user = await users_service.upsert_feishu_user(db, info)
    if user.status != STATUS_ACTIVE:
        users_service.audit(
            db, event=users_service.EVENT_LOGIN_REJECTED, actor=f"user:{user.union_id}",
            user_id=user.id, detail={"reason": "revoked"},
        )
        await db.commit()
        return fail("user_revoked", "revoked")

    user.last_login_at = utc_now_iso()
    if is_cli:
        # CLI：不签会话 cookie，签 60 秒一次性登录码，回跳 127.0.0.1。
        login_code = await states.create_login_code(
            db, user_id=user.id, device_id=auth_state.device_id, challenge=auth_state.cli_challenge
        )
        users_service.audit(
            db, event=users_service.EVENT_CLI_LOGIN, actor=f"user:{user.union_id}", user_id=user.id,
            detail={"device_id": auth_state.device_id},
        )
        await db.commit()
        logger.info("cli login code issued", event="cli_login_code", target_id=str(user.id))
        return _cli_page(auth_state, code=login_code)

    users_service.audit(
        db, event=users_service.EVENT_LOGIN, actor=f"user:{user.union_id}", user_id=user.id,
        detail={"role": user.role},
    )
    await db.commit()

    bind_context(auth="session", actor=f"user:{user.union_id}")
    logger.info("user login", event="user_login", target_id=str(user.id), outcome=user.role)

    resp = RedirectResponse(settings.login.ui_base_url + auth_state.redirect_to, status_code=302)
    resp.delete_cookie(states.NONCE_COOKIE, path="/")
    issue_user_session(resp, user.id)
    return resp
