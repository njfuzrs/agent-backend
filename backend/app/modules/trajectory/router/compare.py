"""多工具对比 API。"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth.data_plane import verify_basic_auth
from app.core.db import get_db
from app.modules.trajectory.model import CompareGroup, CompareGroupItem, ToolStep, Trajectory
from app.modules.trajectory.schemas import (
    CompareGroupAddItemsRequest,
    CompareGroupAddItemsResponse,
    CompareGroupCreateRequest,
    CompareGroupDetailResponse,
    CompareGroupListItem,
    CompareGroupListResponse,
    CompareRadarItem,
    CompareRadarResponse,
)

router = APIRouter(prefix="/compare", tags=["compare"])

RADAR_DIMENSIONS = ["步骤效率", "Token 效率", "耗时效率", "成功率", "工具多样性"]


@router.get("/groups", response_model=CompareGroupListResponse, dependencies=[Depends(verify_basic_auth)])
async def list_compare_groups(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CompareGroup)
        .options(selectinload(CompareGroup.items).selectinload(CompareGroupItem.trajectory))
        .order_by(CompareGroup.created_at.desc())
    )
    groups = list(result.scalars().all())

    items: list[CompareGroupListItem] = []
    for group in groups:
        active_items = _active_group_items(group)
        tool_sources = sorted({item.trajectory.tool_source for item in active_items if item.trajectory})
        items.append(
            CompareGroupListItem(
                id=group.id,
                name=group.name,
                description=group.description or "",
                task_prompt=group.task_prompt or "",
                created_at=group.created_at,
                item_count=len(active_items),
                tool_sources=tool_sources,
            )
        )

    return CompareGroupListResponse(items=items)


@router.post("/groups", response_model=CompareGroupDetailResponse, dependencies=[Depends(verify_basic_auth)])
async def create_compare_group(
    payload: CompareGroupCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    group = CompareGroup(
        name=payload.name.strip(),
        description=payload.description.strip(),
        task_prompt=payload.task_prompt.strip(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)

    return CompareGroupDetailResponse(
        id=group.id,
        name=group.name,
        description=group.description or "",
        task_prompt=group.task_prompt or "",
        created_at=group.created_at,
        item_count=0,
        items=[],
    )


@router.delete("/groups/{group_id}", dependencies=[Depends(verify_basic_auth)])
async def delete_compare_group(group_id: int, db: AsyncSession = Depends(get_db)):
    group = await _get_group_or_404(group_id, db)
    await db.delete(group)
    await db.commit()
    return {"status": "deleted", "group_id": group_id}


@router.get("/groups/{group_id}", response_model=CompareGroupDetailResponse, dependencies=[Depends(verify_basic_auth)])
async def get_compare_group(group_id: int, db: AsyncSession = Depends(get_db)):
    group = await _get_group_with_items_or_404(group_id, db)
    tool_usage_by_trajectory = await _load_tool_usage(db, [item.trajectory.id for item in _active_group_items(group)])
    return _to_group_detail(group, tool_usage_by_trajectory)


@router.post("/groups/{group_id}/items", response_model=CompareGroupAddItemsResponse, dependencies=[Depends(verify_basic_auth)])
async def add_compare_group_items(
    group_id: int,
    payload: CompareGroupAddItemsRequest,
    db: AsyncSession = Depends(get_db),
):
    await _get_group_or_404(group_id, db)

    result = await db.execute(
        select(Trajectory)
        .where(Trajectory.session_id.in_(payload.session_ids))
        .where(Trajectory.deleted_at.is_(None))
    )
    trajectories = {traj.session_id: traj for traj in result.scalars().all()}
    if not trajectories:
        raise HTTPException(status_code=404, detail="no trajectories found")

    trajectory_ids = [traj.id for traj in trajectories.values() if traj.id is not None]
    existing_result = await db.execute(
        select(CompareGroupItem.trajectory_id)
        .where(CompareGroupItem.group_id == group_id)
        .where(CompareGroupItem.trajectory_id.in_(trajectory_ids))
    )
    existing_ids = set(existing_result.scalars().all())

    added_count = 0
    skipped_session_ids: list[str] = []
    for session_id in payload.session_ids:
        trajectory = trajectories.get(session_id)
        if not trajectory or trajectory.id is None or trajectory.id in existing_ids:
            skipped_session_ids.append(session_id)
            continue
        db.add(CompareGroupItem(group_id=group_id, trajectory_id=trajectory.id))
        added_count += 1

    await db.commit()
    return CompareGroupAddItemsResponse(
        group_id=group_id,
        added_count=added_count,
        skipped_session_ids=skipped_session_ids,
    )


@router.delete("/groups/{group_id}/items/{trajectory_id}", dependencies=[Depends(verify_basic_auth)])
async def remove_compare_group_item(
    group_id: int,
    trajectory_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CompareGroupItem)
        .where(CompareGroupItem.group_id == group_id)
        .where(CompareGroupItem.trajectory_id == trajectory_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="compare group item not found")

    await db.delete(item)
    await db.commit()
    return {"status": "removed", "group_id": group_id, "trajectory_id": trajectory_id}


@router.get("/groups/{group_id}/radar", response_model=CompareRadarResponse, dependencies=[Depends(verify_basic_auth)])
async def get_compare_group_radar(group_id: int, db: AsyncSession = Depends(get_db)):
    group = await _get_group_with_items_or_404(group_id, db)
    trajectories = [item.trajectory for item in _active_group_items(group) if item.trajectory]

    return CompareRadarResponse(
        group_id=group.id,
        group_name=group.name,
        dimensions=RADAR_DIMENSIONS,
        items=_normalize_for_radar(trajectories),
    )


async def _get_group_or_404(group_id: int, db: AsyncSession) -> CompareGroup:
    result = await db.execute(select(CompareGroup).where(CompareGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="compare group not found")
    return group


async def _get_group_with_items_or_404(group_id: int, db: AsyncSession) -> CompareGroup:
    result = await db.execute(
        select(CompareGroup)
        .options(selectinload(CompareGroup.items).selectinload(CompareGroupItem.trajectory))
        .where(CompareGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="compare group not found")
    return group


def _active_group_items(group: CompareGroup) -> list[CompareGroupItem]:
    return [
        item
        for item in sorted(group.items, key=lambda value: value.id or 0)
        if item.trajectory and item.trajectory.deleted_at is None
    ]


async def _load_tool_usage(db: AsyncSession, trajectory_ids: list[int]) -> dict[int, dict[str, int]]:
    if not trajectory_ids:
        return {}

    result = await db.execute(
        select(ToolStep.trajectory_id, ToolStep.tool_name)
        .where(ToolStep.trajectory_id.in_(trajectory_ids))
        .where(ToolStep.message_type == "action")
        .where(ToolStep.tool_name.is_not(None))
    )

    grouped: dict[int, Counter] = defaultdict(Counter)
    for trajectory_id, tool_name in result.all():
        if trajectory_id is None or not tool_name:
            continue
        grouped[int(trajectory_id)][str(tool_name)] += 1

    return {
        trajectory_id: dict(counter.most_common())
        for trajectory_id, counter in grouped.items()
    }


def _to_group_detail(
    group: CompareGroup,
    tool_usage_by_trajectory: dict[int, dict[str, int]],
) -> CompareGroupDetailResponse:
    items = []
    for group_item in _active_group_items(group):
        trajectory = group_item.trajectory
        if not trajectory or trajectory.id is None:
            continue

        items.append(
            {
                "trajectory_id": trajectory.id,
                "session_id": trajectory.session_id,
                "tool_source": trajectory.tool_source,
                "model": trajectory.model,
                "start_time": trajectory.start_time,
                "duration_ms": trajectory.duration_ms,
                "total_steps": trajectory.total_steps or 0,
                "total_tokens": trajectory.total_tokens or 0,
                "total_cost_usd": trajectory.total_cost_usd or 0.0,
                "exit_status": trajectory.exit_status or "unknown",
                "quality_status": trajectory.quality_status or "unreviewed",
                "first_prompt": trajectory.first_prompt or "",
                "tools_used": _parse_json_list(trajectory.tools_used),
                "tool_usage": tool_usage_by_trajectory.get(trajectory.id, {}),
            }
        )

    return CompareGroupDetailResponse(
        id=group.id,
        name=group.name,
        description=group.description or "",
        task_prompt=group.task_prompt or "",
        created_at=group.created_at,
        item_count=len(items),
        items=items,
    )


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _normalize_for_radar(trajectories: list[Trajectory]) -> list[CompareRadarItem]:
    if not trajectories:
        return []

    tool_counts = [_tool_count(trajectory) for trajectory in trajectories]
    step_values = [trajectory.total_steps or 0 for trajectory in trajectories]
    token_values = [trajectory.total_tokens or 0 for trajectory in trajectories]
    duration_values = [trajectory.duration_ms or 0 for trajectory in trajectories]

    max_steps = max(step_values) or 1
    min_steps = min(step_values)
    max_tokens = max(token_values) or 1
    min_tokens = min(token_values)
    max_duration = max(duration_values) or 1
    min_duration = min(duration_values)
    max_tools = max(tool_counts) or 1
    min_tools = min(tool_counts)

    items: list[CompareRadarItem] = []
    for trajectory, tool_count in zip(trajectories, tool_counts, strict=True):
        duration_ms = trajectory.duration_ms or 0
        duration_seconds = round(duration_ms / 1000, 2)
        success_value = 1.0 if (trajectory.exit_status or "") == "end_turn" else 0.0

        if len(trajectories) == 1:
            normalized = [0.5, 0.5, 0.5, success_value, 0.5]
        else:
            normalized = [
                _norm_inverse(trajectory.total_steps or 0, min_steps, max_steps),
                _norm_inverse(trajectory.total_tokens or 0, min_tokens, max_tokens),
                _norm_inverse(duration_ms, min_duration, max_duration),
                success_value,
                _norm(tool_count, min_tools, max_tools),
            ]

        items.append(
            CompareRadarItem(
                trajectory_id=trajectory.id or 0,
                session_id=trajectory.session_id,
                tool_source=trajectory.tool_source,
                model=trajectory.model,
                values=[
                    float(trajectory.total_steps or 0),
                    float(trajectory.total_tokens or 0),
                    duration_seconds,
                    success_value,
                    float(tool_count),
                ],
                normalized=normalized,
            )
        )

    return items


def _tool_count(trajectory: Trajectory) -> int:
    return len(_parse_json_list(trajectory.tools_used))


def _norm_inverse(value: int, vmin: int, vmax: int) -> float:
    if vmax == vmin:
        return 0.5
    return round(1 - (value - vmin) / (vmax - vmin), 2)


def _norm(value: int, vmin: int, vmax: int) -> float:
    if vmax == vmin:
        return 0.5
    return round((value - vmin) / (vmax - vmin), 2)
