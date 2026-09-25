"""导出 API。"""

import gzip
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.data_plane import verify_basic_auth
from app.core.db import get_db
from app.modules.trajectory.model import Trajectory
from app.modules.trajectory.schemas import SFTExportRequest, TrajectoryExportRequest
from app.modules.trajectory.service.stats_service import build_trajectory_filters
from app.modules.trajectory.service.storage import storage
from app.modules.trajectory.service.traj_parser import load_traj_json

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/trajectories", dependencies=[Depends(verify_basic_auth)])
async def export_trajectories(
    request: TrajectoryExportRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Trajectory)
        .where(Trajectory.session_id.in_(request.session_ids))
        .where(Trajectory.deleted_at.is_(None))
    )
    trajectories = list(result.scalars().all())

    if not trajectories:
        raise HTTPException(status_code=404, detail="no trajectories found")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = []
        for trajectory in trajectories:
            content = _read_traj_content(trajectory)
            filename = f"{trajectory.session_id}.traj"
            archive.writestr(filename, content)
            manifest.append(
                {
                    "session_id": trajectory.session_id,
                    "tool_source": trajectory.tool_source,
                    "model": trajectory.model,
                    "start_time": trajectory.start_time,
                    "total_steps": trajectory.total_steps,
                    "total_tokens": trajectory.total_tokens,
                    "total_cost_usd": trajectory.total_cost_usd,
                    "quality_status": trajectory.quality_status,
                }
            )

        archive.writestr(
            "manifest.json",
            json.dumps({"total": len(manifest), "items": manifest}, ensure_ascii=False, indent=2),
        )

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="trajectories-export.zip"'},
    )


@router.post("/sft", dependencies=[Depends(verify_basic_auth)])
async def export_sft(
    request: SFTExportRequest,
    db: AsyncSession = Depends(get_db),
):
    trajectories = await _load_sft_trajectories(request, db)
    if not trajectories:
        raise HTTPException(status_code=404, detail="no trajectories found")

    lines = []
    for trajectory in trajectories:
        traj_data = load_traj_json(_read_traj_content(trajectory), trajectory.session_id)
        record = _build_sft_record(trajectory, traj_data, request)
        if record is not None:
            lines.append(json.dumps(record, ensure_ascii=False))

    if not lines:
        raise HTTPException(status_code=404, detail="no exportable trajectories found")

    content = ("\n".join(lines) + "\n").encode("utf-8")
    filename = _build_sft_filename(request.format)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _load_sft_trajectories(request: SFTExportRequest, db: AsyncSession) -> list[Trajectory]:
    if request.min_steps is not None and request.max_steps is not None and request.min_steps > request.max_steps:
        raise HTTPException(status_code=400, detail="min_steps cannot be greater than max_steps")

    query = select(Trajectory)
    filters = build_trajectory_filters(
        tool_source=request.tool_source,
        model=request.model,
        exit_status=request.exit_status,
        task_type=request.task_type,
        quality_status=request.quality_status,
        project_name=request.project_name,
        search=request.search,
        start_date=request.start_date,
        end_date=request.end_date,
    )
    for item in filters:
        query = query.where(item)

    if request.session_ids:
        query = query.where(Trajectory.session_id.in_(request.session_ids))
    if request.min_steps is not None:
        query = query.where(Trajectory.total_steps >= request.min_steps)
    if request.max_steps is not None:
        query = query.where(Trajectory.total_steps <= request.max_steps)

    query = query.order_by(asc(Trajectory.start_time), asc(Trajectory.session_id))
    result = await db.execute(query)
    return list(result.scalars().all())


