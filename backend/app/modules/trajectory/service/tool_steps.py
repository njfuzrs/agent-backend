"""从 .traj 中提取工具调用步骤，并同步到 tool_steps 表。"""

import json
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trajectory.model import ToolStep, Trajectory


def extract_tool_steps(traj_data: dict[str, Any]) -> list[dict[str, Any]]:
    """从 trajectory 数组中抽取可聚合的工具步骤。

    action 步骤直接读取 tool_name；observation 步骤尝试通过 tool_use_id
    关联到对应的 action，便于后续做错误搜索和工具维度分析。
    """
    trajectory = traj_data.get("trajectory", [])
    tool_name_by_use_id: dict[str, str] = {}

    for step in trajectory:
        tool_use_id = str(step.get("tool_use_id") or "").strip()
        tool_name = str(step.get("tool_name") or "").strip()
        if tool_use_id and tool_name:
            tool_name_by_use_id[tool_use_id] = tool_name

    extracted: list[dict[str, Any]] = []
    for index, step in enumerate(trajectory):
        tool_name = str(step.get("tool_name") or "").strip()
        if not tool_name:
            tool_use_id = str(step.get("tool_use_id") or "").strip()
            tool_name = tool_name_by_use_id.get(tool_use_id, "")
        if not tool_name:
            continue

        extracted.append(
            {
                "step_index": index,
                "message_type": str(step.get("message_type") or ""),
                "tool_name": tool_name,
                "tool_input_summary": _summarize_tool_input(step.get("tool_input")),
                "is_error": bool(step.get("is_error", False)),
                "timestamp": step.get("timestamp"),
            }
        )

    return extracted


async def sync_tool_steps(
    db: AsyncSession,
    trajectory: Trajectory,
    traj_data: dict[str, Any],
) -> int:
    """覆盖写入一条轨迹对应的 tool_steps。"""
    if trajectory.id is None:
        raise ValueError("trajectory.id 不能为空")

    await db.execute(delete(ToolStep).where(ToolStep.trajectory_id == trajectory.id))

    items = extract_tool_steps(traj_data)
    if not items:
        return 0

    db.add_all(
        [
            ToolStep(
                trajectory_id=trajectory.id,
                step_index=item["step_index"],
                message_type=item["message_type"] or "action",
                tool_name=item["tool_name"],
                tool_input_summary=item["tool_input_summary"],
                is_error=item["is_error"],
                timestamp=item["timestamp"],
            )
            for item in items
        ]
    )
    return len(items)


def _summarize_tool_input(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""

    for key in ("file_path", "path", "relative_workspace_path", "description", "pattern", "url"):
        value = str(tool_input.get(key) or "").strip()
        if value:
            return value[:120]

    command = str(tool_input.get("command") or "").strip()
    if command:
        return command.replace("\n", " ")[:120]

    try:
        return json.dumps(tool_input, ensure_ascii=False)[:120]
    except (TypeError, ValueError):
        return str(tool_input)[:120]
