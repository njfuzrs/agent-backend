"""管理台 cost API。走 cookie 会话（`require_web_session`），不走设备凭据。

两条前缀：
    /api/v1/usage/ledger/**  —— 账本只读（列表 + by-scope）
    /api/v1/budgets/**       —— 预算 CRUD

都不在 /ctl/ 下。上报是 POST /usage/ledger（设备凭据），查询是同前缀不同方法/鉴权。
边界测试必须能区分这两条链。

管理台对 usage_ledger **只读**。账本能删就不是账本。
`test_usage_ledger_has_no_delete_endpoint` 盯的就是这一点。

预算写口对标 /api/v1/policies/**：reason 必填，同层同周期第二条 enabled → 409。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.session import require_web_session
from app.core.db import get_db
from app.modules.cost.schemas import (
    BudgetAuditListResponse,
    BudgetCreate,
    BudgetDeleteResponse,
    BudgetItem,
    BudgetListResponse,
    BudgetUpdate,
    UsageByScopeResponse,
    UsageLedgerListResponse,
)
from app.modules.cost.service import budgets as budget_service
from app.modules.cost.service import queries as query_service

ledger_router = APIRouter(
    prefix="/usage/ledger",
    tags=["usage-admin"],
    dependencies=[Depends(require_web_session)],
)

budget_router = APIRouter(
    prefix="/budgets",
    tags=["budget-admin"],
    dependencies=[Depends(require_web_session)],
)


@ledger_router.get("", response_model=UsageLedgerListResponse)
async def list_ledger(
    org_id: Optional[str] = None,
    device_id: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """下钻列表。按 received_at 倒序。"""
    return await query_service.list_ledger(
        db, org_id=org_id, device_id=device_id, since=since, limit=limit
    )


@ledger_router.get("/stats/by-scope", response_model=UsageByScopeResponse)
async def by_scope(
    period: str = Query("monthly"),
    period_key: Optional[str] = None,
    org_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """按 device 一行。回答「谁在烧钱」。不得返回单价字段。"""
    return await query_service.by_scope(db, period=period, period_key=period_key, org_id=org_id)


@budget_router.get("", response_model=BudgetListResponse)
async def list_budgets(
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
    org_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """含 disabled 的列表。管理台要能看见「关掉但没删」的那些。"""
    return await budget_service.list_budgets(
        db, scope_type=scope_type, scope_id=scope_id, org_id=org_id
    )


@budget_router.get("/audit", response_model=BudgetAuditListResponse)
async def list_audit(
    budget_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    return await budget_service.list_audit(db, budget_id=budget_id, limit=limit)


@budget_router.get("/evaluate")
async def evaluate_budget(
    device_id: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
):
    """管理台预览该设备会命中哪一层。cookie 会话，**不是**客户端下发。"""
    budget = await budget_service.evaluate_for_device(db, device_id)
    if budget is None:
        return Response(status_code=204)
    return await budget_service.to_evaluate_payload(db, budget)


@budget_router.post("", response_model=BudgetItem, status_code=201)
async def create_budget(
    payload: BudgetCreate,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await budget_service.create_budget(db, payload, actor=username)


@budget_router.get("/{budget_id}", response_model=BudgetItem)
async def get_budget(
    budget_id: int,
    db: AsyncSession = Depends(get_db),
):
    return await budget_service.get_budget(db, budget_id)


@budget_router.patch("/{budget_id}", response_model=BudgetItem)
async def update_budget(
    budget_id: int,
    payload: BudgetUpdate,
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await budget_service.update_budget(db, budget_id, payload, actor=username)


@budget_router.post("/{budget_id}/disable", response_model=BudgetItem)
async def disable_budget(
    budget_id: int,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    """停用：不进下发。"""
    return await budget_service.set_budget_enabled(
        db, budget_id, enabled=False, actor=username, reason=reason
    )


@budget_router.post("/{budget_id}/enable", response_model=BudgetItem)
async def enable_budget(
    budget_id: int,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    return await budget_service.set_budget_enabled(
        db, budget_id, enabled=True, actor=username, reason=reason
    )


@budget_router.delete("/{budget_id}", response_model=BudgetDeleteResponse)
async def delete_budget(
    budget_id: int,
    reason: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
    username: str = Depends(require_web_session),
):
    """真删。审计行保留（budget_id → NULL，scope 文本副本仍在）。"""
    return await budget_service.delete_budget(db, budget_id, actor=username, reason=reason)
