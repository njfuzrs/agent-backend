"""飞书 OAuth 客户端：授权页地址、授权码换 token、取用户信息。

只做 HTTP，不碰库。路由层通过模块属性调用（`feishu.exchange_code(...)`），
测试替换这两个函数即可，不用起假的飞书。

三条纪律：
- app_secret 只在请求体里出现，不进日志、不进异常消息。
- 飞书的响应体不原样进日志（rag-service 那行 `logger.info(f"... {result}")` 把 token 打进了日志）。
  失败只带飞书的错误码。
- P1 不保存 user_access_token：登录只为拿身份，用完即弃。P4 才加密落库。
"""

from dataclasses import dataclass
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


async def exchange_code(code: str, code_verifier: str) -> str:
    """授权码换 user_access_token。授权码 5 分钟有效、只能用一次，回调里立刻换。"""
    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.login.FEISHU_APP_ID,
        "client_secret": settings.login.FEISHU_APP_SECRET,
        "code": code,
        "redirect_uri": redirect_uri(),
        "code_verifier": code_verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(TOKEN_URL, json=payload)
            body = resp.json()
    except (httpx.HTTPError, ValueError):
        raise FeishuError("token", "network") from None
    if not isinstance(body, dict) or body.get("code") not in (0, None):
        raise FeishuError("token", str(body.get("code") if isinstance(body, dict) else "bad_body"))
    # v2 端点直接返回 token 字段，没有 data 包装
    access = body.get("access_token")
    if not access:
        raise FeishuError("token", "no_access_token")
    return access


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
