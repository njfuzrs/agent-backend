"""统计分析 API。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import (
    CostStatsResponse,
    ModelStatsResponse,
    StatsOverviewResponse,
    ToolStatsResponse,
    TrendsResponse,
)
from app.services.stats_service import (
    get_cost_analysis,
    get_model_distribution,
    get_overview,
    get_tool_distribution,
    get_trends,
)
from app.utils.auth import verify_basic_auth

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverviewResponse, dependencies=[Depends(verify_basic_auth)])
async def overview(
    tool_source: Optional[str] = None,
    model: Optional[str] = None,
    exit_status: Optional[str] = None,
    task_type: Optional[str] = None,
    quality_status: Optional[str] = None,
    project_name: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_overview(
        db,
        tool_source=tool_source,
        model=model,
        exit_status=exit_status,
        task_type=task_type,
        quality_status=quality_status,
        project_name=project_name,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/trends", response_model=TrendsResponse, dependencies=[Depends(verify_basic_auth)])
async def trends(
    granularity: str = Query("day", pattern="^(day|week)$"),
    tool_source: Optional[str] = None,
    model: Optional[str] = None,
    exit_status: Optional[str] = None,
    task_type: Optional[str] = None,
    quality_status: Optional[str] = None,
    project_name: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_trends(
        db,
        granularity=granularity,
        tool_source=tool_source,
        model=model,
        exit_status=exit_status,
        task_type=task_type,
        quality_status=quality_status,
        project_name=project_name,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/tools", response_model=ToolStatsResponse, dependencies=[Depends(verify_basic_auth)])
async def tools(
    limit: int = Query(10, ge=1, le=50),
    tool_source: Optional[str] = None,
    model: Optional[str] = None,
    exit_status: Optional[str] = None,
    task_type: Optional[str] = None,
    quality_status: Optional[str] = None,
    project_name: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_tool_distribution(
        db,
        limit=limit,
        tool_source=tool_source,
        model=model,
        exit_status=exit_status,
        task_type=task_type,
        quality_status=quality_status,
        project_name=project_name,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/models", response_model=ModelStatsResponse, dependencies=[Depends(verify_basic_auth)])
async def models(
    limit: int = Query(10, ge=1, le=50),
    tool_source: Optional[str] = None,
    model: Optional[str] = None,
    exit_status: Optional[str] = None,
    task_type: Optional[str] = None,
    quality_status: Optional[str] = None,
    project_name: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_model_distribution(
        db,
        limit=limit,
        tool_source=tool_source,
        model=model,
        exit_status=exit_status,
        task_type=task_type,
        quality_status=quality_status,
        project_name=project_name,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/cost", response_model=CostStatsResponse, dependencies=[Depends(verify_basic_auth)])
async def cost(
    granularity: str = Query("day", pattern="^(day|week)$"),
    tool_source: Optional[str] = None,
    model: Optional[str] = None,
    exit_status: Optional[str] = None,
    task_type: Optional[str] = None,
    quality_status: Optional[str] = None,
    project_name: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_cost_analysis(
        db,
        granularity=granularity,
        tool_source=tool_source,
        model=model,
        exit_status=exit_status,
        task_type=task_type,
        quality_status=quality_status,
        project_name=project_name,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )
