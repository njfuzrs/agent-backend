"""市场目录：管理台 CRUD / 上传 / 发布 / 下架，设备端 index 与制品下载（方案 §5.5）。

管理与下发放在同一个文件里，理由同 policy：「写进去的」和「发出去的」口径一眼可见 ——
只有 published 进 index，可见范围按设备的 org / team 收窄，制品下发前再算一次 sha256。

可见范围求值只用 DeviceContext 的 org_id / team_id 字符串 + market_items 自己的列，
**不 join identity 的表**。team 匹配必须带 org：team_id 只在组织内唯一。
看不见的插件对设备一律 404，不区分「不存在」和「没权限」—— 不给枚举目录留口子。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext
from app.core.logging import current_actor, current_request_id, get_logger
from app.core.timeutil import utc_now_iso
from app.modules.identity.service import admin as identity_admin
from app.modules.marketplace.model import (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    STATUS_YANKED,
    MarketAudit,
    MarketDownload,
    MarketItem,
    MarketVersion,
)
from app.modules.marketplace.schemas import (
    MarketAuditItem,
    MarketAuditListResponse,
    MarketDownloadItem,
    MarketDownloadListResponse,
    MarketDownloadStatsItem,
    MarketDownloadStatsResponse,
    MarketItemCreate,
    MarketItemListResponse,
    MarketItemOut,
    MarketItemUpdate,
    MarketVersionItem,
)
from app.modules.marketplace.service import package as pkg

# 复用轨迹模块的存储后端（OSS / 本地），只走 service 层。按模块属性取，测试才能换成临时目录。
from app.modules.trajectory.service import storage as storage_mod

logger = get_logger("agent.marketplace")

# 市场名。客户端 `/plugin install <name>@company`、installed.json 的 source = "market:company"
MARKET_NAME = "company"
STORAGE_PREFIX = "marketplace"
INDEX_SCHEMA_VERSION = 1


class ArtifactIntegrityError(Exception):
    """存储里的制品与库里的 sha256 对不上。fail-closed：不下发。"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(raw: Optional[str]) -> Any:
    try:
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def storage_key_for(name: str, version: str, sha256: str) -> str:
    """制品 key 带 sha 前缀：同名同版本的两次并发上传不会互相覆盖对方的文件。"""
    return f"{STORAGE_PREFIX}/{name}/{version}-{sha256[:16]}.tar.gz"


# ---------------------------------------------------------------------------
# 管理台
# ---------------------------------------------------------------------------
async def list_items(db: AsyncSession, *, org_id: Optional[str] = None) -> MarketItemListResponse:
    stmt = select(MarketItem).order_by(MarketItem.name.asc())
    if org_id:
        stmt = stmt.where(MarketItem.org_id == org_id)
    items = (await db.execute(stmt)).scalars().all()
    versions = await _versions_by_item(db, [i.id for i in items])
    counts = await _download_counts(db, [i.name for i in items])
    return MarketItemListResponse(
        items=[_to_item(i, versions.get(i.id, []), counts.get(i.name, 0)) for i in items]
    )


async def get_item(db: AsyncSession, name: str) -> MarketItemOut:
    item = await _require_item(db, name)
    versions = await _versions_by_item(db, [item.id])
    counts = await _download_counts(db, [item.name])
    return _to_item(item, versions.get(item.id, []), counts.get(item.name, 0))


async def create_item(db: AsyncSession, payload: MarketItemCreate, actor: str) -> MarketItemOut:
    if not pkg.is_valid_name(payload.name):
        raise HTTPException(status_code=422, detail="name 必须是 slug（小写字母、数字、-、_，以字母或数字开头，≤64）")
    await _check_scope(db, payload.org_id, payload.team_id)
    now = utc_now_iso()
    item = MarketItem(
        name=payload.name,
        kind=payload.kind,
        description=payload.description,
        maintainer=payload.maintainer,
        org_id=payload.org_id,
        team_id=payload.team_id,
        created_at=now,
        updated_at=now,
        created_by=actor or "",
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"插件 {payload.name!r} 已存在") from None
    _audit(db, item=item, action="create", actor=actor, now=now,
           detail={"org_id": item.org_id, "team_id": item.team_id, "kind": item.kind})
    await db.commit()
    _admin_write("create", item.name)
    return _to_item(item, [], 0)


