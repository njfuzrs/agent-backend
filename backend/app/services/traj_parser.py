""".traj 文件解析服务：从 .traj JSON 中提取元数据写入 SQLite"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def parse_traj_content(content: bytes, tool_source: str = "claude-code") -> dict:
    """解析 .traj 文件内容，提取元数据字段。

    返回一个 dict，字段与 Trajectory ORM 模型对应。
    """
    traj_data = json.loads(content)
    metadata = traj_data.get("metadata", {})
    info = traj_data.get("info", {})
    model_stats = info.get("model_stats", {})

    session_id = metadata.get("session_id", "")

    # 提取 first_prompt
    first_prompt = _extract_first_prompt(traj_data, metadata)

    # 计算 duration_ms
    duration_ms = _calc_duration(metadata)

    # 从 trajectory 数组提取工具使用信息（metadata 中可能为空）
    trajectory = traj_data.get("trajectory", [])
    tools_used = metadata.get("tools_used", [])
    if not tools_used and trajectory:
        tools_used = list({
            step.get("tool_name")
            for step in trajectory
            if step.get("tool_name")
        })

    total_steps = metadata.get("total_steps", 0) or len(trajectory)

    now_utc = datetime.now(timezone.utc).isoformat()

    return {
        "session_id": session_id,
        "tool_source": tool_source,
        "model": metadata.get("model", ""),
        "start_time": metadata.get("start_time"),
        "end_time": metadata.get("end_time"),
        "duration_ms": duration_ms,
        "tokens_sent": model_stats.get("tokens_sent", 0),
        "tokens_received": model_stats.get("tokens_received", 0),
        "cache_read_tokens": model_stats.get("cache_read_tokens", 0),
        "cache_creation_tokens": model_stats.get("cache_creation_tokens", 0),
        "total_tokens": metadata.get("total_tokens", 0),
        "total_cost_usd": metadata.get("total_cost_usd", model_stats.get("total_cost_usd", 0.0)),
        "total_steps": total_steps,
        "total_api_calls": model_stats.get("api_calls", metadata.get("total_api_calls", 0)),
        "exit_status": metadata.get("exit_status", info.get("exit_status", "")),
        "tools_used": json.dumps(tools_used),
        "files_edited": json.dumps(metadata.get("files_edited", [])),
        "working_directory": metadata.get("working_directory", ""),
        "has_thinking": metadata.get("has_thinking", info.get("has_thinking", False)),
        "has_sub_agent": metadata.get("has_sub_agent", False),
        "first_prompt": first_prompt,
        "traj_file_size": len(content),
        "uploaded_at": now_utc,
        "updated_at": now_utc,
    }


def _extract_first_prompt(traj_data: dict, metadata: dict) -> str:
    """提取首条用户输入，截断到 500 字符"""
    # 优先从 metadata.user_prompts 取
    if metadata.get("user_prompts"):
        return str(metadata["user_prompts"][0])[:500]

    # 其次从 history 中找第一条 user 消息
    for entry in traj_data.get("history", []):
        if entry.get("role") == "user":
            content = entry.get("content", "")
            if isinstance(content, list):
                # content 可能是 [{"type": "text", "text": "..."}] 格式
                texts = [
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                content = " ".join(texts)
            return str(content)[:500]

    return ""


def _calc_duration(metadata: dict) -> Optional[int]:
    """计算 duration_ms"""
    start = metadata.get("start_time")
    end = metadata.get("end_time")
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return int((e - s).total_seconds() * 1000)
    except (ValueError, TypeError, AttributeError):
        return None
