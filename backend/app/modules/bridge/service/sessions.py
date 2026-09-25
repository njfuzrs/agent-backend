"""session 签发、吊销、列表。

身份一律从调用方传入的 DeviceContext 取，**不从 body 取**。body 里的
device_id / org_id 是攻击面：谁都能写自己属于别的组织。

token 明文只在返回值里出现一次。入库前先 sha256，与设备凭据同款
（identity.service.secrets.hash_secret）。比较在握手侧用 compare_digest。

失败语义：签发是授予信任，fail-closed。超限 429，凭据问题由 require_device
在进到这里之前已经 401。
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext
from app.core.config import settings
from app.core.logging import current_request_id, get_logger
from app.core.timeutil import is_expired, utc_now, utc_now_iso
from app.modules.bridge.model import BridgeAudit, BridgeSession, BridgeSessionToken
from app.modules.bridge.schemas import (
    SessionIssued,
    SessionItem,
    SessionListResponse,
    SessionWhoami,
)
from app.modules.bridge.service.guard import (
    MAX_ONLINE_PER_ORG,
    ONLINE_STATES,
    SESSION_TTL_HOURS,
    UNPAIRED_WAIT_MINUTES,
    require_reason,
)
from app.modules.identity.service.secrets import hash_secret

logger = get_logger("agent.bridge")

# 本地缺省。生产必须配 BRIDGE_WS_PUBLIC_URL，否则远程客户端会去连自己的回环。
_LOCAL_WS_URL = "ws://127.0.0.1:8901/api/v1/bridge/ws"

# 展示字段的长度上限。超了截断而不是 422：这是客户端自报的装饰信息，
# 不值得因为一个长路径让整次签发失败。cwd 只留最后一段。
_VER_MAX = 64
_CWD_MAX = 255


def public_ws_url() -> str:
    """签发响应里的 ws_url。

    不从请求的 Host 猜。M3 踩过「HTTP 301 把 POST 降成 GET」，WS 被 301 更糟。
    配了非 wss、又不是回环的地址直接拒绝签发：发错比不发更危险。
    """
    configured = (settings.control_plane.BRIDGE_WS_PUBLIC_URL or "").strip()
    url = configured or _LOCAL_WS_URL
    if not _url_is_safe(url):
        logger.error(
            "bridge ws url rejected",
            event="bridge_ws_url_rejected",
            outcome="error",
        )
        raise HTTPException(status_code=500, detail="bridge ws url misconfigured")
    return url


def _url_is_safe(url: str) -> bool:
    """wss 一律放行。ws 只允许指向本机回环，且必须是 sidecar 那条路径。"""
    if url.startswith("wss://") and " " not in url:
        return True
    if not url.startswith("ws://"):
        return False
    rest = url[len("ws://"):]
    host = rest.split("/", 1)[0]
    hostname = host.split(":", 1)[0]
    if hostname not in {"127.0.0.1", "localhost"}:
        return False
    return rest.endswith("/api/v1/bridge/ws")


async def issue_cli_session(
    db: AsyncSession,
    ctx: DeviceContext,
    *,
    ver: Optional[str],
    cwd_basename: Optional[str],
) -> SessionIssued:
    """CLI 侧创建 session，返回一次性明文 token。"""
    await _enforce_org_cap(db, ctx.org_id)
    now = utc_now()
    now_iso = now.isoformat()
    expires_at = (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    session_id = "br_" + secrets.token_hex(16)
    token = secrets.token_urlsafe(32)

    db.add(
        BridgeSession(
            id=session_id,
            device_id=ctx.device_id,
            org_id=ctx.org_id,
            team_id=ctx.team_id or "",
            ver=_clip(ver, _VER_MAX),
            cwd_basename=_basename(cwd_basename),
            state="waiting",
            created_at=now_iso,
            expires_at=expires_at,
        )
    )
    db.add(
        BridgeSessionToken(
            session_id=session_id,
            role="cli",
            token_hash=hash_secret(token),
            expires_at=expires_at,
            created_at=now_iso,
        )
    )
    _audit(
        db,
        session_id=session_id,
        actor=f"device:{ctx.device_id}",
        action="issue_cli",
        reason=None,
        now_iso=now_iso,
    )
    await db.commit()
    logger.info(
        "bridge session issued",
        event="bridge_session_issued",
        action="issue_cli",
        target_id=session_id,
        device_id=ctx.device_id,
        org_id=ctx.org_id,
    )
    return SessionIssued(
        session_id=session_id,
        session_token=token,
        ws_url=public_ws_url(),
        expires_at=expires_at,
    )


async def issue_controller_token(
    db: AsyncSession,
    session_id: str,
    actor: str,
) -> SessionIssued:
    """管理台签发 controller 侧 token。同一 session 只留一张有效的。"""
    session = await _require_session(db, session_id)
    if session.state in {"disconnected", "expired"} or is_expired(session.expires_at):
        raise HTTPException(status_code=409, detail="session 已结束或过期，不能再签发")

    now_iso = utc_now_iso()
    token = secrets.token_urlsafe(32)
    token_hash = hash_secret(token)
    # (session_id, role) 唯一，所以不能「插一条新的再留着旧的」。
    # 旧行直接换 hash：旧明文立刻对不上，效果与吊销后再签发相同，
    # 审计侧记的是这一次 issue_controller，不靠 token 行数。
    existing = await db.execute(
        select(BridgeSessionToken).where(
            BridgeSessionToken.session_id == session_id,
            BridgeSessionToken.role == "controller",
        )
    )
    current = existing.scalar_one_or_none()
    if current is None:
        db.add(
            BridgeSessionToken(
                session_id=session_id,
                role="controller",
                token_hash=token_hash,
                expires_at=session.expires_at,
                created_at=now_iso,
            )
        )
    else:
        current.token_hash = token_hash
        current.revoked_at = None
        current.expires_at = session.expires_at
        current.created_at = now_iso
    _audit(
        db,
        session_id=session_id,
        actor=f"web:{actor}",
        action="issue_controller",
        reason=None,
        now_iso=now_iso,
    )
    await db.commit()
    logger.info(
        "bridge controller token issued",
        event="bridge_session_issued",
        action="issue_controller",
        target_id=session_id,
        actor=actor,
    )
    return SessionIssued(
        session_id=session_id,
        session_token=token,
        ws_url=public_ws_url(),
        expires_at=session.expires_at,
    )


async def disconnect_session(
    db: AsyncSession,
    session_id: str,
    actor: str,
    reason: str,
) -> None:
    """管理台强制断开。reason 必填，进审计。

    sidecar 不在本进程里。这里只改 state，sidecar 轮询到 disconnected 后
    关两侧连接。所以这个函数返回时连接可能还没断，这是预期。
    """
    reason = require_reason(reason)
    session = await _require_session(db, session_id)
    now_iso = utc_now_iso()
    if session.state not in {"disconnected", "expired"}:
        session.state = "disconnected"
        session.disconnect_reason = reason
    # token 一并吊销：即便 sidecar 还没轮询到，重连也进不来。
    await db.execute(
        update(BridgeSessionToken)
        .where(
            BridgeSessionToken.session_id == session_id,
            BridgeSessionToken.revoked_at.is_(None),
        )
        .values(revoked_at=now_iso)
    )
    _audit(
        db,
        session_id=session_id,
        actor=f"web:{actor}",
        action="disconnect",
        reason=reason,
        now_iso=now_iso,
    )
    await db.commit()
    logger.info(
        "bridge session disconnected",
        event="bridge_session_disconnected",
        action="disconnect",
        target_id=session_id,
        actor=actor,
    )


async def whoami(db: AsyncSession, ctx: DeviceContext) -> SessionWhoami:
    """当前设备最新的一条未过期 session。调试用，不返回 token。"""
    row = await db.execute(
        select(BridgeSession)
        .where(
            BridgeSession.device_id == ctx.device_id,
            BridgeSession.state.in_(ONLINE_STATES),
        )
        .order_by(BridgeSession.created_at.desc())
    )
    session = row.scalars().first()
    if session is None or is_expired(session.expires_at):
        return SessionWhoami()
    return SessionWhoami(
        session_id=session.id,
        state=session.state,
        expires_at=session.expires_at,
    )


async def list_sessions(db: AsyncSession) -> SessionListResponse:
    """管理台列表。顺手把等太久还没配对的标成 expired。

    懒标记：不另起 cron。握手时仍按 expires_at 拒绝，这里只影响列表看到的状态。
    """
    await _expire_stale_waiting(db)
    rows = await db.execute(
        select(BridgeSession).order_by(BridgeSession.created_at.desc()).limit(200)
    )
    items = [
        SessionItem(
            id=s.id,
            device_id=s.device_id,
            org_id=s.org_id,
            state=s.state,
            ver=s.ver,
            cwd_basename=s.cwd_basename,
            created_at=s.created_at,
            expires_at=s.expires_at,
        )
        for s in rows.scalars()
    ]
    return SessionListResponse(items=items)


async def record_auth_failure(db: AsyncSession, session_id: Optional[str]) -> None:
    """握手失败记一笔。不写 token，不写细因。写失败只记日志，不挡关闭连接。"""
    try:
        _audit(
            db,
            session_id=session_id,
            actor="anon",
            action="auth_fail",
            reason=None,
            now_iso=utc_now_iso(),
        )
        await db.commit()
    except Exception:
        logger.warning(
            "bridge auth failure audit dropped",
            event="bridge_audit_dropped",
            outcome="error",
        )
        await db.rollback()


async def _enforce_org_cap(db: AsyncSession, org_id: str) -> None:
    row = await db.execute(
        select(func.count())
        .select_from(BridgeSession)
        .where(
            BridgeSession.org_id == org_id,
            BridgeSession.state.in_(ONLINE_STATES),
        )
    )
    online = row.scalar_one()
    if online >= MAX_ONLINE_PER_ORG:
        raise HTTPException(status_code=429, detail="too many bridge sessions")


async def _expire_stale_waiting(db: AsyncSession) -> None:
    """waiting 超过 UNPAIRED_WAIT_MINUTES 的标 expired，并吊销它的 token。"""
    cutoff = (utc_now() - timedelta(minutes=UNPAIRED_WAIT_MINUTES)).isoformat()
    now_iso = utc_now_iso()
    rows = await db.execute(
        select(BridgeSession.id).where(
            BridgeSession.state == "waiting",
            BridgeSession.created_at <= cutoff,
        )
    )
    stale_ids = [r[0] for r in rows]
    if not stale_ids:
        return
    await db.execute(
        update(BridgeSession)
        .where(BridgeSession.id.in_(stale_ids))
        .values(state="expired")
    )
    await db.execute(
        update(BridgeSessionToken)
        .where(
            BridgeSessionToken.session_id.in_(stale_ids),
            BridgeSessionToken.revoked_at.is_(None),
        )
        .values(revoked_at=now_iso)
    )
    await db.commit()


async def _require_session(db: AsyncSession, session_id: str) -> BridgeSession:
    row = await db.execute(select(BridgeSession).where(BridgeSession.id == session_id))
    session = row.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _audit(
    db: AsyncSession,
    *,
    session_id: Optional[str],
    actor: str,
    action: str,
    reason: Optional[str],
    now_iso: str,
) -> None:
    db.add(
        BridgeAudit(
            session_id=session_id,
            actor=actor,
            action=action,
            reason=reason,
            created_at=now_iso,
            # 取不到就留空。不为没有请求的写入编造编号（方案 §3.9）。
            request_id=current_request_id(),
        )
    )


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def _basename(value: Optional[str]) -> Optional[str]:
    """只留最后一段。调用方传了绝对路径也不能进库。"""
    text = _clip(value, 1024)
    if text is None:
        return None
    tail = text.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return tail[:_CWD_MAX] or None
