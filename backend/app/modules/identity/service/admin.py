"""组织 / 团队 / 注册码 / 设备列表与归属。供管理台调用，不走控制面 Bearer。"""

import json
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import current_actor, current_request_id, get_logger
from app.core.timeutil import iso_after, utc_now_iso
from app.modules.identity.model import Device, DeviceCredential, EnrollCode, IdentityAudit, Organization, Team
from app.modules.identity.schemas import (
    DeviceListItem,
    DeviceListResponse,
    DeviceMove,
    DeviceMoveResponse,
    EnrollCodeCreate,
    EnrollCodeCreated,
    EnrollCodeItem,
    EnrollCodeListResponse,
    IdentityAuditItem,
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
from app.modules.identity.service.secrets import hash_secret, mint_enroll_code

logger = get_logger("agent.identity")


@dataclass(frozen=True)
class DeviceRef:
    """给其它模块用的设备摘要。不暴露 ORM，避免跨模块 import model。"""

    device_id: str
    org_id: str
    team_id: str = ""


async def get_organization(db: AsyncSession, org_id: str) -> Optional[OrganizationItem]:
    """按对外 slug 查组织。policy 创建时校验 scope 存在，只调这个，不 import model。"""
    row = await db.execute(select(Organization).where(Organization.org_id == org_id))
    org = row.scalar_one_or_none()
    if org is None:
        return None
    return OrganizationItem(org_id=org.org_id, name=org.name, created_at=org.created_at, device_count=0)


async def get_team(db: AsyncSession, org_id: str, team_id: str) -> Optional[str]:
    """组织内 slug。team_id 只在组织内唯一，必须带 org 收窄。"""
    row = await db.execute(
        select(Team)
        .join(Organization, Team.organization_id == Organization.id)
        .where(Organization.org_id == org_id, Team.team_id == team_id)
    )
    team = row.scalar_one_or_none()
    return team.team_id if team is not None else None


async def get_device(db: AsyncSession, device_id: str) -> Optional[DeviceRef]:
    """按对外 device_id 查，带当前所属 org/team。"""
    row = await db.execute(
        select(Device)
        .options(selectinload(Device.organization), selectinload(Device.team))
        .where(Device.device_id == device_id)
    )
    device = row.scalar_one_or_none()
    if device is None:
        return None
    return DeviceRef(
        device_id=device.device_id,
        org_id=device.organization.org_id if device.organization else "",
        team_id=device.team.team_id if device.team else "",
    )


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
    _audit(db, "org_create", "org", org.org_id, org.org_id, {"name": org.name}, payload.reason, now_iso)
    await db.commit()
    _admin_write("org_create", org.org_id)
    return OrganizationItem(org_id=org.org_id, name=org.name, created_at=org.created_at, device_count=0)


async def rename_organization(db: AsyncSession, org_id: str, payload: OrganizationRename) -> OrganizationItem:
    """只改展示名。org_id 是策略 / 预算 / 市场条目里的文本副本，不能改。"""
    org = await _require_org(db, org_id)
    now_iso = utc_now_iso()
    old = org.name
    org.name = payload.name
    _audit(db, "org_rename", "org", org.org_id, org.org_id, {"old_name": old, "new_name": org.name}, payload.reason, now_iso)
    await db.commit()
    _admin_write("org_rename", org.org_id)
    count = (await db.execute(select(func.count(Device.id)).where(Device.organization_id == org.id))).scalar_one()
    return OrganizationItem(org_id=org.org_id, name=org.name, created_at=org.created_at, device_count=int(count))


async def list_teams(db: AsyncSession, org_id: Optional[str] = None) -> TeamListResponse:
    count_row = (
        select(Device.team_id, func.count(Device.id).label("n"))
        .where(Device.team_id.is_not(None))
        .group_by(Device.team_id)
        .subquery()
    )
    stmt = (
        select(Team, Organization.org_id, func.coalesce(count_row.c.n, 0))
        .join(Organization, Team.organization_id == Organization.id)
        .outerjoin(count_row, count_row.c.team_id == Team.id)
        .order_by(Organization.org_id.asc(), Team.team_id.asc())
    )
    if org_id:
        stmt = stmt.where(Organization.org_id == org_id)
    result = await db.execute(stmt)
    items = [
        TeamItem(org_id=org_slug, team_id=team.team_id, name=team.name, created_at=team.created_at, device_count=int(n))
        for team, org_slug, n in result.all()
    ]
    return TeamListResponse(items=items)


async def create_team(db: AsyncSession, payload: TeamCreate) -> TeamItem:
    """在已存在的组织下建团队。组织不存在 → 404：不在这里顺带建组织，避免手滑造出孤儿组织。"""
    org = await _require_org(db, payload.org_id)
    existing = await db.execute(
        select(Team).where(Team.organization_id == org.id, Team.team_id == payload.team_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"团队 {payload.team_id!r} 在组织 {org.org_id!r} 下已存在")
    now_iso = utc_now_iso()
    team = Team(
        organization_id=org.id,
        team_id=payload.team_id,
        name=payload.name or payload.team_id,
        created_at=now_iso,
    )
    db.add(team)
    _audit(db, "team_create", "team", team.team_id, org.org_id, {"name": team.name}, payload.reason, now_iso)
    await db.commit()
    _admin_write("team_create", f"{org.org_id}/{team.team_id}")
    return TeamItem(org_id=org.org_id, team_id=team.team_id, name=team.name, created_at=team.created_at)


async def rename_team(db: AsyncSession, org_id: str, team_id: str, payload: TeamRename) -> TeamItem:
    """只改展示名，team_id 同样是策略 / 预算 / 市场的文本副本。"""
    org = await _require_org(db, org_id)
    team = await _require_team(db, org, team_id)
    now_iso = utc_now_iso()
    old = team.name
    team.name = payload.name
    _audit(db, "team_rename", "team", team.team_id, org.org_id, {"old_name": old, "new_name": team.name}, payload.reason, now_iso)
    await db.commit()
    _admin_write("team_rename", f"{org.org_id}/{team.team_id}")
    count = (await db.execute(select(func.count(Device.id)).where(Device.team_id == team.id))).scalar_one()
    return TeamItem(
        org_id=org.org_id, team_id=team.team_id, name=team.name, created_at=team.created_at, device_count=int(count)
    )


async def move_device(db: AsyncSession, device_id: str, payload: DeviceMove) -> DeviceMoveResponse:
    """改设备的组织 / 团队。飞书登录的设备同样可改（登录只回答「谁」，归属由管理员定）。

    不动凭据：require_device 每次请求都从库里现查 org / team，下一次控制面请求就是新值。
    再次 `sid-code login` 也保留这里设的归属（login.issue_login_credential 不改老设备的组织）。
    值没变时不写审计、返回 changed=false。
    """
    row = await db.execute(
        select(Device)
        .options(selectinload(Device.organization), selectinload(Device.team))
        .where(Device.device_id == device_id)
    )
    device = row.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    org = await _require_org(db, payload.org_id)
    team = await _require_team(db, org, payload.team_id) if payload.team_id else None

    old_org = device.organization.org_id if device.organization else ""
    old_team = device.team.team_id if device.team else ""
    new_team = team.team_id if team is not None else ""
    if old_org == org.org_id and old_team == new_team:
        return DeviceMoveResponse(device_id=device.device_id, org_id=org.org_id, team_id=new_team, changed=False)

    now_iso = utc_now_iso()
    device.organization_id = org.id
    device.team_id = team.id if team is not None else None
    device.updated_at = now_iso
    _audit(
        db,
        "device_move",
        "device",
        device.device_id,
        org.org_id,
        {"from": {"org_id": old_org, "team_id": old_team}, "to": {"org_id": org.org_id, "team_id": new_team}},
        payload.reason,
        now_iso,
    )
    await db.commit()
    _admin_write("device_move", device.device_id)
    return DeviceMoveResponse(device_id=device.device_id, org_id=org.org_id, team_id=new_team, changed=True)


async def list_audit(
    db: AsyncSession,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    org_id: Optional[str] = None,
    limit: int = 200,
) -> IdentityAuditListResponse:
    stmt = select(IdentityAudit).order_by(IdentityAudit.id.desc()).limit(limit)
    if target_type:
        stmt = stmt.where(IdentityAudit.target_type == target_type)
    if target_id:
        stmt = stmt.where(IdentityAudit.target_id == target_id)
    if org_id:
        stmt = stmt.where(IdentityAudit.org_id == org_id)
    rows = (await db.execute(stmt)).scalars().all()
    items = []
    for a in rows:
        try:
            detail = json.loads(a.detail_json or "{}")
        except ValueError:
            detail = {}
        items.append(
            IdentityAuditItem(
                id=a.id,
                action=a.action,
                target_type=a.target_type,
                target_id=a.target_id,
                org_id=a.org_id,
                detail=detail if isinstance(detail, dict) else {},
                reason=a.reason,
                actor=a.actor,
                created_at=a.created_at,
                request_id=a.request_id,
            )
        )
    return IdentityAuditListResponse(items=items)


def _audit(
    db: AsyncSession,
    action: str,
    target_type: str,
    target_id: str,
    org_id: str,
    detail: dict,
    reason: str,
    now_iso: str,
) -> None:
    """与业务写在同一事务里：要么都落，要么都不落。"""
    db.add(
        IdentityAudit(
            action=action,
            target_type=target_type,
            target_id=target_id,
            org_id=org_id,
            detail_json=json.dumps(detail, ensure_ascii=False, sort_keys=True),
            reason=reason or "",
            actor=current_actor(),
            created_at=now_iso,
            # 取不到就留空。不为没有请求的写入编造编号（方案 §3.9）。
            request_id=current_request_id(),
        )
    )


async def _require_org(db: AsyncSession, org_id: str) -> Organization:
    row = await db.execute(select(Organization).where(Organization.org_id == org_id))
    org = row.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail=f"组织 {org_id!r} 不存在")
    return org


async def _require_team(db: AsyncSession, org: Organization, team_id: str) -> Team:
    row = await db.execute(select(Team).where(Team.organization_id == org.id, Team.team_id == team_id))
    team = row.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail=f"团队 {team_id!r} 在组织 {org.org_id!r} 下不存在")
    return team


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
    org_existed = await _org_exists(db, payload.org_id)
    org = await _get_or_create_org(db, payload.org_id, payload.org_name)
    team = None
    team_existed = True
    if payload.team_id:
        team_existed = await _team_exists(db, org, payload.team_id)
        team = await _get_or_create_team(db, org, payload.team_id, payload.team_name)

    ttl_hours = payload.ttl_hours or settings.control_plane.CTL_ENROLL_CODE_TTL_HOURS
    now_iso = utc_now_iso()
    plaintext = mint_enroll_code()
    expires_at = iso_after(hours=ttl_hours)
    code = EnrollCode(
        code_hash=hash_secret(plaintext),
        organization_id=org.id,
        team_id=team.id if team is not None else None,
        expires_at=expires_at,
        created_at=now_iso,
        created_by=created_by,
        note=payload.note or "",
    )
    db.add(code)
    await db.flush()
    # 审计只记注册码的行号、归属与过期时间，明文与 hash 都不进审计。
    # 备注就是这次签发的理由（发给谁、哪台机器）；老调用方不带也能签。
    _audit(
        db,
        "enroll_code_issue",
        "enroll_code",
        str(code.id),
        org.org_id,
        {
            "team_id": team.team_id if team is not None else "",
            "expires_at": expires_at,
            "org_created": not org_existed,
            "team_created": not team_existed,
        },
        payload.note or "",
        now_iso,
    )
    await db.commit()
    # 明文码只在响应里出现一次，日志不记。target 用组织，不记码的任何片段。
    _admin_write("enroll_code", org.org_id)
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
    device_id: str | None = None,
) -> DeviceListResponse:
    filters = []
    if org_id:
        filters.append(Organization.org_id == org_id)
    if device_id:
        filters.append(Device.device_id == device_id)

    count_stmt = select(func.count(Device.id))
    if org_id:
        count_stmt = count_stmt.join(Organization)
    if filters:
        count_stmt = count_stmt.where(*filters)
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
        stmt = stmt.join(Organization)
    if filters:
        stmt = stmt.where(*filters)

    result = await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    items = [_to_list_item(d) for d in result.scalars().unique().all()]
    return DeviceListResponse(total=int(total), page=page, page_size=page_size, items=items)


