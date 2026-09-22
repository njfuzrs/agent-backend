"""控制面鉴权（Control Plane）。

规划 §2.1 的结构决策：控制面与数据面**同仓、同部署，但鉴权与失败语义完全隔离**。

为什么必须隔离而不是「统一鉴权」：两个平面的最坏后果不在一个量级 ——
- 数据面被打穿 = 数据泄漏 / 脏数据；
- 控制面被打穿 = 攻击者获得全体客户端的远程配置权（能下发 `disableAllHooks` /
  `disableBypassPermissionsMode`，等于关掉全公司客户端的护栏）。

统一鉴权意味着两者取较弱的那一档，而现状较弱的那一档是「一个写在部署文档里的共享 token」。

⚠️ 本模块**禁止 import app.core.auth.data_plane 的任何符号**。
   这条由 tests/test_boundaries.py::test_control_plane_never_imports_data_plane_auth 机械化。

M1：查 device_credentials.token_hash → 未吊销未过期 → DeviceContext，并滑动续期。
失败语义：无凭据 / 无效 / 过期 / 已吊销一律 401（fail-closed）。
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import db as db_mod
from app.core.config import settings
from app.core.timeutil import is_expired, utc_now
from app.modules.identity.model import Device, DeviceCredential
from app.modules.identity.service.secrets import hash_secret

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class DeviceContext:
    """控制面调用方身份。由 identity 模块签发的设备凭据解析得出。

    字段形状取自规划 §2.1「哪台设备、属于哪个组织、能读哪份策略」。
    """

    device_id: str
    org_id: str
    team_id: str = ""
    user_id: str = ""


async def require_device(request: Request) -> DeviceContext:
    """控制面鉴权依赖。Authorization: Bearer <设备凭据>。

    查 hash → 未吊销未过期 → 返回 DeviceContext → 更新 last_seen_at 并滑动续期。
    比较用 secrets.compare_digest。签名（返回 DeviceContext）相对 M0 桩保持不变。

    用独立短会话，不占用请求级 get_db：后续控制面写库不会撞上「鉴权已经 commit」。
    last_seen_at 写失败不影响鉴权本身（心跳不是授予信任）。
    """
    token = _bearer_token(request)
    token_hash = hash_secret(token)

    async with db_mod.async_session() as db:
        row = await db.execute(
            select(DeviceCredential)
            .options(
                selectinload(DeviceCredential.device).selectinload(Device.organization),
                selectinload(DeviceCredential.device).selectinload(Device.team),
            )
            .where(DeviceCredential.token_hash == token_hash)
        )
        cred = row.scalar_one_or_none()

        dummy = "0" * 64
        stored = cred.token_hash if cred is not None else dummy
        if not secrets.compare_digest(stored, token_hash) or cred is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if cred.revoked_at is not None or is_expired(cred.expires_at):
            raise HTTPException(status_code=401, detail="Unauthorized")

        device = cred.device
        if device is None or device.organization is None:
            raise HTTPException(status_code=401, detail="Unauthorized")

        ctx = DeviceContext(
            device_id=device.device_id,
            org_id=device.organization.org_id,
            team_id=device.team.team_id if device.team is not None else "",
            user_id=device.user_id or "",
        )

        now = utc_now()
        now_iso = now.isoformat()
        device.last_seen_at = now_iso
        device.updated_at = now_iso
        cred.last_used_at = now_iso
        cred.expires_at = (now + timedelta(days=settings.control_plane.CTL_CREDENTIAL_TTL_DAYS)).isoformat()
        try:
            await db.commit()
        except Exception:
            logger.exception("更新设备 last_seen_at 失败")
            await db.rollback()

        return ctx


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = header[len(prefix) :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return token
