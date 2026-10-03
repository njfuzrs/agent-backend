"""CLI 飞书登录（P2）在 identity 侧的落点：设备归属、签发凭据、连带吊销、登出解绑。

auth 模块只通过这里的函数改设备，不 import identity 的 model（边界测试 ③）。
本文件的函数都**不 commit**：调用方把它和登录审计放进同一个事务。
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.timeutil import iso_after, utc_now_iso
from app.modules.identity.model import Device, DeviceCredential, Organization
from app.modules.identity.service.secrets import hash_secret, mint_credential


@dataclass(frozen=True)
class IssuedCredential:
    credential: str
    expires_at: str
    device_id: str
    org_id: str
    team_id: str = ""


async def device_owner(db: AsyncSession, device_id: str) -> Optional[int]:
    """设备当前归属的 users.id。设备不存在或没绑人返回 None。"""
    row = await db.execute(select(Device.user_ref).where(Device.device_id == device_id))
    return row.scalar_one_or_none()


async def device_counts_by_user(db: AsyncSession) -> dict[int, int]:
    """users.id → 名下设备数。用户页展示用。"""
    rows = await db.execute(
        select(Device.user_ref, func.count(Device.id)).where(Device.user_ref.is_not(None)).group_by(Device.user_ref)
    )
    return {int(uid): int(n) for uid, n in rows.all()}


async def issue_login_credential(
    db: AsyncSession, *, device_id: str, user_ref: int, platform: str, ver: str
) -> IssuedCredential:
    """把设备绑定到 user_ref 并签发新凭据，吊销该设备旧凭据。

    「设备已绑给另一个在职用户」的 409 由调用方先判（它知道用户状态）。
    新设备落进 CTL_LOGIN_ORG_ID 组织（不存在就建）；老设备保留原组织与团队 ——
    登录回答的是「谁」，组织归属仍由管理员决定。
    """
    now_iso = utc_now_iso()
    row = await db.execute(
        select(Device)
        .options(selectinload(Device.organization), selectinload(Device.team))
        .where(Device.device_id == device_id)
    )
    device = row.scalar_one_or_none()
    if device is None:
        org = await _get_or_create_login_org(db, now_iso)
        device = Device(
            device_id=device_id,
            organization_id=org.id,
            user_id="",
            user_ref=user_ref,
            platform=platform,
            ver=ver,
            last_seen_at=now_iso,
            created_at=now_iso,
            updated_at=now_iso,
        )
        db.add(device)
        await db.flush()
        org_slug, team_slug = org.org_id, ""
    else:
        device.user_ref = user_ref
        if platform:
            device.platform = platform
        if ver:
            device.ver = ver
        device.updated_at = now_iso
        device.last_seen_at = now_iso
        org_slug = device.organization.org_id if device.organization else ""
        team_slug = device.team.team_id if device.team else ""

    await db.execute(
        update(DeviceCredential)
        .where(DeviceCredential.device_id == device.id)
        .where(DeviceCredential.revoked_at.is_(None))
        .values(revoked_at=now_iso)
    )
    plaintext = mint_credential()
    expires_at = iso_after(days=settings.control_plane.CTL_CREDENTIAL_TTL_DAYS)
    db.add(
        DeviceCredential(
            device_id=device.id,
            token_hash=hash_secret(plaintext),
            expires_at=expires_at,
            created_at=now_iso,
        )
    )
    await db.flush()
    return IssuedCredential(
        credential=plaintext, expires_at=expires_at, device_id=device_id, org_id=org_slug, team_id=team_slug
    )


async def revoke_user_devices(db: AsyncSession, user_ref: int) -> int:
    """吊销某人名下所有设备的有效凭据，返回吊销的凭据条数。

    只吊销凭据、不解绑：设备仍记着曾属于谁。被吊销的人不算「在职」，
    别人在这台设备上登录可以接手（见 auth 的 409 判定）。
    """
    now_iso = utc_now_iso()
    ids = select(Device.id).where(Device.user_ref == user_ref).scalar_subquery()
    result = await db.execute(
        update(DeviceCredential)
        .where(DeviceCredential.device_id.in_(ids))
        .where(DeviceCredential.revoked_at.is_(None))
        .values(revoked_at=now_iso)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def logout_device(db: AsyncSession, device_id: str) -> Optional[int]:
    """CLI 登出：吊销本设备全部凭据并解绑，返回解绑前的 user_ref。

    解绑之后换人登录同一台电脑不会撞 409。
    """
    row = await db.execute(select(Device).where(Device.device_id == device_id))
    device = row.scalar_one_or_none()
    if device is None:
        return None
    now_iso = utc_now_iso()
    previous = device.user_ref
    await db.execute(
        update(DeviceCredential)
        .where(DeviceCredential.device_id == device.id)
        .where(DeviceCredential.revoked_at.is_(None))
        .values(revoked_at=now_iso)
    )
    device.user_ref = None
    device.updated_at = now_iso
    await db.flush()
    return previous


async def _get_or_create_login_org(db: AsyncSession, now_iso: str) -> Organization:
    slug = settings.control_plane.CTL_LOGIN_ORG_ID
    row = await db.execute(select(Organization).where(Organization.org_id == slug))
    org = row.scalar_one_or_none()
    if org is not None:
        return org
    org = Organization(org_id=slug, name=slug, created_at=now_iso)
    db.add(org)
    await db.flush()
    return org
