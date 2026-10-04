"""飞书 OAuth 客户端：授权页地址、授权码换 token、刷新 token、取用户信息。

只做 HTTP，不碰库。路由层通过模块属性调用（`feishu.exchange_code(...)`），
测试替换这两个函数即可，不用起假的飞书。

三条纪律：
- app_secret 只在请求体里出现，不进日志、不进异常消息。
- 飞书的响应体不原样进日志（rag-service 那行 `logger.info(f"... {result}")` 把 token 打进了日志）。
  失败只带飞书的错误码。
- token 只以 FeishuTokenSet 的形式交给调用方，由 feishu 模块加密落库（P4）；
  没配 TOKEN_ENC_KEY 时登录用完即弃。FeishuTokenSet 的 repr 不含 token 明文。
"""

from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from app.core.config import settings

# 授权页用文档写法的 accounts 域名（方案 §4）。token 与 user_info 在 open 域名下。
AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"

TIMEOUT_SECONDS = 10.0


class FeishuError(Exception):
    """飞书返回非 0 或网络失败。消息里只有固定短句与飞书错误码，不含响应体。"""

    def __init__(self, stage: str, code: str):
        super().__init__(f"feishu {stage} failed: {code}")
        self.stage = stage
        self.code = code


# 飞书刷新接口的服务端错误（官方建议重试）。其余非 0 码一律视为 refresh_token 已不可用。
TRANSIENT_CODES = frozenset({"network", "20050"})


@dataclass(frozen=True)
class FeishuTokenSet:
    """一次换 token / 刷新的结果。时长以飞书响应为准，不硬编码（方案 §4）。

    repr=False：这个对象一旦被误打进日志或异常消息，也只剩字段名。
    """

    access_token: str = field(repr=False)
    expires_in: int
    refresh_token: str = field(default="", repr=False)
    refresh_expires_in: int = 0
    scope: str = ""


@dataclass(frozen=True)
class FeishuUser:
    tenant_key: str
    union_id: str
    open_id: str
    name: str
    email: str


def redirect_uri() -> str:
    """飞书回调地址。必须与开发者后台「重定向 URL」逐字一致，否则授权页报错。"""
    return settings.login.PUBLIC_BASE_URL.rstrip("/") + "/api/v1/auth/feishu/callback"


def authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.login.FEISHU_APP_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    scope = settings.login.FEISHU_LOGIN_SCOPE.strip()
    if scope:
        params["scope"] = scope
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str) -> FeishuTokenSet:
    """授权码换 user_access_token。授权码 5 分钟有效、只能用一次，回调里立刻换。"""
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.login.FEISHU_APP_ID,
        "client_secret": settings.login.FEISHU_APP_SECRET,
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": code_verifier,
    }
    return await _token_request("token", payload)


async def refresh_user_token(refresh_token: str) -> FeishuTokenSet:
    """refresh_token 换新的一对 token。

    飞书的 refresh_token 一次性：成功后旧的立即作废，调用方必须在同一把锁里把新的落库
    （方案 §6.4）。失败抛 FeishuError(stage="refresh")，code 在 TRANSIENT_CODES 里的可重试。
    """
    payload = {
        "grant_type": "refresh_token",
        "client_id": settings.login.FEISHU_APP_ID,
        "client_secret": settings.login.FEISHU_APP_SECRET,
        "refresh_token": refresh_token,
    }
    return await _token_request("refresh", payload)


async def _token_request(stage: str, payload: dict) -> FeishuTokenSet:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(TOKEN_URL, json=payload)
            body = resp.json()
    except (httpx.HTTPError, ValueError):
        raise FeishuError(stage, "network") from None
    if not isinstance(body, dict) or body.get("code") not in (0, None):
        raise FeishuError(stage, str(body.get("code") if isinstance(body, dict) else "bad_body"))
    # v2 端点直接返回 token 字段，没有 data 包装
    access = body.get("access_token")
    if not access:
        raise FeishuError(stage, "no_access_token")
    return FeishuTokenSet(
        access_token=access,
        expires_in=_as_int(body.get("expires_in")),
        refresh_token=body.get("refresh_token") or "",
        refresh_expires_in=_as_int(body.get("refresh_token_expires_in")),
        scope=body.get("scope") or "",
    )


def _as_int(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


async def fetch_user_info(access_token: str) -> FeishuUser:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.get(
                USER_INFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            body = resp.json()
    except (httpx.HTTPError, ValueError):
        raise FeishuError("user_info", "network") from None
    if not isinstance(body, dict) or body.get("code") != 0:
        raise FeishuError("user_info", str(body.get("code") if isinstance(body, dict) else "bad_body"))
    data = body.get("data") or {}
    union_id = data.get("union_id") or ""
    tenant_key = data.get("tenant_key") or ""
    if not union_id or not tenant_key:
        raise FeishuError("user_info", "missing_identity")
    return FeishuUser(
        tenant_key=tenant_key,
        union_id=union_id,
        open_id=data.get("open_id") or "",
        name=data.get("name") or "",
        # 自建应用 + contact:user.email:readonly 才有。只作展示。
        email=data.get("email") or data.get("enterprise_email") or "",
    )
