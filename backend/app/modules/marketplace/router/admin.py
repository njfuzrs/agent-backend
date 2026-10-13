"""管理台市场 API。走 cookie 会话（`require_web_session`，admin），不走设备凭据。

路径在 /api/v1/marketplace/**，**不在 /ctl/**：浏览器没有设备凭据（理由同 policy admin）。

上架流程：登记条目（POST /items）→ 上传包（POST /items/{name}/versions，multipart，落 draft）
→ 发布（POST .../publish?reason=）。发布 / 下架必须写理由，进 market_audit。
版本不可覆盖、不可删除：下架是终态，要修就升版本号重传。

CI 上传凭据（P2′，方案 §7）：上传端点单独一个 router（`upload_router`），认 cookie 或机器凭据；
其余端点路由级先挂 `deny_upload_token`（有效机器凭据 → 403 + denied 审计）再挂 `require_web_session`。
由 test_boundaries 的 test_marketplace_admin_requires_web_session 锁定：只有上传端点能换依赖。
"""

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.db import get_db
from app.core.timeutil import utc_now
from app.modules.marketplace.schemas import (
    MarketAuditListResponse,
    MarketDownloadListResponse,
    MarketDownloadStatsResponse,
    MarketItemCreate,
    MarketItemListResponse,
    MarketItemOut,
    MarketItemUpdate,
    MarketVersionItem,
    UploadTokenAuditListResponse,
    UploadTokenCreate,
    UploadTokenIssued,
    UploadTokenItem,
    UploadTokenListResponse,
)
from app.modules.marketplace.service import catalog, upload_tokens
from app.modules.marketplace.service.package import MAX_PACKAGE_BYTES
from app.modules.marketplace.service.upload_tokens import Uploader, deny_upload_token, require_uploader

router = APIRouter(
    prefix="/marketplace",
    tags=["marketplace-admin"],
    # 顺序有意义：先拦机器凭据（403），再要 cookie（401）
    dependencies=[Depends(deny_upload_token), Depends(require_web_session)],
)

# 只有上传 draft 这一个端点接受机器凭据
upload_router = APIRouter(prefix="/marketplace", tags=["marketplace-admin"])


@router.get("/items", response_model=MarketItemListResponse)
async def list_items(org_id: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """含 draft / yanked 的全部条目与版本。管理台要看得见没发布的和下架的。"""
    return await catalog.list_items(db, org_id=org_id)


@router.post("/items", response_model=MarketItemOut, status_code=201)
async def create_item(
    payload: MarketItemCreate,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_web_session),
):
    return await catalog.create_item(db, payload, actor=actor)


@router.get("/audit", response_model=MarketAuditListResponse)
async def list_audit(
    name: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await catalog.list_audit(db, name=name, limit=limit)


@router.get("/downloads", response_model=MarketDownloadListResponse)
async def list_downloads(
    name: Optional[str] = None,
    user_ref: Optional[int] = None,
    device_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """谁（user_ref，从设备凭据取）在什么时候拉了哪个插件的哪个版本。"""
    return await catalog.list_downloads(
        db, name=name, user_ref=user_ref, device_id=device_id, since=since, limit=limit
    )


@router.get("/downloads/stats", response_model=MarketDownloadStatsResponse)
async def download_stats(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    since = (utc_now() - timedelta(days=days)).isoformat()
    return await catalog.download_stats(db, since=since)


@router.get("/items/{name}", response_model=MarketItemOut)
async def get_item(name: str, db: AsyncSession = Depends(get_db)):
    return await catalog.get_item(db, name)


@router.patch("/items/{name}", response_model=MarketItemOut)
async def update_item(
    name: str,
    payload: MarketItemUpdate,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_web_session),
):
    return await catalog.update_item(db, name, payload, actor=actor)


@upload_router.post("/items/{name}/versions", response_model=MarketVersionItem, status_code=201)
async def upload_version(
    name: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    uploader: Uploader = Depends(require_uploader),
):
    """上传 tar.gz，落 draft。name / version 只从包里的 plugin.json 取。

    管理台 admin（cookie）或 CI 上传凭据（Bearer mkt_...）均可；凭据只能传自己组织的条目。
    """
    item = await catalog.get_item(db, name)
    await upload_tokens.deny_cross_org(uploader, item.org_id, name)
    # 多读一个字节：读到就说明超限，不必把整个大文件读进内存
    content = await file.read(MAX_PACKAGE_BYTES + 1)
    if len(content) > MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"包超过 {MAX_PACKAGE_BYTES // (1024 * 1024)} MiB")
    version = await catalog.upload_version(db, name, content, actor=uploader.actor)
    await upload_tokens.record_upload(uploader, name, version.version, version.sha256)
    return version


@router.post("/items/{name}/versions/{version}/publish", response_model=MarketVersionItem)
async def publish_version(
    name: str,
    version: str,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_web_session),
):
    return await catalog.publish_version(db, name, version, actor=actor, reason=reason)


@router.post("/items/{name}/versions/{version}/yank", response_model=MarketVersionItem)
async def yank_version(
    name: str,
    version: str,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_web_session),
):
    return await catalog.yank_version(db, name, version, actor=actor, reason=reason)


# ---------------------------------------------------------------------------
# CI 上传凭据（只能上传 draft）
# ---------------------------------------------------------------------------
@router.get("/upload-tokens", response_model=UploadTokenListResponse)
async def list_upload_tokens(db: AsyncSession = Depends(get_db)):
    return await upload_tokens.list_tokens(db)


@router.post("/upload-tokens", response_model=UploadTokenIssued, status_code=201)
async def issue_upload_token(
    payload: UploadTokenCreate,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_web_session),
):
    """明文 token 只在本响应里出现一次。"""
    return await upload_tokens.issue(db, payload, actor=actor)


@router.get("/upload-tokens/audit", response_model=UploadTokenAuditListResponse)
async def list_upload_token_audit(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await upload_tokens.list_audit(db, limit=limit)


@router.post("/upload-tokens/{token_id}/revoke", response_model=UploadTokenItem)
async def revoke_upload_token(
    token_id: int,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(require_web_session),
):
    """吊销立即生效：下一次用它的请求即 401。"""
    return await upload_tokens.revoke(db, token_id, actor=actor, reason=reason)
