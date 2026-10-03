"""OAuth state、浏览器 nonce 与 PKCE。

- state 与 nonce 都只存 sha256。state 一次性：UPDATE ... WHERE used_at IS NULL，rowcount==1。
- nonce 放在 HttpOnly cookie 里，callback 比对 state 绑定的 nonce 与 cookie 是否一致。
  不比的话，攻击者把**自己的**授权回调链接发给管理员，管理员点开就登进了攻击者的账号
  （登录 CSRF，方案 §5.7）。
- PKCE verifier 不落库：用会话签名密钥从 state 派生。拿到库也算不出 verifier。
"""

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import signing_key
from app.core.timeutil import is_expired, utc_now
from app.modules.auth.model import AuthState

STATE_TTL_MINUTES = 10
NONCE_COOKIE = "traj_login_nonce"
KIND_WEB = "web"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_verifier(state: str) -> str:
    """43 字符 base64url（RFC 7636 下限），由 state 确定性派生。"""
    digest = hmac.new(signing_key(), b"feishu-pkce-v1:" + state.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def safe_redirect_path(value: str | None) -> str:
    """只接受站内路径。`//evil.com`、`/\\evil.com`、完整 URL 一律回落到 /。"""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) < 0x20 for c in value):
        return "/"
    return value


async def create_state(db: AsyncSession, redirect_to: str) -> tuple[str, str]:
    """返回 (state, nonce) 明文。库里只有两者的 hash。"""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    now = utc_now()
    db.add(
        AuthState(
            state_hash=_sha256(state),
            kind=KIND_WEB,
            nonce_hash=_sha256(nonce),
            redirect_to=safe_redirect_path(redirect_to),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=STATE_TTL_MINUTES)).isoformat(),
        )
    )
    await db.commit()
    return state, nonce


async def consume_state(db: AsyncSession, state: str, nonce: str | None) -> AuthState | None:
    """校验并消费 state。任何一项不对都返回 None，调用方不区分原因（不给枚举机会）。

    顺序：先查、再比 nonce、最后原子消费。nonce 不对时**不消费**：
    否则攻击者能用一个错 nonce 的请求把管理员正在进行的登录作废。
    """
    if not state:
        return None
    row = await db.get(AuthState, _sha256(state))
    if row is None or row.kind != KIND_WEB or row.used_at is not None or is_expired(row.expires_at):
        return None
    if not nonce or not secrets.compare_digest(row.nonce_hash, _sha256(nonce)):
        return None
    consumed = await db.execute(
        update(AuthState)
        .where(AuthState.state_hash == row.state_hash)
        .where(AuthState.used_at.is_(None))
        .values(used_at=utc_now().isoformat())
    )
    if consumed.rowcount != 1:
        return None
    await db.commit()
    return row
