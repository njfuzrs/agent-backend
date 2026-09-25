"""policy 求值、下发组装与管理台 CRUD。

下发（`evaluate` / `delivery_body`）与管理（create / update / disable）在同一个
文件里，是为了让「写进去的和发出去的是同一份口径」一眼可见 —— 尤其是
disabled 的策略不进下发、settings 不含 source 这两条，分两个文件写容易一边改一边忘。

失败语义（规划 §6）：`GET /ctl/policy` 是 fail-open。客户端拉不到（5xx / 超时 /
401）就回落磁盘缓存再回落本地 managed-settings，所以本端点出错不会阻塞开发者工作。
管理台写入是 fail-closed：`reason` 空、未知字段、空策略一律 422，不落库。

求值只使用 DeviceContext 三个字符串 + policies 表自己的列，**不 join identity 的表**
（跨模块直接查表会被边界测试打红）。team 匹配必须带 org_id：team_id 只在组织内
唯一，不带 org 会让跨 org 同名 team 串台。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext
from app.core.logging import current_actor, current_request_id, get_logger
from app.core.timeutil import utc_now_iso
from app.modules.identity.service import admin as identity_admin
from app.modules.policy.model import Policy, PolicyAudit
from app.modules.policy.schemas import (
    PolicyAuditItem,
    PolicyAuditListResponse,
    PolicyCreate,
    PolicyDeleteResponse,
    PolicyItem,
    PolicyListResponse,
    PolicyUpdate,
)
from app.modules.policy.service.guard import validate_settings

logger = get_logger("agent.policy")


def _dumps(value: Any) -> str:
    """存库用紧凑 JSON。ensure_ascii=False 让中文 reason 在库里可读。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: Optional[str]) -> Any:
    """出库还原。坏数据不能让整个下发端点 500（fail-open 通道）。"""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def delivery_body(policy: Policy) -> dict[str, Any]:
    """组装客户端 PolicySettings。source 写死 remote，剥掉库里误存的 source。"""
    settings = _loads(policy.settings_json)
    if not isinstance(settings, dict):
        settings = {}
    settings.pop("source", None)
    return {"source": "remote", **settings}


def etag_of(body: dict[str, Any]) -> str:
    """规范化 JSON 的 sha256 前 16 hex，带引号。sort_keys 会递归，嵌套 dict 也稳定。"""
    canonical = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


def admin_etag_of(policy: Policy) -> str:
    """管理台 item.etag：下发 body + disabled_at。停用 / 启用必须换 etag。

    下发路径的 ETag 仍只哈希 settings（disabled 行不进 evaluate，走 204）。
    管理台 etag 是给人看的信号，也防以后拿它当客户端缓存键踩坑。
    """
    body = delivery_body(policy)
    return etag_of({**body, "disabled_at": policy.disabled_at})


# ---------------------------------------------------------------------------
# 下发求值（device > team > org，不合并）
# ---------------------------------------------------------------------------
async def evaluate(db: AsyncSession, ctx: DeviceContext) -> Optional[Policy]:
    """按 device > team > org 返回至多一份 enabled 策略。不合并字段。

    team 匹配带 org_id：防止跨组织同名 team 串台。ctx.team_id 为空时跳过 team 层。
    """
    device = await _find_enabled(db, "device", ctx.device_id)
    if device is not None:
        return device
    if ctx.team_id:
        team = await _find_enabled(
            db,
            "team",
            ctx.team_id,
            org_id=ctx.org_id,
        )
        if team is not None:
            return team
    return await _find_enabled(db, "org", ctx.org_id, org_id=ctx.org_id)


async def evaluate_for_device(db: AsyncSession, device_id: str) -> Optional[Policy]:
    """管理台预览用。走 identity service 查设备，不 import identity.model。"""
    ref = await identity_admin.get_device(db, device_id)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"device {device_id!r} 不存在")
    ctx = DeviceContext(device_id=ref.device_id, org_id=ref.org_id, team_id=ref.team_id)
    return await evaluate(db, ctx)