async def update_item(db: AsyncSession, name: str, payload: MarketItemUpdate, actor: str) -> MarketItemOut:
    item = await _require_item(db, name)
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="没有要修改的字段")
    org_id = changes.get("org_id", item.org_id)
    team_id = changes.get("team_id", item.team_id)
    if "org_id" in changes or "team_id" in changes:
        await _check_scope(db, org_id, team_id or "")
    before = {k: getattr(item, k) for k in changes}
    for key, value in changes.items():
        setattr(item, key, value if value is not None else "")
    now = utc_now_iso()
    item.updated_at = now
    _audit(db, item=item, action="update", actor=actor, now=now,
           detail={"before": before, "after": {k: getattr(item, k) for k in changes}})
    await db.commit()
    _admin_write("update", item.name)
    return await get_item(db, name)


async def upload_version(db: AsyncSession, name: str, content: bytes, actor: str) -> MarketVersionItem:
    """上传一个版本：校验包 → 存制品 → 落 draft。发布是另一步，要写理由。"""
    item = await _require_item(db, name)
    try:
        validated = pkg.inspect_package(content)
    except pkg.PackageError as exc:
        raise HTTPException(status_code=422, detail={"message": "插件包校验失败", "errors": exc.errors}) from None
    if validated.name != item.name:
        raise HTTPException(
            status_code=422,
            detail=f"包里 plugin.json 的 name 是 {validated.name!r}，与目录条目 {item.name!r} 不一致",
        )
    existing = await db.execute(
        select(MarketVersion.id).where(MarketVersion.item_id == item.id, MarketVersion.version == validated.version)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"{item.name}@{validated.version} 已存在。版本不可覆盖，请升版本号")

    key = storage_key_for(item.name, validated.version, validated.sha256)
    # 先写制品再落库：库里有行就一定有文件。反过来失败只会留下一个没人引用的对象，无害。
    storage_mod.storage.put(key, content)

    now = utc_now_iso()
    version = MarketVersion(
        item_id=item.id,
        version=validated.version,
        manifest_json=_dumps(validated.manifest),
        components_json=_dumps(validated.components),
        sha256=validated.sha256,
        size_bytes=validated.size_bytes,
        storage_key=key,
        status=STATUS_DRAFT,
        created_at=now,
        created_by=actor or "",
    )
    db.add(version)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"{item.name}@{validated.version} 已存在") from None
    _audit(db, item=item, version=validated.version, action="upload", actor=actor, now=now,
           detail={"sha256": validated.sha256, "size_bytes": validated.size_bytes})
    await db.commit()
    _admin_write("upload", f"{item.name}@{validated.version}")
    return _to_version(version)


async def publish_version(db: AsyncSession, name: str, version: str, actor: str, reason: str) -> MarketVersionItem:
    """draft → published。已发布 / 已下架的不能再发布（下架是终态，要恢复就升版本号重传）。"""
    item = await _require_item(db, name)
    row = await _require_version(db, item, version)
    if row.status != STATUS_DRAFT:
        raise HTTPException(status_code=409, detail=f"{name}@{version} 当前是 {row.status}，只有 draft 能发布")
    # 发布前确认制品还在、哈希还对：发出去之后才发现坏包，所有人装都会失败
    try:
        _read_verified(row)
    except (FileNotFoundError, ArtifactIntegrityError):
        raise HTTPException(status_code=409, detail="制品缺失或与登记的 sha256 不一致，不能发布") from None
    now = utc_now_iso()
    row.status = STATUS_PUBLISHED
    row.published_at = now
    row.published_by = actor or ""
    item.updated_at = now
    _audit(db, item=item, version=version, action="publish", actor=actor, now=now, reason=_require_reason(reason),
           detail={"sha256": row.sha256})
    await db.commit()
    _admin_write("publish", f"{name}@{version}")
    return _to_version(row)