async def revoke_device(db: AsyncSession, device_id: str, reason: str) -> RevokeResponse:
    """吊销设备全部有效凭据。理由必填，与吊销同一事务写 identity_audit。"""
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason 必填（会进审计）")
    row = await db.execute(
        select(Device)
        .options(selectinload(Device.organization), selectinload(Device.team))
        .where(Device.device_id == device_id)
    )
    device = row.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    now_iso = utc_now_iso()
    result = await db.execute(
        update(DeviceCredential)
        .where(DeviceCredential.device_id == device.id)
        .where(DeviceCredential.revoked_at.is_(None))
        .values(revoked_at=now_iso)
    )
    device.updated_at = now_iso
    _audit(
        db,
        "device_revoke",
        "device",
        device.device_id,
        device.organization.org_id if device.organization else "",
        {
            "team_id": device.team.team_id if device.team else "",
            "user_ref": device.user_ref,
            "revoked_credentials": int(result.rowcount or 0),
        },
        reason,
        now_iso,
    )
    await db.commit()
    _admin_write("revoke", device.device_id)
    return RevokeResponse(device_id=device.device_id, revoked=True)


def _admin_write(action: str, target_id: str) -> None:
    """管理台写操作完成一条。actor 从上下文取（方案 §3.6、§3.9）。

    业务审计在 identity_audit 表（组织 / 团队 / 改归属 / 签发注册码 / 吊销设备），
    这条日志只是运行记录。注册码明文不进日志，它只在响应里出现一次。
    """
    logger.info(
        "admin write",
        event="admin_write",
        action=action,
        target_id=target_id,
        actor=current_actor(),
    )


def _to_list_item(device: Device) -> DeviceListItem:
    active = [c for c in device.credentials if c.revoked_at is None]
    active.sort(key=lambda c: c.created_at, reverse=True)
    current = active[0] if active else None
    return DeviceListItem(
        device_id=device.device_id,
        org_id=device.organization.org_id if device.organization else "",
        team_id=device.team.team_id if device.team else "",
        user_id=device.user_id or "",
        user_ref=device.user_ref,
        platform=device.platform or "",
        ver=device.ver or "",
        last_seen_at=device.last_seen_at,
        created_at=device.created_at,
        credential_expires_at=current.expires_at if current else None,
        revoked=current is None and len(device.credentials) > 0,
    )


async def _org_exists(db: AsyncSession, org_id: str) -> bool:
    row = await db.execute(select(Organization.id).where(Organization.org_id == org_id))
    return row.scalar_one_or_none() is not None


async def _team_exists(db: AsyncSession, org: Organization, team_id: str) -> bool:
    row = await db.execute(select(Team.id).where(Team.organization_id == org.id, Team.team_id == team_id))
    return row.scalar_one_or_none() is not None


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
