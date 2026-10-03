"""OAuth state、浏览器 nonce 与 PKCE。

- state 与 nonce 都只存 sha256。state 一次性：UPDATE ... WHERE used_at IS NULL，rowcount==1。
- nonce 放在 HttpOnly cookie 里，callback 比对 state 绑定的 nonce 与 cookie 是否一致。
  不比的话，攻击者把**自己的**授权回调链接发给管理员，管理员点开就登进了攻击者的账号
  （登录 CSRF，方案 §5.7）。
- PKCE verifier 不落库：用会话签名密钥从 state 派生。拿到库也算不出 verifier。

CLI 登录（P2）复用同一张表，kind=cli，多记 CLI 的回调端口、PKCE challenge、
cli_state 与 device_id。callback 消费 state 后按 kind 分流。
CLI 流程同样下发 nonce cookie：浏览器是 CLI 打开的那个，start 与 callback 在同一个浏览器里。

一次性登录码（login_codes）也在这里：60 秒、只存 hash、绑定 user + device + challenge。
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import signing_key
from app.core.timeutil import is_expired, utc_now
from app.modules.auth.model import AuthState, LoginCode

STATE_TTL_MINUTES = 10
NONCE_COOKIE = "traj_login_nonce"
KIND_WEB = "web"
KIND_CLI = "cli"
KINDS = (KIND_WEB, KIND_CLI)

LOGIN_CODE_TTL_SECONDS = 60

# CLI 入参的形状。start 时不合格直接 400，不建 state、不跳转：端口还不可信，没处可回。
# 端口不许落在特权段：CLI 是普通用户进程，拿不到 1024 以下。
CLI_PORT_MIN = 1024
CLI_PORT_MAX = 65535
# S256 challenge = base64url(sha256) 去掉填充，恰好 43 字符。
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_CLI_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")  # RFC 7636 §4.1


@dataclass(frozen=True)
class CliParams:
    port: int
    challenge: str
    cli_state: str
    device_id: str


def validate_cli_params(port: int, challenge: str, cli_state: str, device_id: str) -> CliParams | None:
    if not (CLI_PORT_MIN <= port <= CLI_PORT_MAX):
        return None
    if not _CHALLENGE_RE.match(challenge or ""):
        return None
    if not _CLI_STATE_RE.match(cli_state or ""):
        return None
    if not _DEVICE_ID_RE.match(device_id or ""):
        return None
    return CliParams(port=port, challenge=challenge, cli_state=cli_state, device_id=device_id)


def cli_redirect_url(port: int) -> str:
    """回跳 CLI 的地址。主机写死 127.0.0.1，唯一的变量是端口（已校验为整数）—— 不存在开放重定向。"""
    return f"http://127.0.0.1:{int(port)}/callback"


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


async def create_state(db: AsyncSession, redirect_to: str, cli: CliParams | None = None) -> tuple[str, str]:
    """返回 (state, nonce) 明文。库里只有两者的 hash。cli 非空即 kind=cli。"""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    now = utc_now()
    db.add(
        AuthState(
            state_hash=_sha256(state),
            kind=KIND_CLI if cli is not None else KIND_WEB,
            nonce_hash=_sha256(nonce),
            redirect_to="/" if cli is not None else safe_redirect_path(redirect_to),
            cli_port=cli.port if cli is not None else None,
            cli_challenge=cli.challenge if cli is not None else None,
            cli_state=cli.cli_state if cli is not None else None,
            device_id=cli.device_id if cli is not None else None,
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
    if row is None or row.kind not in KINDS or row.used_at is not None or is_expired(row.expires_at):
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


# ---------------------------------------------------------------------------
# 一次性登录码（CLI）
# ---------------------------------------------------------------------------
def verifier_matches(verifier: str, challenge: str) -> bool:
    if not _VERIFIER_RE.match(verifier or ""):
        return False
    return secrets.compare_digest(pkce_challenge(verifier), challenge)


async def create_login_code(db: AsyncSession, *, user_id: int, device_id: str, challenge: str) -> str:
    """返回明文登录码。调用方负责 commit（与登录审计同一个事务）。"""
    code = secrets.token_urlsafe(32)
    now = utc_now()
    db.add(
        LoginCode(
            code_hash=_sha256(code),
            user_id=user_id,
            device_id=device_id,
            cli_challenge=challenge,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=LOGIN_CODE_TTL_SECONDS)).isoformat(),
        )
    )
    return code


async def consume_login_code(db: AsyncSession, code: str, verifier: str, device_id: str) -> LoginCode | None:
    """校验并消费登录码。任何一项不对都返回 None，调用方不区分原因。

    verifier 或 device_id 不对时**不消费**：同机别的进程截到登录码但没有 verifier，
    不能用一次错误的兑换把真正的 CLI 的登录作废。verifier 是 32 字节随机数，不怕穷举。
    """
    if not code:
        return None
    row = await db.get(LoginCode, _sha256(code))
    if row is None or row.used_at is not None or is_expired(row.expires_at):
        return None
    if not secrets.compare_digest(row.device_id, device_id or ""):
        return None
    if not verifier_matches(verifier, row.cli_challenge):
        return None
    consumed = await db.execute(
        update(LoginCode)
        .where(LoginCode.code_hash == row.code_hash)
        .where(LoginCode.used_at.is_(None))
        .values(used_at=utc_now().isoformat())
    )
    if consumed.rowcount != 1:
        return None
    return row
