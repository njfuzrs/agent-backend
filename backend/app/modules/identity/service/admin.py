"""组织 / 注册码 / 设备列表。供管理台调用，不走控制面 Bearer。"""

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.modules.identity.model import Device, DeviceCredential, EnrollCode, Organization, Team
from app.modules.identity.schemas import (
    DeviceListItem,
    DeviceListResponse,
    EnrollCodeCreate,
    EnrollCodeCreated,
    EnrollCodeItem,
    EnrollCodeListResponse,
    OrganizationCreate,
    OrganizationItem,
    OrganizationListResponse,
    RevokeResponse,
)
from app.modules.identity.service.secrets import hash_secret, mint_enroll_code
from app.modules.identity.service.timeutil import iso_after, utc_now_iso


async def create_organization(db: AsyncSession, payload: OrganizationCreate) -> OrganizationItem:
    now_iso = utc_now_iso()
    existing = await db.execute(select(Organization).where(Organization.org_id == payload.org_id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="org_id already exists")
    org = Organization(
        org_id=payload.org_id,
        name=payload.name or payload.org_id,
        created_at=now_iso,
    )
    db.add(org)
    await db.commit()
    return OrganizationItem(org_id=org.org_id, name=org.name, created_at=org.created_at, device_count=0)


async def list_organizations(db: AsyncSession) -> OrganizationListResponse:
    count_row = (
        select(Device.organization_id, func.count(Device.id).label("n"))
        .group_by(Device.organization_id)
        .subquery()
    )
    result = await db.execute(
        select(Organization, func.coalesce(count_row.c.n, 0))
        .outerjoin(count_row, count_row.c.organization_id == Organization.id)
        .order_by(Organization.created_at.asc())
    )
    items = [
        OrganizationItem(
            org_id=org.org_id,
            name=org.name,
            created_at=org.created_at,
            device_count=int(n),
        )
        for org, n in result.all()
    ]
    return OrganizationListResponse(items=items)


async def create_enroll_code(
    db: AsyncSession,
    payload: EnrollCodeCreate,
    created_by: str,
) -> EnrollCodeCreated:
    org = await _get_or_create_org(db, payload.org_id, payload.org_name)
    team = None
    if payload.team_id:
        team = await _get_or_create_team(db, org, payload.team_id, payload.team_name)

    ttl_hours = payload.ttl_hours or settings.control_plane.CTL_ENROLL_CODE_TTL_HOURS
    now_iso = utc_now_iso()
    plaintext = mint_enroll_code()
    expires_at = iso_after(hours=ttl_hours)
    db.add(
        EnrollCode(
            code_hash=hash_secret(plaintext),
            organization_id=org.id,
            team_id=team.id if team is not None else None,
            expires_at=expires_at,
            created_at=now_iso,
            created_by=created_by,
            note=payload.note or "",
        )
    )
    await db.commit()
    return EnrollCodeCreated(
        code=plaintext,
        org_id=org.org_id,
        team_id=team.team_id if team is not None else "",
        expires_at=expires_at,
        note=payload.note or "",
    )


async def list_enroll_codes(db: AsyncSession) -> EnrollCodeListResponse:
    result = await db.execute(
        select(EnrollCode)
        .options(selectinload(EnrollCode.organization), selectinload(EnrollCode.team))
        .order_by(EnrollCode.created_at.desc())
    )
    items = []
    for code in result.scalars().all():
        items.append(
            EnrollCodeItem(
                id=code.id,
                org_id=code.organization.org_id if code.organization else "",
                team_id=code.team.team_id if code.team else "",
                expires_at=code.expires_at,
                used_at=code.used_at,
                created_at=code.created_at,
                created_by=code.created_by,
                note=code.note,
            )
        )
    return EnrollCodeListResponse(items=items)


async def list_devices(
    db: AsyncSession,
    page: int,
    page_size: int,
    org_id: str | None = None,
) -> DeviceListResponse:
    filters = []
    if org_id:
        filters.append(Organization.org_id == org_id)

    count_stmt = select(func.count(Device.id))
    if org_id:
        count_stmt = count_stmt.join(Organization).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Device)
        .options(
            selectinload(Device.organization),
            selectinload(Device.team),
            selectinload(Device.credentials),
        )
        .order_by(Device.last_seen_at.desc().nullslast(), Device.created_at.desc())
    )
    if org_id:
        stmt = stmt.join(Organization).where(*filters)

    result = await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    items = [_to_list_item(d) for d in result.scalars().unique().all()]
    return DeviceListResponse(total=int(total), page=page, page_size=page_size, items=items)


async def revoke_device(db: AsyncSession, device_id: str) -> RevokeResponse:
    row = await db.execute(select(Device).where(Device.device_id == device_id))
    device = row.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    now_iso = utc_now_iso()
    await db.execute(
        update(DeviceCredential)
        .where(DeviceCredential.device_id == device.id)
        .where(DeviceCredential.revoked_at.is_(None))
        .values(revoked_at=now_iso)
    )
    device.updated_at = now_iso
    await db.commit()
    return RevokeResponse(device_id=device.device_id, revoked=True)


def _to_list_item(device: Device) -> DeviceListItem:
    active = [c for c in device.credentials if c.revoked_at is None]
    active.sort(key=lambda c: c.created_at, reverse=True)
    current = active[0] if active else None
    return DeviceListItem(
        device_id=device.device_id,
        org_id=device.organization.org_id if device.organization else "",
        team_id=device.team.team_id if device.team else "",
        user_id=device.user_id or "",
        platform=device.platform or "",
        ver=device.ver or "",
        last_seen_at=device.last_seen_at,
        created_at=device.created_at,
        credential_expires_at=current.expires_at if current else None,
        revoked=current is None and len(device.credentials) > 0,
    )


async def _get_or_create_org(db: AsyncSession, org_id: str, name: str) -> Organization:
    row = await db.execute(select(Organization).where(Organization.org_id == org_id))
    org = row.scalar_one_or_none()
    if org is not None:
        return org
    org = Organization(org_id=org_id, name=name or org_id, created_at=utc_now_iso())
    db.add(org)
    await db.flush()
    return org


async def _get_or_create_team(
    db: AsyncSession,
    org: Organization,
    team_id: str,
    name: str,
) -> Team:
    row = await db.execute(
        select(Team).where(Team.organization_id == org.id, Team.team_id == team_id)
    )
    team = row.scalar_one_or_none()
    if team is not None:
        return team
    team = Team(
        organization_id=org.id,
        team_id=team_id,
        name=name or team_id,
        created_at=utc_now_iso(),
    )
    db.add(team)
    await db.flush()
    return team
