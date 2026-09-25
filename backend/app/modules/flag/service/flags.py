"""flag 下发与管理台 CRUD。

下发（`serve_flags`）与管理（`create_flag` / `update_flag` / `delete_flag`）在同一个
文件里，是为了让「写进去的和发出去的是同一份口径」一眼可见 —— 尤其是 disabled 的
flag 不进下发这条，分两个文件写容易一边改一边忘。

失败语义（规划 §6）：`GET /ctl/flags` 是 fail-open。客户端 `refreshFromRemote` 整个
包在 `try {} catch {}` 里，非 2xx 直接 `return`，且有磁盘缓存兜底 —— 所以本端点出错
不会阻塞开发者工作。正因为 fail-open，它只能承载「施加约束」类开关（门禁见 guard.py）。
"""

import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import current_actor, current_request_id, get_logger
from app.core.timeutil import utc_now_iso
from app.modules.flag.model import FeatureFlag, FeatureFlagAudit
from app.modules.flag.schemas import (
    FlagAuditItem,
    FlagAuditListResponse,
    FlagCreate,
    FlagDeleteResponse,
    FlagItem,
    FlagListResponse,
    FlagUpsert,
)
from app.modules.flag.service.guard import validate_description, validate_key

logger = get_logger("agent.flag")


def _dumps(value: Any) -> str:
    """存库用紧凑 JSON。ensure_ascii=False 让中文描述在库里可读。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: Optional[str]) -> Any:
    """出库还原原生 JSON 类型。

    库里的值理论上都是我们自己 `_dumps` 写进去的，但**坏数据不能让整个下发端点 500**：
    下发是 fail-open 通道，一条脏行把 200 变成 500，等于所有客户端拿不到任何 flag。
    所以解析失败时把原文当字符串返回 —— 客户端拿到 string 而不是 bool，
    它自己的 `=== true` 判断会走默认分支，退化是安全方向。
    """
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


# ---------------------------------------------------------------------------
# 下发（无认证，客户端读）
# ---------------------------------------------------------------------------
async def serve_flags(db: AsyncSession) -> dict[str, Any]:
    """返回扁平 JSON：`{key: value}`。

    契约（`feature-flags.ts::refreshFromRemote`）：
    - 顶层就是 flag 字典，**不能包裹在 `{"flags": ...}` 里** —— 客户端直接
      `Object.entries(payload)`，包一层会让每个 flag 名变成 "flags"；
    - 客户端 `remoteValues.clear()` 后全量重填，所以本响应必须是**全量**，
      不能是增量 —— 少发一个 key 就等于告诉客户端「这个 flag 被删了，回落默认值」；
    - disabled 的 flag 不出现在响应里，正是利用上面这条语义做「关掉但不删」。
    """
    rows = await db.execute(
        select(FeatureFlag)
        .where(FeatureFlag.disabled_at.is_(None))
        .order_by(FeatureFlag.key.asc())
    )
    return {flag.key: _loads(flag.value_json) for flag in rows.scalars().all()}


# ---------------------------------------------------------------------------
# 管理台 CRUD（cookie 会话）
# ---------------------------------------------------------------------------
async def list_flags(db: AsyncSession) -> FlagListResponse:
    rows = await db.execute(select(FeatureFlag).order_by(FeatureFlag.key.asc()))
    return FlagListResponse(items=[_to_item(f) for f in rows.scalars().all()])


async def create_flag(db: AsyncSession, payload: FlagCreate, actor: str) -> FlagItem:
    key = validate_key(payload.key)
    description = validate_description(payload.description)

    existing = await _find(db, key)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"flag {key!r} 已存在")

    now_iso = utc_now_iso()
    value_json = _dumps(payload.value)
    flag = FeatureFlag(
        key=key,
        value_json=value_json,
        description=description,
        created_at=now_iso,
        updated_at=now_iso,
        updated_by=actor,
    )
    db.add(flag)
    await db.flush()
    _audit(
        db,
        flag_id=flag.id,
        key=key,
        action="create",
        old_value_json=None,
        new_value_json=value_json,
        reason=payload.reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("create", key)
    return _to_item(flag)


async def update_flag(db: AsyncSession, key: str, payload: FlagUpsert, actor: str) -> FlagItem:
    # key 来自 URL 路径，同样过门禁 —— 否则 PUT /flags/disable_sandbox 能绕过创建时的校验
    # 去改一条（历史遗留的）违规 flag
    validate_key(key)
    description = validate_description(payload.description)

    flag = await _require(db, key)
    now_iso = utc_now_iso()
    old_value_json = flag.value_json
    new_value_json = _dumps(payload.value)

    flag.value_json = new_value_json
    flag.description = description
    flag.updated_at = now_iso
    flag.updated_by = actor
    _audit(
        db,
        flag_id=flag.id,
        key=key,
        action="update",
        old_value_json=old_value_json,
        new_value_json=new_value_json,
        reason=payload.reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("update", key)
    return _to_item(flag)


async def set_flag_enabled(
    db: AsyncSession,
    key: str,
    enabled: bool,
    actor: str,
    reason: str = "",
) -> FlagItem:
    """启用 / 停用。停用 = 不进下发 = 客户端按「远程已删除」回落默认值。"""
    validate_key(key)
    flag = await _require(db, key)
    now_iso = utc_now_iso()
    flag.disabled_at = None if enabled else now_iso
    flag.updated_at = now_iso
    flag.updated_by = actor
    _audit(
        db,
        flag_id=flag.id,
        key=key,
        action="enable" if enabled else "disable",
        old_value_json=flag.value_json,
        new_value_json=flag.value_json,
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    await db.commit()
    _admin_write("enable" if enabled else "disable", key)
    return _to_item(flag)


async def delete_flag(db: AsyncSession, key: str, actor: str, reason: str = "") -> FlagDeleteResponse:
    """真删。审计行留下（flag_id 走 SET NULL），key 文本副本保住「删了哪个」。

    为什么这里是真删而不是软删除：软删除已经由 `disabled_at` 承担了
    （「关掉但不删」是常态操作）。真删是「这个 flag 彻底不要了」，
    留一行 disabled 记录只会让 flag 列表越来越长。审计里有全过程，可追溯。
    """
    validate_key(key)
    flag = await _require(db, key)
    now_iso = utc_now_iso()
    _audit(
        db,
        flag_id=flag.id,
        key=key,
        action="delete",
        old_value_json=flag.value_json,
        new_value_json=None,
        reason=reason,
        actor=actor,
        now_iso=now_iso,
    )
    # 先 flush 审计行再删 —— 否则同一事务里 SET NULL 的时机依赖 flush 顺序
    await db.flush()
    await db.delete(flag)
    await db.commit()
    _admin_write("delete", key)
    return FlagDeleteResponse(key=key, deleted=True)


async def list_audit(db: AsyncSession, key: Optional[str] = None, limit: int = 100) -> FlagAuditListResponse:
    stmt = select(FeatureFlagAudit).order_by(FeatureFlagAudit.id.desc()).limit(limit)
    if key:
        stmt = stmt.where(FeatureFlagAudit.key == key)
    rows = await db.execute(stmt)
    return FlagAuditListResponse(
        items=[
            FlagAuditItem(
                id=a.id,
                key=a.key,
                action=a.action,
                old_value=_loads(a.old_value_json),
                new_value=_loads(a.new_value_json),
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
    flag_id: Optional[int],
    key: str,
    action: str,
    old_value_json: Optional[str],
    new_value_json: Optional[str],
    reason: str,
    actor: str,
    now_iso: str,
) -> None:
    db.add(
        FeatureFlagAudit(
            flag_id=flag_id,
            key=key,
            action=action,
            old_value_json=old_value_json,
            new_value_json=new_value_json,
            reason=reason or "",
            actor=actor or "",
            created_at=now_iso,
            # 取不到就留空。不为没有请求的写入编造编号（方案 §3.9）。
            request_id=current_request_id(),
        )
    )


async def _find(db: AsyncSession, key: str) -> Optional[FeatureFlag]:
    row = await db.execute(select(FeatureFlag).where(FeatureFlag.key == key))
    return row.scalar_one_or_none()


async def _require(db: AsyncSession, key: str) -> FeatureFlag:
    flag = await _find(db, key)
    if flag is None:
        raise HTTPException(status_code=404, detail=f"flag {key!r} 不存在")
    return flag


def _to_item(flag: FeatureFlag) -> FlagItem:
    return FlagItem(
        key=flag.key,
        value=_loads(flag.value_json),
        description=flag.description,
        disabled=flag.disabled_at is not None,
        created_at=flag.created_at,
        updated_at=flag.updated_at,
        updated_by=flag.updated_by,
    )
