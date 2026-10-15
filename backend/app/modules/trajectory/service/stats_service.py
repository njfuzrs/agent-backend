"""统计分析服务。"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trajectory.model import ToolStep, Trajectory

# 成功口径的唯一事实源：exit_status 表示「结果」，只有正常收尾才算成功。
#
# 刻意不含 terminal_closed：它是「结束方式」（落在 end_source），不是结果。
# sid-code 在空闲关终端时会按末轮推断 exit_status（正常收尾 → end_turn），
# 只有一轮都没跑过才落 exit_status=terminal_closed——没提问不算成功也不算失败的任务。
# 若把它放进来，「先 Esc 打断再关窗口」那 ~11% 的会话会被误算成功（2026-10-15 实测 9/82）。
SUCCESS_EXIT_STATUSES = frozenset({"end_turn"})

# 不进成功率分母的 exit_status：没有任务可评判的会话。
# exit_status=terminal_closed 只在「开了就关、一轮都没跑」时出现（sid-code collector），
# 计入分母会把空会话算成失败，曲线随「开了多少空窗口」漂移。
# 只影响 success_rate；total_trajectories / count 仍按全部会话计。
NO_TASK_EXIT_STATUSES = frozenset({"terminal_closed"})


def is_success(exit_status: Optional[str]) -> bool:
    return (exit_status or "") in SUCCESS_EXIT_STATUSES


def success_rate(items: list[Trajectory]) -> float:
    """成功率 = 成功会话数 ÷（全部会话 − 无任务会话）。分母为 0 时返回 0.0。"""
    judged = [t for t in items if (t.exit_status or "") not in NO_TASK_EXIT_STATUSES]
    if not judged:
        return 0.0
    return round(sum(1 for t in judged if is_success(t.exit_status)) / len(judged), 4)


def build_trajectory_filters(
    *,
    tool_source: Optional[str] = None,
    model: Optional[str] = None,
    exit_status: Optional[str] = None,
    task_type: Optional[str] = None,
    quality_status: Optional[str] = None,
    project_name: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    device_id: Optional[str] = None,
) -> list:
    filters = [Trajectory.deleted_at.is_(None)]

    if tool_source:
        filters.append(Trajectory.tool_source == tool_source)
    if model:
        filters.append(Trajectory.model == model)
    if exit_status:
        filters.append(Trajectory.exit_status == exit_status)
    if task_type:
        filters.append(Trajectory.task_type == task_type)
    if quality_status:
        filters.append(Trajectory.quality_status == quality_status)
    if project_name:
        filters.append(Trajectory.project_name == project_name)
    if search:
        filters.append(Trajectory.first_prompt.contains(search))
    if start_date:
        filters.append(Trajectory.start_time >= start_date)
    if end_date:
        filters.append(Trajectory.start_time <= end_date)
    if device_id:
        # M4 设备列表「轨迹」跳转：/?device_id= 实际落到 /trajectories?device_id=
        # 列已有 idx_traj_device_id，只加参数不改响应形状。
        filters.append(Trajectory.device_id == device_id)

    return filters


async def get_overview(
    db: AsyncSession,
    **filters,
) -> dict:
    trajectories = await _load_trajectories(db, **filters)
    total = len(trajectories)
    total_tokens = sum(t.total_tokens or 0 for t in trajectories)
    total_cost = sum(t.total_cost_usd or 0.0 for t in trajectories)
    total_steps = sum(t.total_steps or 0 for t in trajectories)

    return {
        "total_trajectories": total,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "avg_steps_per_trajectory": round(total_steps / total, 2) if total else 0.0,
        "avg_cost_per_trajectory": round(total_cost / total, 4) if total else 0.0,
        "success_rate": success_rate(trajectories),
        "tool_source_distribution": dict(Counter(t.tool_source or "unknown" for t in trajectories)),
        "model_distribution": dict(Counter((t.model or "unknown") for t in trajectories)),
        "quality_distribution": dict(Counter((t.quality_status or "unreviewed") for t in trajectories)),
    }


async def get_trends(
    db: AsyncSession,
    *,
    granularity: str = "day",
    **filters,
) -> dict:
    trajectories = await _load_trajectories(db, **filters)
    bucket_map: dict[str, list[Trajectory]] = defaultdict(list)

    for trajectory in trajectories:
        bucket = _bucket_label(trajectory.start_time, granularity)
        if bucket:
            bucket_map[bucket].append(trajectory)

    points = []
    for bucket in sorted(bucket_map):
        items = bucket_map[bucket]
        total_steps = sum(item.total_steps or 0 for item in items)
        total_tokens = sum(item.total_tokens or 0 for item in items)
        total_cost = sum(item.total_cost_usd or 0.0 for item in items)
        count = len(items)

        points.append(
            {
                "date": bucket,
                "count": count,
                "total_tokens": total_tokens,
                "total_cost_usd": round(total_cost, 4),
                "avg_steps": round(total_steps / count, 2) if count else 0.0,
                "success_rate": success_rate(items),
            }
        )

    return {"granularity": _normalize_granularity(granularity), "data": points}


async def get_tool_distribution(
    db: AsyncSession,
    *,
    limit: int = 10,
    **filters,
) -> dict:
    rows = await _load_tool_rows(db, **filters)
    counts = Counter(tool_name for tool_name in rows if tool_name)
    items = [
        {"name": name, "count": count}
        for name, count in counts.most_common(limit)
    ]
    return {"items": items}


async def get_model_distribution(
    db: AsyncSession,
    *,
    limit: int = 10,
    **filters,
) -> dict:
    trajectories = await _load_trajectories(db, **filters)
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "tokens": 0, "cost": 0.0})

    for trajectory in trajectories:
        key = trajectory.model or "unknown"
        grouped[key]["count"] += 1
        grouped[key]["tokens"] += trajectory.total_tokens or 0
        grouped[key]["cost"] += trajectory.total_cost_usd or 0.0

    items = [
        {
            "name": name,
            "count": int(values["count"]),
            "total_tokens": int(values["tokens"]),
            "total_cost_usd": round(float(values["cost"]), 4),
        }
        for name, values in sorted(grouped.items(), key=lambda item: item[1]["count"], reverse=True)[:limit]
    ]
    return {"items": items}


async def get_cost_analysis(
    db: AsyncSession,
    *,
    granularity: str = "day",
    **filters,
) -> dict:
    trajectories = await _load_trajectories(db, **filters)
    bucketed: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    total_by_tool_source: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "tokens": 0, "cost": 0.0}
    )
    total_by_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"count": 0, "tokens": 0, "cost": 0.0}
    )

    for trajectory in trajectories:
        bucket = _bucket_label(trajectory.start_time, granularity)
        if not bucket:
            continue

        tool_source = trajectory.tool_source or "unknown"
        model = trajectory.model or "unknown"
        total_cost = trajectory.total_cost_usd or 0.0
        total_tokens = trajectory.total_tokens or 0

        bucketed[bucket][tool_source] += total_cost

        total_by_tool_source[tool_source]["count"] += 1
        total_by_tool_source[tool_source]["tokens"] += total_tokens
        total_by_tool_source[tool_source]["cost"] += total_cost

        total_by_model[model]["count"] += 1
        total_by_model[model]["tokens"] += total_tokens
        total_by_model[model]["cost"] += total_cost

    dates = sorted(bucketed)
    tool_sources = [
        name
        for name, _ in sorted(
            total_by_tool_source.items(),
            key=lambda item: item[1]["cost"],
            reverse=True,
        )
    ]

    series = [
        {
            "name": tool_source,
            "values": [round(bucketed[date_value].get(tool_source, 0.0), 4) for date_value in dates],
        }
        for tool_source in tool_sources
    ]

    timeline = []
    for date_value in dates:
        by_tool_source = {
            tool_source: round(bucketed[date_value].get(tool_source, 0.0), 4)
            for tool_source in tool_sources
            if bucketed[date_value].get(tool_source, 0.0) > 0
        }
        timeline.append(
            {
                "date": date_value,
                "total_cost_usd": round(sum(by_tool_source.values()), 4),
                "by_tool_source": by_tool_source,
            }
        )

    return {
        "granularity": _normalize_granularity(granularity),
        "dates": dates,
        "series": series,
        "totals_by_tool_source": _to_distribution_items(total_by_tool_source),
        "totals_by_model": _to_distribution_items(total_by_model),
        "timeline": timeline,
    }


async def _load_trajectories(db: AsyncSession, **filters) -> list[Trajectory]:
    result = await db.execute(
        select(Trajectory).where(*build_trajectory_filters(**filters))
    )
    return list(result.scalars().all())


async def _load_tool_rows(db: AsyncSession, **filters) -> list[str]:
    result = await db.execute(
        select(ToolStep.tool_name)
        .join(Trajectory, ToolStep.trajectory_id == Trajectory.id)
        .where(*build_trajectory_filters(**filters))
        .where(ToolStep.tool_name.is_not(None))
        .where(ToolStep.message_type == "action")
    )
    return [str(value) for value in result.scalars().all() if value]


def _to_distribution_items(grouped: dict[str, dict[str, float]]) -> list[dict]:
    return [
        {
            "name": name,
            "count": int(values["count"]),
            "total_tokens": int(values["tokens"]),
            "total_cost_usd": round(float(values["cost"]), 4),
        }
        for name, values in sorted(grouped.items(), key=lambda item: item[1]["cost"], reverse=True)
    ]


def _normalize_granularity(granularity: str) -> str:
    return "week" if granularity == "week" else "day"


def _bucket_label(time_value: Optional[str], granularity: str) -> Optional[str]:
    if not time_value:
        return None

    parsed = _parse_time(time_value)
    if not parsed:
        return None

    if _normalize_granularity(granularity) == "week":
        week_start = parsed.date() - timedelta(days=parsed.weekday())
        return week_start.isoformat()

    return parsed.date().isoformat()


def _parse_time(time_value: str) -> Optional[datetime]:
    normalized = time_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.combine(date.fromisoformat(time_value[:10]), datetime.min.time())
        except ValueError:
            return None
