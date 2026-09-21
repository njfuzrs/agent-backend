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
    EnrollCodeCreate,
    EnrollCodeCreated,
    EnrollCodeListResponse,
    OrganizationCreate,
    OrganizationItem,
    OrganizationListResponse,
    RevokeResponse,
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
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.list_devices(db, page=page, page_size=page_size, org_id=org_id)


@router.post("/devices/{device_id}/revoke", response_model=RevokeResponse)
async def revoke_device(
    device_id: str,
    db: AsyncSession = Depends(get_db),
):
    return await admin_service.revoke_device(db, device_id)
