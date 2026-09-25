"""设备注册（fail-closed）。一次性码把「谁能加入组织」留在管理员手里。"""

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.timeutil import is_expired, iso_after, utc_now_iso
from app.modules.identity.model import Device, DeviceCredential, EnrollCode, Organization, Team
from app.modules.identity.schemas import EnrollRequest, EnrollResponse
from app.modules.identity.service.secrets import hash_secret, mint_credential

logger = get_logger("agent.identity")


async def enroll_device(db: AsyncSession, raw_token: str, payload: EnrollRequest) -> EnrollResponse:
    """消费一次性注册码并签发设备凭据。

    失败语义：fail-closed（规划 §6）。任何校验失败都是 401，不区分「码不存在 /
    已用过 / 过期」，避免枚举。开关关闭时走 403，方便运维判断是没开注册还是码错了。
    """
    if not settings.control_plane.CTL_ENROLL_ENABLED:
        raise HTTPException(status_code=403, detail="enroll is disabled")

    token = (raw_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid enroll token")

    now_iso = utc_now_iso()
    code_hash = hash_secret(token)
    row = await db.execute(select(EnrollCode).where(EnrollCode.code_hash == code_hash))
    code = row.scalar_one_or_none()
    if (
        code is None
        or code.used_at is not None
        or is_expired(code.expires_at)
    ):
        raise HTTPException(status_code=401, detail="Invalid enroll token")

    org = await db.get(Organization, code.organization_id)
    if org is None:
        raise HTTPException(status_code=401, detail="Invalid enroll token")

    # 请求里带了 org_id 就必须对得上码绑定的组织，对不上当码无效处理
    if payload.org_id and payload.org_id != org.org_id:
        raise HTTPException(status_code=401, detail="Invalid enroll token")

    team = await _resolve_team(db, org, code, payload.team_id, now_iso)

    consumed = await db.execute(
        update(EnrollCode)
        .where(EnrollCode.id == code.id)
        .where(EnrollCode.used_at.is_(None))
        .values(used_at=now_iso)
    )
    if consumed.rowcount != 1:
        raise HTTPException(status_code=401, detail="Invalid enroll token")

    device = await _upsert_device(
        db,
        payload=payload,
        org=org,
        team=team,
        now_iso=now_iso,
    )

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
    await db.commit()

    # 成功才记。失败走路由层的 agent.auth（reason=enroll_rejected），不在这里记。
    # 不记码、不记凭据明文：这两样都是密钥，日志只留设备与组织。
    logger.info(
        "device enrolled",
        event="device_enrolled",
        device_id=device.device_id,
        org_id=org.org_id,
    )

    return EnrollResponse(
        credential=plaintext,
        expires_at=expires_at,
        device_id=device.device_id,
        org_id=org.org_id,
        team_id=team.team_id if team is not None else "",
    )


async def _resolve_team(
    db: AsyncSession,
    org: Organization,
    code: EnrollCode,
    requested_team_id: str | None,
    now_iso: str,
) -> Team | None:
    bound = await db.get(Team, code.team_id) if code.team_id is not None else None
    if requested_team_id:
        if bound is not None and bound.team_id != requested_team_id:
            raise HTTPException(status_code=401, detail="Invalid enroll token")
        return await _get_or_create_team(db, org, requested_team_id, now_iso)
    return bound


async def _get_or_create_team(
    db: AsyncSession,
    org: Organization,
    team_slug: str,
    now_iso: str,
) -> Team:
    row = await db.execute(
        select(Team).where(Team.organization_id == org.id, Team.team_id == team_slug)
    )
    team = row.scalar_one_or_none()
    if team is not None:
        return team
    team = Team(
        organization_id=org.id,
        team_id=team_slug,
        name=team_slug,
        created_at=now_iso,
    )
    db.add(team)
    await db.flush()
    return team


async def _upsert_device(
    db: AsyncSession,
    payload: EnrollRequest,
    org: Organization,
    team: Team | None,
    now_iso: str,
) -> Device:
    row = await db.execute(select(Device).where(Device.device_id == payload.device_id))
    device = row.scalar_one_or_none()
    if device is None:
        device = Device(
            device_id=payload.device_id,
            organization_id=org.id,
            team_id=team.id if team is not None else None,
            user_id=payload.user_id or "",
            platform=payload.platform or "",
            ver=payload.ver or "",
            last_seen_at=now_iso,
            created_at=now_iso,
            updated_at=now_iso,
        )
        db.add(device)
        await db.flush()
        return device

    if device.organization_id != org.id:
        raise HTTPException(status_code=409, detail="device already enrolled in another org")

    device.team_id = team.id if team is not None else device.team_id
    if payload.user_id:
        device.user_id = payload.user_id
    if payload.platform:
        device.platform = payload.platform
    if payload.ver:
        device.ver = payload.ver
    device.updated_at = now_iso
    device.last_seen_at = now_iso
    await db.flush()
    return device
