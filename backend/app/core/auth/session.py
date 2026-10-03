"""管理台会话（HttpOnly cookie），替代前端把密码明文存 localStorage。

规划 §1.3 ③ / §PR-0.5 第 4 条：`api.ts` 原来把 Basic Auth 密码写进 `localStorage`，
一个 XSS 即可读走管理员凭据。M3 之后管理台能配 policy，这个凭据的价值会从
「能看轨迹」升级为「能给全公司下发策略」—— **在提权之前修，比提权之后修便宜。**

为什么用「无状态签名 token」而不是「服务端 session 表」：
生产是 `uvicorn --workers 2`，进程内存不共享 —— 在 worker A 登录、请求落到 worker B
会直接掉线。签名 token 把状态放在 cookie 里，天然跨 worker。

P1（飞书登录）之后会话有两种主体，cookie 形状 `<subject>.<expiry>.<hmac>`：

    pw:<username>  共享口令登录（应急入口，AUTH_PASSWORD_LOGIN_ENABLED 关掉后立即失效）
    u:<users.id>   飞书登录。**每次请求按主键查一次 users**：吊销或降级下一次请求即生效。
                   管理台流量很小，一次主键查询可以忽略；仍然不需要服务端会话表。

角色：admin 能用全部管理台接口；member 一律 403（登录不等于管理员，方案 §5.7）。
口令会话视同 admin —— 它就是原来那个共享 admin 账号。

cookie 属性：
    HttpOnly  → JS 读不到，XSS 拿不走
    SameSite=Lax → 防 CSRF（跨站 POST 不带 cookie）
    Secure    → 由 SESSION_COOKIE_SECURE 控制；上 TLS 后必须置 true
"""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from app.core import db as db_mod
from app.core.config import settings
from app.core.logging import bind_context, get_logger
from app.modules.auth.model import ROLE_ADMIN, STATUS_ACTIVE, User

auth_logger = get_logger("agent.auth")

COOKIE_NAME = "traj_session"
# 会话有效期：8 小时（一个工作日），到期需重新登录
SESSION_TTL_SECONDS = 8 * 60 * 60

KIND_PASSWORD = "password"
KIND_USER = "user"


@dataclass(frozen=True)
class SessionPrincipal:
    """一次请求的管理台身份。actor 与审计表的 actor 列是同一个值。"""

    kind: str  # password / user
    actor: str  # 口令会话是用户名（admin）；飞书会话是 user:<union_id>
    role: str
    name: str = ""
    user_id: int | None = None
    union_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def signing_key() -> bytes:
    """签名密钥。

    未显式配置 SESSION_SECRET 时，从 AUTH_PASSWORD 派生 —— 这样不必新增必填配置项，
    且**改密码会使全部已发出的会话立即失效**。生产应显式配置 SESSION_SECRET：
    飞书会话不该因为改了应急口令而全部掉线。
    """
    explicit = settings.data_plane.SESSION_SECRET
    if explicit:
        return explicit.encode()
    return hashlib.sha256(
        b"traj-session-v1:" + settings.data_plane.AUTH_PASSWORD.encode()
    ).digest()


def _sign(payload: str) -> str:
    return hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()


def _set_cookie(response: Response, subject: str) -> None:
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{subject}.{expiry}"
    response.set_cookie(
        key=COOKIE_NAME,
        value=f"{payload}.{_sign(payload)}",
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.data_plane.SESSION_COOKIE_SECURE,
        path="/",
    )


def issue_password_session(response: Response, username: str) -> None:
    _set_cookie(response, f"pw:{username}")


def issue_user_session(response: Response, user_id: int) -> None:
    _set_cookie(response, f"u:{user_id}")


def clear_session(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _verified_subject(request: Request) -> str | None:
    """校验签名与过期，返回 subject；无效返回 None。不查库。"""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    parts = raw.rsplit(".", 2)
    if len(parts) != 3:
        return None
    subject, expiry_str, sig = parts
    # compare_digest：避免按字节比较导致的时序侧信道
    if not secrets.compare_digest(sig, _sign(f"{subject}.{expiry_str}")):
        return None
    try:
        if int(expiry_str) < int(time.time()):
            return None
    except ValueError:
        return None
    return subject


async def read_session(request: Request) -> SessionPrincipal | None:
    """解析会话 cookie。无效、过期、口令登录已关闭、用户已吊销 → None。"""
    subject = _verified_subject(request)
    if subject is None:
        return None

    if subject.startswith("pw:"):
        username = subject[3:]
        # 关掉口令登录后，已发出的口令会话一并失效，而不是挂到 8 小时到期。
        if not settings.login.AUTH_PASSWORD_LOGIN_ENABLED:
            return None
        # 用户名也要核 —— 改过 AUTH_USERNAME 后旧会话应失效
        if not secrets.compare_digest(username, settings.data_plane.AUTH_USERNAME):
            return None
        return SessionPrincipal(kind=KIND_PASSWORD, actor=username, role=ROLE_ADMIN, name=username)

    if subject.startswith("u:"):
        try:
            user_id = int(subject[2:])
        except ValueError:
            return None
        async with db_mod.async_session() as db:
            user = await db.get(User, user_id)
        if user is None or user.status != STATUS_ACTIVE:
            return None
        return SessionPrincipal(
            kind=KIND_USER,
            actor=f"user:{user.union_id}",
            role=user.role,
            name=user.name,
            user_id=user.id,
            union_id=user.union_id,
        )

    # P1 之前发的 cookie 没有前缀，一律当无效，重新登录即可。
    return None


def verify_credentials(username: str, password: str) -> bool:
    """核对登录表单提交的用户名口令。"""
    ok_user = secrets.compare_digest(username, settings.data_plane.AUTH_USERNAME)
    ok_pass = secrets.compare_digest(password, settings.data_plane.AUTH_PASSWORD)
    return ok_user and ok_pass


def bind_principal(principal: SessionPrincipal) -> None:
    # 只记种类与 actor，不记 cookie 值。
    bind_context(auth="session", actor=principal.actor)


def reject_non_admin(principal: SessionPrincipal) -> None:
    """member 登录了但不是管理员：403，不是 401（不让前端误以为要重新登录）。"""
    if not principal.is_admin:
        auth_logger.warning("credential rejected", event="auth_rejected", reason="not_admin")
        raise HTTPException(status_code=403, detail="Forbidden")


async def require_session(request: Request) -> SessionPrincipal:
    """任意已登录身份（含 member）。只给 /auth/me、/auth/logout 这类「关于自己」的端点用。"""
    principal = await read_session(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    bind_principal(principal)
    return principal


async def require_web_session(request: Request) -> str:
    """管理台鉴权依赖：只认 cookie 会话，且必须是 admin。返回 actor。

    必须是异步的。FastAPI 把同步依赖丢进线程池，而 contextvar 不跨线程——
    在同步函数里写的 actor，请求回到事件循环后就读不到了，admin_write 的
    actor 会是空的，和审计行对不上（方案 §3.9）。
    """
    principal = await read_session(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    # 管理台路由只挂本依赖，不走 verify_basic_auth，所以 actor 在这里写。
    bind_principal(principal)
    reject_non_admin(principal)
    return principal.actor


async def require_admin_principal(request: Request) -> SessionPrincipal:
    """同 require_web_session，但返回完整身份。用户管理要知道「是不是在改自己」。"""
    await require_web_session(request)
    principal = await read_session(request)
    assert principal is not None  # 上一步已校验
    return principal