async def yank_version(db: AsyncSession, name: str, version: str, actor: str, reason: str) -> MarketVersionItem:
    """下架：不再进 index、不能再下载。已装的客户端照常用（fail-static，方案 §6.2）。"""
    item = await _require_item(db, name)
    row = await _require_version(db, item, version)
    if row.status == STATUS_YANKED:
        raise HTTPException(status_code=409, detail=f"{name}@{version} 已经下架")
    now = utc_now_iso()
    old_status = row.status
    row.status = STATUS_YANKED
    row.yanked_at = now
    item.updated_at = now
    _audit(db, item=item, version=version, action="yank", actor=actor, now=now, reason=_require_reason(reason),
           detail={"from": old_status})
    await db.commit()
    _admin_write("yank", f"{name}@{version}")
    return _to_version(row)


async def list_audit(db: AsyncSession, *, name: Optional[str] = None, limit: int = 100) -> MarketAuditListResponse:
    stmt = select(MarketAudit).order_by(MarketAudit.id.desc()).limit(limit)
    if name:
        stmt = stmt.where(MarketAudit.item_name == name)
    rows = (await db.execute(stmt)).scalars().all()
    return MarketAuditListResponse(
        items=[
            MarketAuditItem(
                id=r.id, item_id=r.item_id, item_name=r.item_name, version=r.version, action=r.action,
                reason=r.reason, actor=r.actor, detail=_loads(r.detail_json), created_at=r.created_at,
                request_id=r.request_id,
            )
            for r in rows
        ]
    )


async def list_downloads(
    db: AsyncSession,
    *,
    name: Optional[str] = None,
    user_ref: Optional[int] = None,
    device_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
) -> MarketDownloadListResponse:
    stmt = select(MarketDownload).order_by(MarketDownload.id.desc()).limit(limit)
    if name:
        stmt = stmt.where(MarketDownload.item_name == name)
    if user_ref is not None:
        stmt = stmt.where(MarketDownload.user_ref == user_ref)
    if device_id:
        stmt = stmt.where(MarketDownload.device_id == device_id)
    if since:
        stmt = stmt.where(MarketDownload.created_at >= since)
    rows = (await db.execute(stmt)).scalars().all()
    return MarketDownloadListResponse(
        items=[
            MarketDownloadItem(
                id=r.id, created_at=r.created_at, item_name=r.item_name, version=r.version,
                device_id=r.device_id, org_id=r.org_id, user_ref=r.user_ref,
            )
            for r in rows
        ]
    )