def _build_sft_record(
    trajectory: Trajectory,
    traj_data: dict[str, Any],
    request: SFTExportRequest,
) -> Optional[dict[str, Any]]:
    messages = _build_messages_payload(trajectory, traj_data, include_thinking=request.include_thinking)
    if not messages:
        return None

    tools = _extract_tools_schema(trajectory.session_id)
    metadata = {
        "session_id": trajectory.session_id,
        "tool_source": trajectory.tool_source,
        "model": trajectory.model,
        "quality_status": trajectory.quality_status,
        "quality_rating": trajectory.quality_rating,
        "task_type": trajectory.task_type,
        "project_name": trajectory.project_name,
        "start_time": trajectory.start_time,
        "end_time": trajectory.end_time,
        "duration_ms": trajectory.duration_ms,
        "total_steps": trajectory.total_steps,
        "total_tokens": trajectory.total_tokens,
        "total_cost_usd": trajectory.total_cost_usd,
        "exit_status": trajectory.exit_status,
        "tools_used": _parse_json_list(trajectory.tools_used),
    }

    if request.format == "messages":
        return {
            "session_id": trajectory.session_id,
            "messages": messages,
            "tools": tools,
            "metadata": metadata,
        }

    prompt = _extract_prompt(messages)
    if request.format == "tool":
        return {
            "session_id": trajectory.session_id,
            "prompt": prompt,
            "response": _render_tool_text(messages),
            "tools": tools,
            "metadata": metadata,
        }

    return {
        "session_id": trajectory.session_id,
        "prompt": prompt,
        "response": _render_xml_text(messages),
        "tools": tools,
        "metadata": metadata,
    }


def _build_messages_payload(
    trajectory: Trajectory,
    traj_data: dict[str, Any],
    *,
    include_thinking: bool,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    for system_message in _extract_system_messages(trajectory.session_id, traj_data):
        messages.append({"role": "system", "content": system_message})

    for prompt in _extract_user_prompts(trajectory, traj_data):
        messages.append({"role": "user", "content": prompt})

    tool_name_by_use_id: dict[str, str] = {}
    for index, step in enumerate(traj_data.get("trajectory", [])):
        message_type = str(step.get("message_type") or "")
        tool_use_id = str(step.get("tool_use_id") or f"tool_{index}")
        tool_name = str(step.get("tool_name") or "").strip()
        if tool_name:
            tool_name_by_use_id[tool_use_id] = tool_name

        if message_type == "action":
            if tool_name:
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": _assistant_content(step, include_thinking),
                    "tool_calls": [
                        {
                            "id": tool_use_id,
                            "type": "function",
                            "name": tool_name,
                            "arguments": _tool_arguments(step.get("tool_input")),
                        }
                    ],
                }
                messages.append(assistant_message)
                continue

            content = _assistant_content(step, include_thinking)
            if content:
                messages.append({"role": "assistant", "content": content})
            continue

        if message_type == "observation":
            content = _stringify_content(step.get("content"))
            if not content:
                continue

            observation: dict[str, Any] = {"role": "tool", "content": content}
            if tool_use_id:
                observation["tool_call_id"] = tool_use_id
            resolved_tool_name = tool_name or tool_name_by_use_id.get(tool_use_id, "")
            if resolved_tool_name:
                observation["name"] = resolved_tool_name
            messages.append(observation)

    if not any(message.get("role") == "assistant" for message in messages):
        return []
    return messages


def _extract_system_messages(session_id: str, traj_data: dict[str, Any]) -> list[str]:
    raw_entries = _read_raw_entries(session_id)
    if raw_entries:
        request = raw_entries[0].get("request", {})
        system_messages = _normalize_system_blocks(request.get("system"))
        if system_messages:
            return system_messages

    results = []
    for item in traj_data.get("history", []):
        if item.get("role") != "system":
            continue
        content = _stringify_content(item.get("content"))
        if content:
            results.append(content)
    return _dedupe_in_order(results)


def _extract_tools_schema(session_id: str) -> list[dict[str, Any]]:
    raw_entries = _read_raw_entries(session_id)
    if not raw_entries:
        return []

    request = raw_entries[0].get("request", {})
    tools = request.get("tools")
    return tools if isinstance(tools, list) else []


def _extract_user_prompts(trajectory: Trajectory, traj_data: dict[str, Any]) -> list[str]:
    metadata = traj_data.get("metadata", {})
    user_prompts = metadata.get("user_prompts")
    if isinstance(user_prompts, list):
        values = [_stringify_content(item) for item in user_prompts]
        prompts = [value for value in values if value]
        if prompts:
            return _dedupe_in_order(prompts)

    history_prompts = []
    for item in traj_data.get("history", []):
        if item.get("role") != "user":
            continue
        content = _stringify_content(item.get("content"))
        if content:
            history_prompts.append(content)
    if history_prompts:
        return _dedupe_in_order(history_prompts)

    if trajectory.first_prompt:
        return [trajectory.first_prompt]
    return []


