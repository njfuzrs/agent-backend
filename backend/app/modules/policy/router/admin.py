"""管理台 policy API。走 cookie 会话（`require_web_session`），不走设备凭据。

路径在 /api/v1/policies/**，**不在 /ctl/** —— 规划 §3 已纠正：管理台 CRUD 不能挂
require_device，浏览器没有设备凭据。放在 /ctl/ 下会被边界测试 ② 要求挂
require_device，然后为了让浏览器能用又去豁免，等于把写端点也变成无认证。

失败语义：写入 fail-closed。`reason` 空 → 422（flag 允许空 reason；policy 规划写明必填）。
未知字段 / 空策略 / 非法 feature → 422。同层第二条 enabled → 409。

与 identity/router/admin.py 同一套形状：不 import 数据面符号，
控制面模块禁令（边界测试 ①）才能保住。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.db import get_db
from app.modules.policy.schemas import (
    PolicyAuditListResponse,
    PolicyCreate,
    PolicyDeleteResponse,
    PolicyItem,
    PolicyListResponse,
    PolicyUpdate,
)
from app.modules.policy.service import policies as policy_service

router = APIRouter(
    prefix="/policies",
    tags=["policy-admin"],
    dependencies=[Depends(require_web_session)],
)


@router.get("", response_model=PolicyListResponse)
async def list_policies(
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    org_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """含 disabled 的列表。管理台要能看见「关掉但没删」的那些，并按 scope 筛。"""
    return await policy_service.list_policies(
        db, scope_type=scope_type, scope_id=scope_id, org_id=org_id
    )


@router.get("/audit", response_model=PolicyAuditListResponse)
async def list_audit(
    policy_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.list_audit(db, policy_id=policy_id, limit=limit)


@router.get("/evaluate")
async def evaluate_policy(
    device_id: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
):
    """管理台预览该设备会命中哪一层。cookie 会话，**不是**客户端下发。"""
    policy = await policy_service.evaluate_for_device(db, device_id)
    if policy is None:
        return Response(status_code=204)
    return policy_service.to_evaluate_payload(policy)


@router.post("", response_model=PolicyItem, status_code=201)
async def create_policy(
    payload: PolicyCreate,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await policy_service.create_policy(db, payload, actor=username)


@router.get("/{policy_id}", response_model=PolicyItem)
async def get_policy(
    policy_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await policy_service.get_policy(db, policy_id)


@router.put("/{policy_id}", response_model=PolicyItem)
async def update_policy(
    policy_id: int,
    payload: PolicyUpdate,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await policy_service.update_policy(db, policy_id, payload, actor=username)


@router.post("/{policy_id}/disable", response_model=PolicyItem)
async def disable_policy(
    policy_id: int,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    """停用：不进下发。"""
    return await policy_service.set_policy_enabled(
        db, policy_id, enabled=False, actor=username, reason=reason
    )


@router.post("/{policy_id}/enable", response_model=PolicyItem)
async def enable_policy(
    policy_id: int,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await policy_service.set_policy_enabled(
        db, policy_id, enabled=True, actor=username, reason=reason
    )


@router.delete("/{policy_id}", response_model=PolicyDeleteResponse)
async def delete_policy(
    policy_id: int,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    """真删。审计行保留（policy_id → NULL，scope 文本副本仍在）。"""
    return await policy_service.delete_policy(db, policy_id, actor=username, reason=reason)