# ---------------------------------------------------------------------------
# 管理台 CRUD（cookie 会话）
# ---------------------------------------------------------------------------
async def list_policies(
    db: AsyncSession,
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> PolicyListResponse:
    stmt = select(Policy).order_by(Policy.id.desc())
    if scope_type:
        stmt = stmt.where(Policy.scope_type == scope_type)
    if scope_id:
        stmt = stmt.where(Policy.scope_id == scope_id)
    if org_id:
        stmt = stmt.where(Policy.org_id == org_id)
    rows = await db.execute(stmt)
    return PolicyListResponse(items=[_to_item(p) for p in rows.scalars().all()])


async def get_policy(db: AsyncSession, policy_id: int) -> PolicyItem:
    return _to_item(await _require(db, policy_id))


async def create_policy(db: AsyncSession, payload: PolicyCreate, actor: str) -> PolicyItem:
    reason = _require_reason(payload.reason)
    settings = validate_settings(payload.settings.model_dump(exclude_none=True))
    scope_type = payload.scope_type
    scope_id = payload.scope_id.strip()
    org_id = payload.org_id.strip()
    if not scope_id or not org_id:
        raise HTTPException(status_code=422, detail="scope_id / org_id 不能为空")

    org_id = await _resolve_scope(db, scope_type, scope_id, org_id)

    existing = await _find_enabled(db, scope_type, scope_id, org_id=org_id if scope_type == "team" else None)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"同层已有生效策略（{scope_type}:{scope_id}）。改内容请更新那一条，或先停用再新建。",
        )

    now_iso = utc_now_iso()
    settings_json = _dumps(settings)
    policy = Policy(
        scope_type=scope_type,
        scope_id=scope_id,
        org_id=org_id,
        settings_json=settings_json,
        created_at=now_iso,
        updated_at=now_iso,
        updated_by=actor,
    )
    db.add(policy)
    await db.flush()
    _audit(
        db,
        policy_id=policy.id,
        scope_type=scope_type,
        scope_id=scope_id,
        action="create",
        old_settings_json=None,
        new_settings_json=settings_json,
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("create", str(policy.id))
    return _to_item(policy)


async def update_policy(db: AsyncSession, policy_id: int, payload: PolicyUpdate, actor: str) -> PolicyItem:
    reason = _require_reason(payload.reason)
    settings = validate_settings(payload.settings.model_dump(exclude_none=True))
    policy = await _require(db, policy_id)
    now_iso = utc_now_iso()
    old_settings_json = policy.settings_json
    new_settings_json = _dumps(settings)
    policy.settings_json = new_settings_json
    policy.updated_at = now_iso
    policy.updated_by = actor
    _audit(
        db,
        policy_id=policy.id,
        scope_type=policy.scope_type,
        scope_id=policy.scope_id,
        action="update",
        old_settings_json=old_settings_json,
        new_settings_json=new_settings_json,
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("update", str(policy.id))
    return _to_item(policy)


async def set_policy_enabled(
    db: AsyncSession,
    policy_id: int,
    enabled: bool,
    actor: str,
    reason: str,
) -> PolicyItem:
    reason = _require_reason(reason)
    policy = await _require(db, policy_id)
    if enabled:
        org_filter = policy.org_id if policy.scope_type == "team" else None
        existing = await _find_enabled(db, policy.scope_type, policy.scope_id, org_id=org_filter)
        if existing is not None and existing.id != policy.id:
            raise HTTPException(
                status_code=409,
                detail=f"同层已有生效策略（{policy.scope_type}:{policy.scope_id}）",
            )
    now_iso = utc_now_iso()
    policy.disabled_at = None if enabled else now_iso
    policy.updated_at = now_iso
    policy.updated_by = actor
    _audit(
        db,
        policy_id=policy.id,
        scope_type=policy.scope_type,
        scope_id=policy.scope_id,
        action="enable" if enabled else "disable",
        old_settings_json=policy.settings_json,
        new_settings_json=policy.settings_json,
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("enable" if enabled else "disable", str(policy.id))
    return _to_item(policy)


async def delete_policy(db: AsyncSession, policy_id: int, actor: str, reason: str) -> PolicyDeleteResponse:
    """真删。审计行留下（policy_id 走 SET NULL），scope 文本副本保住「删了哪一层」。"""
    reason = _require_reason(reason)
    policy = await _require(db, policy_id)
    now_iso = utc_now_iso()
    _audit(
        db,
        policy_id=policy.id,
        scope_type=policy.scope_type,
        scope_id=policy.scope_id,
        action="delete",
        old_settings_json=policy.settings_json,
        new_settings_json=None,
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.flush()
    await db.delete(policy)
    await db.commit()
    _admin_write("delete", str(policy_id))
    return PolicyDeleteResponse(id=policy_id, deleted=True)


async def list_audit(
    db: AsyncSession,
    policy_id: Optional[int] = None,
    limit: int = 100,
) -> PolicyAuditListResponse:
    stmt = select(PolicyAudit).order_by(PolicyAudit.id.desc()).limit(limit)
    if policy_id is not None:
        stmt = stmt.where(PolicyAudit.policy_id == policy_id)
    rows = await db.execute(stmt)
    return PolicyAuditListResponse(
        items=[
            PolicyAuditItem(
                id=a.id,
                policy_id=a.policy_id,
                scope_type=a.scope_type,
                scope_id=a.scope_id,
                action=a.action,
                old_settings=_loads(a.old_settings_json) if a.old_settings_json else None,
                new_settings=_loads(a.new_settings_json) if a.new_settings_json else None,
                reason=a.reason,
                actor=a.actor,
                created_at=a.created_at,
                request_id=a.request_id,
            )
            for a in rows.scalars().all()
        ]
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _require_reason(reason: str) -> str:
    text = (reason or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="reason 必填（会进审计；空策略变更无法追溯）")
    return text


async def _resolve_scope(db: AsyncSession, scope_type: str, scope_id: str, org_id: str) -> str:
    """校验 scope 存在。调 identity service，不 import identity.model。

    device 行用设备当前所属 org 覆盖客户端乱填，防止把设备策略写到别人的 org 名下。
    """
    if scope_type == "org":
        found = await identity_admin.get_organization(db, org_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"组织 {org_id!r} 不存在")
        if scope_id != org_id:
            raise HTTPException(status_code=422, detail="scope_type=org 时 scope_id 必须等于 org_id")
        return org_id
    if scope_type == "team":
        found = await identity_admin.get_team(db, org_id, scope_id)
        if found is None:
            raise HTTPException(
                status_code=404,
                detail=f"团队 {scope_id!r} 在组织 {org_id!r} 下不存在",
            )
        return org_id
    if scope_type == "device":
        ref = await identity_admin.get_device(db, scope_id)
        if ref is None:
            raise HTTPException(status_code=404, detail=f"设备 {scope_id!r} 不存在")
        return ref.org_id
    raise HTTPException(status_code=422, detail=f"未知 scope_type {scope_type!r}")


async def _find_enabled(
    db: AsyncSession,
    scope_type: str,
    scope_id: str,
    org_id: Optional[str] = None,
) -> Optional[Policy]:
    stmt = select(Policy).where(
        Policy.scope_type == scope_type,
        Policy.scope_id == scope_id,
        Policy.disabled_at.is_(None),
    )
    if org_id is not None:
        stmt = stmt.where(Policy.org_id == org_id)
    row = await db.execute(stmt)
    return row.scalar_one_or_none()


async def _require(db: AsyncSession, policy_id: int) -> Policy:
    row = await db.execute(select(Policy).where(Policy.id == policy_id))
    policy = row.scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=404, detail=f"policy {policy_id} 不存在")
    return policy


def _admin_write(action: str, target_id: str) -> None:
    """管理台写操作完成一条。actor 从上下文取，与审计行同一值（方案 §3.6、§3.9）。

    reason 不进日志，它在审计表里。提交失败时到不了这里，那一条归兜底。
    """
    logger.info(
        "admin write",
        event="admin_write",
        action=action,
        target_id=target_id,
        actor=current_actor(),
    )


def _audit(
    db: AsyncSession,
    *,
    policy_id: Optional[int],
    scope_type: str,
    scope_id: str,
    action: str,
    old_settings_json: Optional[str],
    new_settings_json: Optional[str],
    reason: str,
    actor: str,
    now_iso: str,
) -> None:
    db.add(
        PolicyAudit(
            policy_id=policy_id,
            scope_type=scope_type,
            scope_id=scope_id,
            action=action,
            old_settings_json=old_settings_json,
            new_settings_json=new_settings_json,
            reason=reason,
            actor=actor or "",
            created_at=now_iso,
            # 取不到就留空。不为没有请求的写入编造编号（方案 §3.9）。
            request_id=current_request_id(),
        )
    )


def to_evaluate_payload(policy: Policy) -> dict[str, Any]:
    """管理台 evaluate 响应。layer = 命中的 scope_type。"""
    item = _to_item(policy)
    return {
        "layer": policy.scope_type,
        "policy_id": policy.id,
        "scope_id": policy.scope_id,
        "org_id": policy.org_id,
        "settings": item.settings,
    }


def _to_item(policy: Policy) -> PolicyItem:
    body = delivery_body(policy)
    settings = {k: v for k, v in body.items() if k != "source"}
    return PolicyItem(
        id=policy.id,
        scope_type=policy.scope_type,
        scope_id=policy.scope_id,
        org_id=policy.org_id,
        settings=settings,
        disabled=policy.disabled_at is not None,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
        updated_by=policy.updated_by,
        etag=admin_etag_of(policy),
    )
