"""用户 upsert、角色与吊销，以及审计落库。"""

import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import current_request_id, get_logger
from app.core.timeutil import utc_now_iso
from app.modules.auth.model import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLES,
    STATUS_ACTIVE,
    STATUS_REVOKED,
    AuthAudit,
    User,
)
from app.modules.auth.service.feishu import FeishuUser
from app.modules.identity.service import login as device_login

logger = get_logger("agent.auth.users")

PROVIDER_FEISHU = "feishu"

# auth_audit.event 的取值。改这里要同步方案 §6.1。
EVENT_LOGIN = "login"
EVENT_LOGOUT = "logout"
EVENT_BREAK_GLASS = "break_glass"
EVENT_REVOKE = "revoke"
EVENT_RESTORE = "restore"
EVENT_ROLE_CHANGE = "role_change"
EVENT_LOGIN_REJECTED = "login_rejected"
# CLI 登录（P2）：callback 签发登录码记 cli_login，兑换出设备凭据记 cli_exchange，
# 设备已归别人记 cli_conflict，CLI 主动登出记 cli_logout。
EVENT_CLI_LOGIN = "cli_login"
EVENT_CLI_EXCHANGE = "cli_exchange"
EVENT_CLI_CONFLICT = "cli_conflict"
EVENT_CLI_LOGOUT = "cli_logout"


def audit(
    db: AsyncSession,
    *,
    event: str,
    actor: str,
    user_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """只追加。调用方负责 commit，和业务写入同一个事务。"""
    db.add(
        AuthAudit(
            created_at=utc_now_iso(),
            user_id=user_id,
            actor=actor or "",
            event=event,
            detail_json=json.dumps(detail, ensure_ascii=False) if detail else None,
            request_id=current_request_id(),
        )
    )


async def upsert_feishu_user(db: AsyncSession, info: FeishuUser) -> User:
    """按 (provider, tenant_key, union_id) 找人；没有就建 member。

    引导名单里的人每次登录都确保是 admin —— 这样误把自己降级了，登录一次就能恢复。
    名单外的人角色不动：admin 由管理台授予，不由登录改写。
    已吊销的人不在这里放行，交给调用方拒绝（status 不被登录改写）。
    """
    now = utc_now_iso()
    row = await db.execute(
        select(User).where(
            User.provider == PROVIDER_FEISHU,
            User.tenant_key == info.tenant_key,
            User.union_id == info.union_id,
        )
    )
    user = row.scalar_one_or_none()
    bootstrap = info.union_id in settings.login.bootstrap_union_ids
    if user is None:
        user = User(
            provider=PROVIDER_FEISHU,
            tenant_key=info.tenant_key,
            union_id=info.union_id,
            open_id=info.open_id,
            name=info.name,
            email=info.email,
            role=ROLE_ADMIN if bootstrap else ROLE_MEMBER,
            status=STATUS_ACTIVE,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.flush()
        return user

    user.open_id = info.open_id or user.open_id
    user.name = info.name or user.name
    user.email = info.email or user.email
    if bootstrap and user.role != ROLE_ADMIN:
        user.role = ROLE_ADMIN
    user.updated_at = now
    await db.flush()
    return user


async def get_user(db: AsyncSession, user_id: Optional[int]) -> Optional[User]:
    if user_id is None:
        return None
    return await db.get(User, user_id)


async def list_users(db: AsyncSession) -> list[User]:
    rows = await db.execute(select(User).order_by(User.id))
    return list(rows.scalars())


async def _get_user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


async def _active_admin_count(db: AsyncSession) -> int:
    row = await db.execute(
        select(func.count()).select_from(User).where(User.role == ROLE_ADMIN, User.status == STATUS_ACTIVE)
    )
    return int(row.scalar() or 0)


async def set_role(db: AsyncSession, user_id: int, role: str, *, actor: str, actor_user_id: Optional[int]) -> User:
    if role not in ROLES:
        raise HTTPException(status_code=422, detail="role must be admin or member")
    user = await _get_user_or_404(db, user_id)
    if user.role == role:
        return user
    # 不许把自己降成 member：降完立即 403，只能靠另一个 admin 或改 .env 救回来。
    if actor_user_id == user.id and role != ROLE_ADMIN:
        raise HTTPException(status_code=409, detail="cannot demote yourself")
    old = user.role
    user.role = role
    user.updated_at = utc_now_iso()
    audit(db, event=EVENT_ROLE_CHANGE, actor=actor, user_id=user.id, detail={"from": old, "to": role})
    await db.commit()
    logger.info("admin write", event="admin_write", action="user_role", target_id=str(user.id), actor=actor)
    return user


async def set_status(db: AsyncSession, user_id: int, revoked: bool, *, actor: str, actor_user_id: Optional[int]) -> User:
    """吊销 / 恢复。吊销后该用户已有会话的下一次请求即 401（read_session 每次查 status）。

    吊销同一个事务里连带吊销他名下全部设备凭据（P2），CLI 下一次请求即 401。
    恢复**不**恢复凭据：旧凭据已经作废，本人重新 `sid-code auth login` 即可。
    飞书 token 要到 P4 才落库，那时在这里一并删。
    """
    user = await _get_user_or_404(db, user_id)
    target = STATUS_REVOKED if revoked else STATUS_ACTIVE
    if user.status == target:
        return user
    if revoked and actor_user_id == user.id:
        raise HTTPException(status_code=409, detail="cannot revoke yourself")
    user.status = target
    user.updated_at = utc_now_iso()
    detail = None
    if revoked:
        detail = {"credentials_revoked": await device_login.revoke_user_devices(db, user.id)}
    audit(db, event=EVENT_REVOKE if revoked else EVENT_RESTORE, actor=actor, user_id=user.id, detail=detail)
    await db.commit()
    logger.info(
        "admin write",
        event="admin_write",
        action="user_revoke" if revoked else "user_restore",
        target_id=str(user.id),
        actor=actor,
    )
    return user


async def list_audit(db: AsyncSession, user_id: Optional[int], limit: int) -> list[AuthAudit]:
    q = select(AuthAudit).order_by(AuthAudit.id.desc()).limit(limit)
    if user_id is not None:
        q = q.where(AuthAudit.user_id == user_id)
    rows = await db.execute(q)
    return list(rows.scalars())