def _assistant_content(step: dict[str, Any], include_thinking: bool) -> Optional[str]:
    content = _stringify_content(step.get("content"))
    thought = str(step.get("thought") or "").strip()
    tool_name = str(step.get("tool_name") or "").strip()

    if tool_name and content:
        marker = f"Tool: {tool_name}"
        if marker in content:
            content = content.split(marker, 1)[0].strip()

    if include_thinking and thought:
        if content and content != thought:
            return f"{thought}\n\n{content}".strip()
        return thought

    return content or None


def _tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return parsed if isinstance(parsed, dict) else {"raw": value}
    return {}


def _extract_prompt(messages: list[dict[str, Any]]) -> str:
    user_messages = [str(message.get("content") or "") for message in messages if message.get("role") == "user"]
    if user_messages:
        return "\n\n".join(user_messages)
    return ""


def _render_tool_text(messages: list[dict[str, Any]]) -> str:
    blocks = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "").strip()

        if role == "system":
            if content:
                blocks.append(f"[SYSTEM]\n{content}")
            continue

        if role == "user":
            if content:
                blocks.append(f"[USER]\n{content}")
            continue

        if role == "assistant":
            tool_calls = message.get("tool_calls") or []
            text_parts = []
            if content:
                text_parts.append(content)
            for tool_call in tool_calls:
                arguments = json.dumps(tool_call.get("arguments") or {}, ensure_ascii=False)
                text_parts.append(f"<tool_call name=\"{tool_call.get('name', '')}\">{arguments}</tool_call>")
            if text_parts:
                blocks.append("[ASSISTANT]\n" + "\n".join(text_parts))
            continue

        if role == "tool":
            name = message.get("name") or "tool"
            blocks.append(f"<tool_result name=\"{name}\">{content}</tool_result>")

    return "\n\n".join(blocks).strip()


def _render_xml_text(messages: list[dict[str, Any]]) -> str:
    parts = ["<conversation>"]
    for message in messages:
        role = str(message.get("role") or "unknown")
        content = _escape_xml(str(message.get("content") or ""))
        parts.append(f"  <message role=\"{role}\">")

        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            parts.append("    <tool_calls>")
            for tool_call in tool_calls:
                arguments = _escape_xml(json.dumps(tool_call.get("arguments") or {}, ensure_ascii=False))
                parts.append(
                    f"      <tool_call id=\"{tool_call.get('id', '')}\" name=\"{tool_call.get('name', '')}\">{arguments}</tool_call>"
                )
            parts.append("    </tool_calls>")

        if content:
            parts.append(f"    <content>{content}</content>")
        parts.append("  </message>")
    parts.append("</conversation>")
    return "\n".join(parts)


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _normalize_system_blocks(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []

    if not isinstance(value, list):
        return []

    normalized = []
    for item in value:
        content = _stringify_content(item)
        if content:
            normalized.append(content)
    return _dedupe_in_order(normalized)


def _stringify_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "")
                if item_type in {"text", "output_text", "thinking"}:
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif item_type == "tool_result":
                    text = str(item.get("content") or "").strip()
                    if text:
                        parts.append(text)
            else:
                text = str(item).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        if "text" in value:
            return str(value.get("text") or "").strip()
        if "content" in value:
            return _stringify_content(value.get("content"))
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value).strip()
    return str(value).strip()


def _dedupe_in_order(values: list[str]) -> list[str]:
    results = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        results.append(value)
    return results


def _read_raw_entries(session_id: str) -> list[dict[str, Any]]:
    content = _read_session_file(session_id, "raw.jsonl")
    if content is None:
        return []

    items = []
    for line in content.decode("utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            items.append(data)
    return items


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _build_sft_filename(format_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"traj-sft-{format_name}-{timestamp}.jsonl"


def _read_traj_content(traj: Trajectory) -> bytes:
    key = traj.oss_key or traj.traj_file_path
    try:
        content = storage.get(key)
        if key.endswith(".gz"):
            content = gzip.decompress(content)
        return content
    except FileNotFoundError:
        pass

    fallback_key = f"sessions/{traj.session_id}/session.traj"
    try:
        return storage.get(fallback_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"traj file not found: {traj.session_id}") from None


def _read_session_file(session_id: str, filename: str) -> Optional[bytes]:
    gz_key = f"sessions/{session_id}/{filename}.gz"
    try:
        content = storage.get(gz_key)
        return gzip.decompress(content)
    except FileNotFoundError:
        pass

    key = f"sessions/{session_id}/{filename}"
    try:
        return storage.get(key)
    except FileNotFoundError:
        return None
