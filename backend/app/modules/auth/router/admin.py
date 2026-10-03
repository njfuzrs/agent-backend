"""管理台用户管理：列表、改角色、吊销 / 恢复、登录审计。只给 admin。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import SessionPrincipal, require_admin_principal, require_web_session
from app.core.db import get_db
from app.modules.auth.schemas import (
    AuthAuditItem,
    AuthAuditListResponse,
    UserItem,
    UserListResponse,
    UserRoleUpdate,
)
from app.modules.auth.service import users as users_service
from app.modules.identity.service import login as device_login

router = APIRouter(
    prefix="/users",
    tags=["users-admin"],
    dependencies=[Depends(require_web_session)],
)


def _item(user, device_count: int = 0) -> UserItem:
    return UserItem(
        id=user.id,
        provider=user.provider,
        union_id=user.union_id,
        name=user.name,
        email=user.email,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        device_count=device_count,
    )


@router.get("", response_model=UserListResponse)
async def list_users(db: AsyncSession = Depends(get_db)):
    counts = await device_login.device_counts_by_user(db)
    return UserListResponse(items=[_item(u, counts.get(u.id, 0)) for u in await users_service.list_users(db)])


@router.get("/audit", response_model=AuthAuditListResponse)
async def list_audit(
    user_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = await users_service.list_audit(db, user_id, limit)
    return AuthAuditListResponse(items=[AuthAuditItem.model_validate(r, from_attributes=True) for r in rows])


@router.patch("/{user_id}/role", response_model=UserItem)
async def set_role(
    user_id: int,
    payload: UserRoleUpdate,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_admin_principal),
):
    user = await users_service.set_role(
        db, user_id, payload.role, actor=principal.actor, actor_user_id=principal.user_id
    )
    return _item(user)


@router.post("/{user_id}/revoke", response_model=UserItem)
async def revoke(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_admin_principal),
):
    user = await users_service.set_status(
        db, user_id, True, actor=principal.actor, actor_user_id=principal.user_id
    )
    return _item(user)


@router.post("/{user_id}/restore", response_model=UserItem)
async def restore(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    principal: SessionPrincipal = Depends(require_admin_principal),
):
    user = await users_service.set_status(
        db, user_id, False, actor=principal.actor, actor_user_id=principal.user_id
    )
    return _item(user)
