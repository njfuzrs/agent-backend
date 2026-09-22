"""管理台 flag API。走 cookie 会话（`require_web_session`），不走设备凭据。

路径在 /api/v1/flags/**，**不在 /ctl/** —— 规划 §3「管理台后续」明确纠正了 2.2 原文：
管理台 CRUD 不能挂 require_device，浏览器没有设备凭据。放在 /ctl/ 下会被边界测试 ②
要求挂 require_device，然后为了让浏览器能用又去豁免，等于把写端点也变成无认证。
路径分开，鉴权链才分得开。

与 identity/router/admin.py 同一套形状：不 import 数据面符号，
控制面模块禁令（边界测试 ①）才能保住。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.db import get_db
from app.modules.flag.schemas import (
    FlagAuditListResponse,
    FlagCreate,
    FlagDeleteResponse,
    FlagItem,
    FlagListResponse,
    FlagUpsert,
)
from app.modules.flag.service import flags as flag_service

router = APIRouter(
    prefix="/flags",
    tags=["flag-admin"],
    dependencies=[Depends(require_web_session)],
)


@router.get("", response_model=FlagListResponse)
async def list_flags(db: AsyncSession = Depends(get_db)):
    """含 disabled 的全量列表 —— 管理台要能看见「关掉但没删」的那些。"""
    return await flag_service.list_flags(db)


@router.post("", response_model=FlagItem, status_code=201)
async def create_flag(
    payload: FlagCreate,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await flag_service.create_flag(db, payload, actor=username)


@router.put("/{key}", response_model=FlagItem)
async def update_flag(
    key: str,
    payload: FlagUpsert,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await flag_service.update_flag(db, key, payload, actor=username)


@router.post("/{key}/disable", response_model=FlagItem)
async def disable_flag(
    key: str,
    reason: str = Query("", max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    """停用：不进下发，客户端按「远程已删除」回落默认值。"""
    return await flag_service.set_flag_enabled(db, key, enabled=False, actor=username, reason=reason)


@router.post("/{key}/enable", response_model=FlagItem)
async def enable_flag(
    key: str,
    reason: str = Query("", max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await flag_service.set_flag_enabled(db, key, enabled=True, actor=username, reason=reason)


@router.delete("/{key}", response_model=FlagDeleteResponse)
async def delete_flag(
    key: str,
    reason: str = Query("", max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    """真删。审计行保留（flag_id → NULL，key 文本副本仍在）。"""
    return await flag_service.delete_flag(db, key, actor=username, reason=reason)


@router.get("/audit", response_model=FlagAuditListResponse)
async def list_audit(
    key: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await flag_service.list_audit(db, key=key, limit=limit)
