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

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core import db as db_mod
from app.core.config import settings
from app.core.logging import bind_context, db_error_fields, get_logger
from app.core.timeutil import is_expired, utc_now
from app.modules.identity.model import Device, DeviceCredential
from app.modules.identity.service.secrets import hash_secret

auth_logger = get_logger("agent.auth")
db_logger = get_logger("agent.db")


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
    token, reject_reason = _bearer_token(request)
    if reject_reason is not None:
        # 头缺失与头坏了分开。两者都还没有凭据可以查。
        _reject(reject_reason)
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
            _reject("unknown")
        if cred.revoked_at is not None:
            _reject("revoked")
        if is_expired(cred.expires_at):
            _reject("expired")

        device = cred.device
        if device is None or device.organization is None:
            # 凭据在，设备或组织没了。对外仍是 401，原因归查无此证。
            _reject("unknown")

        ctx = DeviceContext(
            device_id=device.device_id,
            org_id=device.organization.org_id,
            team_id=device.team.team_id if device.team is not None else "",
            user_id=device.user_id or "",
        )
        # 通过之后才写。鉴权失败的请求不带这两个字段，这是对的：
        # 失败靠 reason 而不是设备标识（方案 §3.4，已裁决）。
        bind_context(auth="device", device_id=ctx.device_id, org_id=ctx.org_id)

        now = utc_now()
        now_iso = now.isoformat()
        device.last_seen_at = now_iso
        device.updated_at = now_iso
        cred.last_used_at = now_iso
        cred.expires_at = (now + timedelta(days=settings.control_plane.CTL_CREDENTIAL_TTL_DAYS)).isoformat()
        try:
            await db.commit()
        except Exception as exc:
            # 提交失败不是鉴权失败，归 db（方案 §3.5）。栈按 §4.5 裁剪：
            # 数据库异常的消息里有语句和绑定参数，不能原样进 exc。
            fields, logged = db_error_fields(exc)
            db_logger.exception(
                "commit failed",
                logged,
                event="commit_failed",
                device_id=ctx.device_id,
                **fields,
            )
            await db.rollback()

        return ctx


def _bearer_token(request: Request) -> tuple[str, Optional[str]]:
    """解析 Authorization 头。

    返回 (token, reason)。reason 非空表示头不合格，token 为空串：
    头缺失是 missing_bearer，头在但不是 Bearer 或值为空是 malformed。
    两者对外都是 401，日志里分开——配错和没配是两种处置。
    """
    header = request.headers.get("Authorization", "")
    if not header:
        return "", "missing_bearer"
    prefix = "Bearer "
    if not header.startswith(prefix):
        return "", "malformed"
    token = header[len(prefix) :].strip()
    if not token:
        return "", "malformed"
    return token, None


def _reject(reason: str) -> None:
    """鉴权失败记一条 warning 后抛 401。reason 用枚举，不从异常消息取。"""
    auth_logger.warning("credential rejected", event="auth_rejected", reason=reason)
    raise HTTPException(status_code=401, detail="Unauthorized")
