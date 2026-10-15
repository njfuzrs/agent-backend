"""管理台身份 API。走 cookie 会话，不走控制面 Bearer，也不走上传 token。

路径在 /api/v1/identity/**，不在 /ctl/ —— 这是给人看的控制台，不是给客户端下发策略。
鉴权用 require_web_session：不 import 数据面符号，边界测试对 identity 模块的禁令才能保住。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.db import get_db
from app.modules.identity.schemas import (
    DeviceListResponse,
    DeviceMove,
    DeviceMoveResponse,
    EnrollCodeCreate,
    EnrollCodeCreated,
    EnrollCodeListResponse,
    IdentityAuditListResponse,
    OrganizationCreate,
    OrganizationItem,
    OrganizationListResponse,
    OrganizationRename,
    RevokeResponse,
    TeamCreate,
    TeamItem,
    TeamListResponse,
    TeamRename,
)
from app.modules.identity.service import admin as admin_service

router = APIRouter(
    prefix="/identity",
    tags=["identity-admin"],
    dependencies=[Depends(require_web_session)],
)


@router.get("/organizations", response_model=OrganizationListResponse)
async def list_organizations(db: AsyncSession = Depends(get_db)):
    return await admin_service.list_organizations(db)


@router.post("/organizations", response_model=OrganizationItem, status_code=201)
async def create_organization(
    payload: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.create_organization(db, payload)


@router.patch("/organizations/{org_id}", response_model=OrganizationItem)
async def rename_organization(
    org_id: str,
    payload: OrganizationRename,
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.rename_organization(db, org_id, payload)


@router.get("/teams", response_model=TeamListResponse)
async def list_teams(
    org_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_teams(db, org_id=org_id)


@router.post("/teams", response_model=TeamItem, status_code=201)
async def create_team(
    payload: TeamCreate,
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.create_team(db, payload)


@router.patch("/organizations/{org_id}/teams/{team_id}", response_model=TeamItem)
async def rename_team(
    org_id: str,
    team_id: str,
    payload: TeamRename,
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.rename_team(db, org_id, team_id, payload)


@router.get("/audit", response_model=IdentityAuditListResponse)
async def list_audit(
    target_type: Optional[str] = Query(None, pattern="^(org|team|device|enroll_code)$"),
    target_id: Optional[str] = None,
    org_id: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_audit(
        db, target_type=target_type, target_id=target_id, org_id=org_id, limit=limit
    )


@router.get("/enroll-codes", response_model=EnrollCodeListResponse)
async def list_enroll_codes(db: AsyncSession = Depends(get_db)):
    return await admin_service.list_enroll_codes(db)


@router.post("/enroll-codes", response_model=EnrollCodeCreated, status_code=201)
async def create_enroll_code(
    payload: EnrollCodeCreate,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await admin_service.create_enroll_code(db, payload, created_by=username)


@router.get("/devices", response_model=DeviceListResponse)
async def list_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    org_id: Optional[str] = None,
    device_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_devices(
        db, page=page, page_size=page_size, org_id=org_id, device_id=device_id
    )


@router.post("/devices/{device_id}/revoke", response_model=RevokeResponse)
async def revoke_device(
    device_id: str,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    """吊销设备全部凭据。理由必填（与策略 / 预算 / flag 的下架类操作同一形状：query 参数）。"""
    return await admin_service.revoke_device(db, device_id, reason=reason)


@router.post("/devices/{device_id}/move", response_model=DeviceMoveResponse)
async def move_device(
    device_id: str,
    payload: DeviceMove,
    db: AsyncSession = Depends(get_db),
):
    """调整设备的组织 / 团队。下一次控制面请求的 DeviceContext 即为新值。"""
    return await admin_service.move_device(db, device_id, payload)
