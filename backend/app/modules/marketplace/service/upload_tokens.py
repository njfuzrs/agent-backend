"""CI 上传凭据（机器凭据，方案 §7 / P2′）：签发、吊销、鉴权、越权拦截。

scope 只有一条：`POST /marketplace/items/{name}/versions`（上传 draft），且只能传到凭据所属组织的条目。
其余管理端点（发布 / 下架 / 登记 / 改可见范围 / 签发凭据 / 读审计）一律 403，并记一行 denied 审计。
发布不交给 CI：hooks 会在每个员工本机执行命令，发布前的人工核对是治理链的核心。

凭据形如 `mkt_<43 字符>`，走 `Authorization: Bearer`。前缀让拦截依赖不查库就能认出它，
也方便仓库的 secret 扫描。库里只存 sha256；明文只在签发响应里出现一次。
没有滑动续期：CI 凭据到期就重签，不因为天天在用而永远不过期。
吊销 / 过期立即生效：每次请求都查库。

为什么放在 marketplace 模块而不是 core/auth：它只授予这一个模块的一个端点，
放进 core 会让人以为它是第三种通用凭据。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db as db_mod
from app.core.auth.session import require_web_session
from app.core.logging import bind_context, current_request_id, get_logger
from app.core.timeutil import is_expired, utc_now, utc_now_iso
from app.modules.identity.service import admin as identity_admin
from app.modules.identity.service.secrets import hash_secret, mint_credential
from app.modules.marketplace.model import MarketTokenAudit, MarketUploadToken
from app.modules.marketplace.schemas import (
    UploadTokenAuditItem,
    UploadTokenAuditListResponse,
    UploadTokenCreate,
    UploadTokenIssued,
    UploadTokenItem,
    UploadTokenListResponse,
)

auth_logger = get_logger("agent.auth")

TOKEN_PREFIX = "mkt_"
AUTH_KIND = "market_token"


@dataclass(frozen=True)
class Uploader:
    """上传端点的调用方。token_id 为 None = 管理台 admin（cookie 会话）。"""

    actor: str
    token_id: Optional[int] = None
    token_name: str = ""
    # 机器凭据只能传这个组织的条目；cookie 会话不限
    org_id: Optional[str] = None


def actor_of(token: MarketUploadToken) -> str:
    """审计里的 actor。带 id：同名凭据吊销后重签，审计仍分得清是哪一把。"""
    return f"ci:{token.name}#{token.id}"


def _machine_token(request: Request) -> Optional[str]:
    """Authorization: Bearer mkt_... → 凭据明文；不是机器凭据返回 None。"""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    value = header[len("Bearer "):].strip()
    return value if value.startswith(TOKEN_PREFIX) else None


async def _lookup(db: AsyncSession, raw: str) -> MarketUploadToken:
    """查凭据。无效 / 吊销 / 过期 → 401（fail-closed），reason 只进日志。"""
    token_hash = hash_secret(raw)
    row = (
        await db.execute(select(MarketUploadToken).where(MarketUploadToken.token_hash == token_hash))
    ).scalar_one_or_none()
    stored = row.token_hash if row is not None else "0" * 64
    if row is None or not secrets.compare_digest(stored, token_hash):
        _reject("unknown")
    if row.revoked_at is not None:
        _reject("revoked")
    if is_expired(row.expires_at):
        _reject("expired")
    return row


def _reject(reason: str) -> None:
    auth_logger.warning("credential rejected", event="auth_rejected", reason=f"market_token_{reason}")
    raise HTTPException(status_code=401, detail="Unauthorized")


async def require_uploader(request: Request) -> Uploader:
    """上传端点的鉴权：机器凭据，或管理台 admin cookie。两者之一即可。"""
    raw = _machine_token(request)
    if raw is None:
        return Uploader(actor=await require_web_session(request))

    async with db_mod.async_session() as db:
        token = await _lookup(db, raw)
        bind_context(auth=AUTH_KIND, actor=actor_of(token))
        token.last_used_at = utc_now_iso()
        await db.commit()
        return Uploader(actor=actor_of(token), token_id=token.id, token_name=token.name, org_id=token.org_id)


async def deny_upload_token(request: Request) -> None:
    """挂在其余管理端点上，排在 require_web_session 之前。

    有效的机器凭据 → 403 并记 denied（这是越权尝试，不是没登录，不让 CI 误以为换个凭据就行）；
    无效 / 吊销 / 过期 → 401。不带机器凭据 → 放行给后面的 cookie 校验。
    """
    raw = _machine_token(request)
    if raw is None:
        return
    async with db_mod.async_session() as db:
        token = await _lookup(db, raw)
        bind_context(auth=AUTH_KIND, actor=actor_of(token))
        _audit(
            db, token=token, action="denied", actor=actor_of(token),
            detail={"method": request.method, "path": request.url.path},
        )
        await db.commit()
    auth_logger.warning("credential rejected", event="auth_rejected", reason="market_token_out_of_scope")
    raise HTTPException(status_code=403, detail="CI 上传凭据只能上传草稿；发布 / 下架请到管理台操作")


async def record_upload(uploader: Uploader, item_name: str, version: str, sha256: str) -> None:
    """机器凭据上传成功后，在凭据审计里也记一行：按凭据查「这把传过什么」不必去翻 market_audit。"""
    if uploader.token_id is None:
        return
    async with db_mod.async_session() as db:
        token = await db.get(MarketUploadToken, uploader.token_id)
        if token is not None:
            _audit(db, token=token, action="upload", actor=uploader.actor,
                   detail={"item": item_name, "version": version, "sha256": sha256})
            await db.commit()


async def deny_cross_org(uploader: Uploader, item_org_id: str, item_name: str) -> None:
    """机器凭据往别的组织的条目上传 → 403 并记 denied。cookie 会话不受限。"""
    if uploader.token_id is None or uploader.org_id == item_org_id:
        return
    async with db_mod.async_session() as db:
        token = await db.get(MarketUploadToken, uploader.token_id)
        if token is not None:
            _audit(db, token=token, action="denied", actor=uploader.actor,
                   detail={"item": item_name, "item_org_id": item_org_id, "token_org_id": uploader.org_id})
            await db.commit()
    raise HTTPException(status_code=403, detail=f"该凭据只能上传组织 {uploader.org_id!r} 的插件")


# ---------------------------------------------------------------------------
# 管理台：签发 / 列表 / 吊销 / 审计
# ---------------------------------------------------------------------------
async def issue(db: AsyncSession, payload: UploadTokenCreate, actor: str) -> UploadTokenIssued:
    if await identity_admin.get_organization(db, payload.org_id) is None:
        raise HTTPException(status_code=404, detail=f"组织 {payload.org_id!r} 不存在")
    raw = TOKEN_PREFIX + mint_credential()
    now = utc_now()
    token = MarketUploadToken(
        name=payload.name,
        token_hash=hash_secret(raw),
        org_id=payload.org_id,
        created_at=now.isoformat(),
        created_by=actor or "",
        expires_at=(now + timedelta(days=payload.expires_in_days)).isoformat(),
    )
    db.add(token)
    await db.flush()
    _audit(db, token=token, action="issue", actor=actor, reason=payload.reason.strip(),
           detail={"org_id": token.org_id, "expires_at": token.expires_at})
    await db.commit()
    return UploadTokenIssued(**_to_item(token).model_dump(), token=raw)


async def revoke(db: AsyncSession, token_id: int, actor: str, reason: str) -> UploadTokenItem:
    token = await db.get(MarketUploadToken, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="凭据不存在")
    if token.revoked_at is not None:
        raise HTTPException(status_code=409, detail="凭据已经吊销")
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason 必填（会进审计）")
    token.revoked_at = utc_now_iso()
    token.revoked_by = actor or ""
    _audit(db, token=token, action="revoke", actor=actor, reason=reason)
    await db.commit()
    return _to_item(token)


async def list_tokens(db: AsyncSession) -> UploadTokenListResponse:
    rows = (await db.execute(select(MarketUploadToken).order_by(MarketUploadToken.id.desc()))).scalars().all()
    return UploadTokenListResponse(items=[_to_item(t) for t in rows])


async def list_audit(db: AsyncSession, *, limit: int = 100) -> UploadTokenAuditListResponse:
    rows = (
        await db.execute(select(MarketTokenAudit).order_by(MarketTokenAudit.id.desc()).limit(limit))
    ).scalars().all()
    return UploadTokenAuditListResponse(
        items=[
            UploadTokenAuditItem(
                id=r.id, token_id=r.token_id, token_name=r.token_name, action=r.action, reason=r.reason,
                actor=r.actor, detail=_loads(r.detail_json), created_at=r.created_at, request_id=r.request_id,
            )
            for r in rows
        ]
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _audit(
    db: AsyncSession,
    *,
    token: MarketUploadToken,
    action: str,
    actor: str,
    reason: str = "",
    detail: Optional[dict[str, Any]] = None,
) -> None:
    db.add(
        MarketTokenAudit(
            token_id=token.id,
            token_name=token.name,
            action=action,
            reason=reason,
            actor=actor or "",
            detail_json=json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
            created_at=utc_now_iso(),
            request_id=current_request_id(),
        )
    )


def _loads(raw: Optional[str]) -> dict[str, Any]:
    try:
        value = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _to_item(token: MarketUploadToken) -> UploadTokenItem:
    if token.revoked_at is not None:
        status = "revoked"
    elif is_expired(token.expires_at):
        status = "expired"
    else:
        status = "active"
    return UploadTokenItem(
        id=token.id, name=token.name, org_id=token.org_id, status=status, created_at=token.created_at,
        created_by=token.created_by, expires_at=token.expires_at, last_used_at=token.last_used_at,
        revoked_at=token.revoked_at, revoked_by=token.revoked_by,
    )