async def download_stats(db: AsyncSession, *, since: str) -> MarketDownloadStatsResponse:
    """按插件 × 人聚合下载。user_ref 为 NULL 的设备合成一行（未登录）。"""
    stmt = (
        select(
            MarketDownload.item_name,
            MarketDownload.user_ref,
            func.count(func.distinct(MarketDownload.device_id)).label("devices"),
            func.count().label("downloads"),
            func.max(MarketDownload.created_at).label("last_at"),
        )
        .where(MarketDownload.created_at >= since)
        .group_by(MarketDownload.item_name, MarketDownload.user_ref)
        .order_by(MarketDownload.item_name.asc(), func.count().desc())
    )
    rows = (await db.execute(stmt)).all()
    return MarketDownloadStatsResponse(
        since=since,
        items=[
            MarketDownloadStatsItem(
                item_name=r.item_name, user_ref=r.user_ref, devices=int(r.devices),
                downloads=int(r.downloads), last_at=r.last_at,
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# 设备端（只读）
# ---------------------------------------------------------------------------
def _visible_to(ctx: DeviceContext):
    """可见范围：同 org，且条目不限 team 或 team 相同。team 比较隐含在同 org 之内。"""
    return (MarketItem.org_id == ctx.org_id) & or_(
        MarketItem.team_id == "", MarketItem.team_id == (ctx.team_id or "\x00")
    )


async def device_index(db: AsyncSession, ctx: DeviceContext) -> dict[str, Any]:
    """设备可见的目录。每个插件给全部已发布版本，`version` 是其中 semver 最大的那个。

    body 里**没有时间戳**：ETag 是 body 的哈希，带上生成时间就永远不会 304。
    """
    rows = (
        await db.execute(
            select(MarketItem, MarketVersion)
            .join(MarketVersion, MarketVersion.item_id == MarketItem.id)
            .where(_visible_to(ctx), MarketVersion.status == STATUS_PUBLISHED)
        )
    ).all()
    grouped: dict[str, tuple[MarketItem, list[MarketVersion]]] = {}
    for item, version in rows:
        grouped.setdefault(item.name, (item, []))[1].append(version)

    plugins = []
    for name in sorted(grouped):
        item, versions = grouped[name]
        versions.sort(key=lambda v: pkg.version_key(v.version), reverse=True)
        latest = versions[0]
        plugins.append(
            {
                "name": item.name,
                "kind": item.kind,
                "description": item.description or _loads(latest.manifest_json).get("description", ""),
                "maintainer": item.maintainer,
                "version": latest.version,
                "sha256": latest.sha256,
                "size": latest.size_bytes,
                # 相对 index 的 URL。客户端拼成 <backend>/api/v1/ctl/marketplace/artifacts/<name>/<version>
                "artifact": f"artifacts/{item.name}/{latest.version}",
                "components": _loads(latest.components_json),
                "versions": [
                    {"version": v.version, "sha256": v.sha256, "size": v.size_bytes, "published_at": v.published_at}
                    for v in versions
                ],
            }
        )
    return {"schema": INDEX_SCHEMA_VERSION, "name": MARKET_NAME, "plugins": plugins}


def etag_of(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return '"' + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16] + '"'


async def device_artifact(db: AsyncSession, ctx: DeviceContext, name: str, version: str) -> tuple[bytes, str]:
    """设备下载制品。看不见 / 不存在 / 未发布 → 404；已下架 → 410；哈希不对 → 500（不下发）。

    返回 (内容, sha256)。成功后写一行 market_downloads。
    """
    row = (
        await db.execute(
            select(MarketItem, MarketVersion)
            .join(MarketVersion, MarketVersion.item_id == MarketItem.id)
            .where(_visible_to(ctx), MarketItem.name == name, MarketVersion.version == version)
        )
    ).first()
    if row is None or row[1].status == STATUS_DRAFT:
        raise HTTPException(status_code=404, detail="Not Found")
    item, ver = row
    if ver.status == STATUS_YANKED:
        raise HTTPException(status_code=410, detail=f"{name}@{version} 已下架")
    try:
        content = _read_verified(ver)
    except FileNotFoundError:
        logger.error("artifact missing", event="market_artifact_missing", item=name, version=version)
        raise HTTPException(status_code=500, detail="artifact unavailable") from None
    except ArtifactIntegrityError:
        # 存储里的文件被换了或坏了。宁可装不上，也不能发一个和目录登记不一致的包
        logger.error("artifact integrity failed", event="market_artifact_integrity_failed", item=name, version=version)
        raise HTTPException(status_code=500, detail="artifact integrity check failed") from None

    db.add(
        MarketDownload(
            created_at=utc_now_iso(),
            item_name=item.name,
            version=ver.version,
            device_id=ctx.device_id,
            org_id=ctx.org_id,
            # 从凭据取，不从请求取（方案 §5.6）
            user_ref=ctx.user_ref,
            request_id=current_request_id(),
        )
    )
    await db.commit()
    return content, ver.sha256


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _read_verified(ver: MarketVersion) -> bytes:
    content = storage_mod.storage.get(ver.storage_key)
    if pkg.sha256_hex(content) != ver.sha256:
        raise ArtifactIntegrityError(ver.storage_key)
    return content


async def _check_scope(db: AsyncSession, org_id: str, team_id: str) -> None:
    if await identity_admin.get_organization(db, org_id) is None:
        raise HTTPException(status_code=404, detail=f"组织 {org_id!r} 不存在")
    if team_id and await identity_admin.get_team(db, org_id, team_id) is None:
        raise HTTPException(status_code=404, detail=f"团队 {org_id}/{team_id} 不存在")


async def _require_item(db: AsyncSession, name: str) -> MarketItem:
    item = (await db.execute(select(MarketItem).where(MarketItem.name == name))).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"插件 {name!r} 不存在")
    return item


async def _require_version(db: AsyncSession, item: MarketItem, version: str) -> MarketVersion:
    row = (
        await db.execute(
            select(MarketVersion).where(MarketVersion.item_id == item.id, MarketVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{item.name}@{version} 不存在")
    return row


async def _versions_by_item(db: AsyncSession, item_ids: list[int]) -> dict[int, list[MarketVersion]]:
    if not item_ids:
        return {}
    rows = (await db.execute(select(MarketVersion).where(MarketVersion.item_id.in_(item_ids)))).scalars().all()
    out: dict[int, list[MarketVersion]] = {}
    for v in rows:
        out.setdefault(v.item_id, []).append(v)
    for versions in out.values():
        versions.sort(key=lambda v: pkg.version_key(v.version), reverse=True)
    return out


async def _download_counts(db: AsyncSession, names: list[str]) -> dict[str, int]:
    if not names:
        return {}
    rows = (
        await db.execute(
            select(MarketDownload.item_name, func.count())
            .where(MarketDownload.item_name.in_(names))
            .group_by(MarketDownload.item_name)
        )
    ).all()
    return {name: int(count) for name, count in rows}


def _require_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason 必填（会进审计）")
    return reason


def _audit(
    db: AsyncSession,
    *,
    item: MarketItem,
    action: str,
    actor: str,
    now: str,
    version: str = "",
    reason: str = "",
    detail: Optional[dict[str, Any]] = None,
) -> None:
    db.add(
        MarketAudit(
            item_id=item.id,
            item_name=item.name,
            version=version,
            action=action,
            reason=reason,
            actor=actor or "",
            detail_json=_dumps(detail or {}),
            created_at=now,
            request_id=current_request_id(),
        )
    )


def _admin_write(action: str, target_id: str) -> None:
    """管理台写操作完成一条。reason 不进日志，它在审计表里。"""
    logger.info("admin write", event="admin_write", action=action, target_id=target_id, actor=current_actor())


def _to_version(v: MarketVersion) -> MarketVersionItem:
    return MarketVersionItem(
        id=v.id, version=v.version, status=v.status, sha256=v.sha256, size_bytes=v.size_bytes,
        manifest=_loads(v.manifest_json), components=_loads(v.components_json), created_at=v.created_at,
        created_by=v.created_by, published_at=v.published_at, published_by=v.published_by, yanked_at=v.yanked_at,
    )


def _to_item(item: MarketItem, versions: list[MarketVersion], downloads: int) -> MarketItemOut:
    published = [v for v in versions if v.status == STATUS_PUBLISHED]
    return MarketItemOut(
        id=item.id, name=item.name, kind=item.kind, description=item.description, maintainer=item.maintainer,
        org_id=item.org_id, team_id=item.team_id, created_at=item.created_at, updated_at=item.updated_at,
        created_by=item.created_by, latest_version=published[0].version if published else None,
        versions=[_to_version(v) for v in versions], download_count=downloads,
    )
