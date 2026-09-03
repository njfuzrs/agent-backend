"""管理台会话（HttpOnly cookie），替代前端把密码明文存 localStorage。

规划 §1.3 ③ / §PR-0.5 第 4 条：`api.ts` 原来把 Basic Auth 密码写进 `localStorage`，
一个 XSS 即可读走管理员凭据。M3 之后管理台能配 policy，这个凭据的价值会从
「能看轨迹」升级为「能给全公司下发策略」—— **在提权之前修，比提权之后修便宜。**

为什么用「无状态签名 token」而不是「服务端 session 表」：
生产是 `uvicorn --workers 2`，进程内存不共享 —— 在 worker A 登录、请求落到 worker B
会直接掉线。签名 token 把状态放在 cookie 里，天然跨 worker，且不需要新表
（M0 的纪律是零新表）。

cookie 属性：
    HttpOnly  → JS 读不到，XSS 拿不走
    SameSite=Lax → 防 CSRF（跨站 POST 不带 cookie）
    Secure    → 由 SESSION_COOKIE_SECURE 控制；上 TLS 后必须置 true
"""

import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, Response

from app.core.config import settings

COOKIE_NAME = "traj_session"
# 会话有效期：8 小时（一个工作日），到期需重新登录
SESSION_TTL_SECONDS = 8 * 60 * 60


def _signing_key() -> bytes:
    """签名密钥。

    未显式配置 SESSION_SECRET 时，从 AUTH_PASSWORD 派生 —— 这样不必新增必填配置项，
    且**改密码会使全部已发出的会话立即失效**，这正是想要的语义。
    """
    explicit = settings.data_plane.SESSION_SECRET
    if explicit:
        return explicit.encode()
    return hashlib.sha256(
        b"traj-session-v1:" + settings.data_plane.AUTH_PASSWORD.encode()
    ).digest()


def issue_session(response: Response, username: str) -> None:
    """签发会话 cookie。token 形状：<username>.<expiry>.<hmac>"""
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{username}.{expiry}"
    sig = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    response.set_cookie(
        key=COOKIE_NAME,
        value=f"{payload}.{sig}",
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.data_plane.SESSION_COOKIE_SECURE,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def read_session(request: Request) -> str | None:
    """校验 cookie，返回用户名；无效/过期返回 None。"""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    parts = raw.rsplit(".", 2)
    if len(parts) != 3:
        return None
    username, expiry_str, sig = parts

    expected = hmac.new(
        _signing_key(), f"{username}.{expiry_str}".encode(), hashlib.sha256
    ).hexdigest()
    # compare_digest：避免按字节比较导致的时序侧信道
    if not secrets.compare_digest(sig, expected):
        return None

    try:
        if int(expiry_str) < int(time.time()):
            return None
    except ValueError:
        return None

    # 用户名也要核 —— 改过 AUTH_USERNAME 后旧会话应失效
    if not secrets.compare_digest(username, settings.data_plane.AUTH_USERNAME):
        return None
    return username


def verify_credentials(username: str, password: str) -> bool:
    """核对登录表单提交的用户名口令。"""
    ok_user = secrets.compare_digest(username, settings.data_plane.AUTH_USERNAME)
    ok_pass = secrets.compare_digest(password, settings.data_plane.AUTH_PASSWORD)
    return ok_user and ok_pass


def require_web_session(request: Request) -> str:
    """管理台鉴权依赖：只认 cookie 会话。用于 /api/v1/auth/me。"""
    username = read_session(request)
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return username
