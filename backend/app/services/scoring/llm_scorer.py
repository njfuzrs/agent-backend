"""第三层：调用 LLM API 对轨迹进行语义级评估。"""

import json
import logging
from typing import Any

import httpx

from app.config import settings
from .models import LLMScoreResult

logger = logging.getLogger(__name__)

# 评估 prompt 模板
_EVAL_PROMPT = """你是一个 Agent 轨迹质量评估专家。请评估以下 Agent 轨迹的质量，用于筛选 SFT 训练数据。

## 轨迹摘要
- 用户任务：{first_prompt}
- 工具来源：{tool_source}
- 模型：{model}
- 完成状态：{exit_status}
- 总步骤数：{total_steps}
- 使用工具：{tools_used}
- 编辑文件：{files_edited}
- 耗时：{duration_ms}ms
- Token 消耗：{total_tokens}

## 工具调用序列
{tool_sequence}

## 评估维度（每项 0-100 分）
1. task_complexity：任务本身的难度和学习价值（简单问候=0，多文件重构=100）
2. solution_quality：工具选择是否合理，步骤是否高效
3. trainability：这条轨迹作为 SFT 训练数据的价值
4. completeness：任务是否被完整解决

请严格以 JSON 格式返回，不要包含其他内容：
{{"task_complexity": <int>, "solution_quality": <int>, "trainability": <int>, "completeness": <int>, "overall": <int>, "reasoning": "<一句话评语>", "suggested_task_type": "<bug_fix|feature|refactor|explain|test|other>"}}"""


async def score_by_llm(
    traj_meta: dict[str, Any],
    tool_sequence: str,
) -> LLMScoreResult:
    """调用 LLM 评估轨迹质量。"""
    prompt = _EVAL_PROMPT.format(
        first_prompt=traj_meta.get("first_prompt", "")[:500],
        tool_source=traj_meta.get("tool_source", ""),
        model=traj_meta.get("model", ""),
        exit_status=traj_meta.get("exit_status", ""),
        total_steps=traj_meta.get("total_steps", 0),
        tools_used=traj_meta.get("tools_used", "[]"),
        files_edited=traj_meta.get("files_edited", "[]"),
        duration_ms=traj_meta.get("duration_ms", 0),
        total_tokens=traj_meta.get("total_tokens", 0),
        tool_sequence=tool_sequence[:3000],
    )

    model = settings.SCORING_LLM_MODEL
    result = LLMScoreResult(llm_model=model)

    try:
        response = await _call_llm(prompt, model)
        parsed = _parse_llm_response(response["content"])
        result.llm_score = parsed.get("overall", 0)
        result.llm_details = {
            k: parsed.get(k, 0)
            for k in ("task_complexity", "solution_quality",
                       "trainability", "completeness")
        }
        result.llm_reasoning = parsed.get("reasoning", "")
        result.suggested_task_type = parsed.get("suggested_task_type", "")
        result.llm_tokens_used = response.get("tokens_used", 0)
    except Exception as e:
        logger.error("LLM 评分失败: %s", e)
        result.llm_reasoning = f"评分失败: {e}"

    return result


async def _call_llm(prompt: str, model: str) -> dict:
    """调用 LLM API（兼容 OpenAI 格式）。"""
    if not settings.SCORING_LLM_BASE_URL or not settings.SCORING_LLM_API_KEY:
        raise ValueError("未配置 SCORING_LLM_BASE_URL 或 SCORING_LLM_API_KEY")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.SCORING_LLM_API_KEY}",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{settings.SCORING_LLM_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    choice = data["choices"][0]["message"]
    usage = data.get("usage", {})
    return {
        "content": choice["content"],
        "tokens_used": usage.get("total_tokens", 0),
    }


def _parse_llm_response(content: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 块
    if "```" in content:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end])
            except json.JSONDecodeError:
                pass
    return {}


def build_tool_sequence_summary(traj_data: dict) -> str:
    """从 .traj 数据构建工具调用序列摘要。"""
    trajectory = traj_data.get("trajectory", [])
    lines = []
    step_idx = 0
    for step in trajectory:
        if step.get("message_type") != "action":
            continue
        tool_name = step.get("tool_name", "")
        if not tool_name:
            continue
        step_idx += 1
        tool_input = step.get("tool_input", {})
        summary = _summarize_input(tool_input)
        lines.append(f"{step_idx}. {tool_name}({summary})")
        if step_idx >= 30:
            lines.append("... (截断)")
            break
    return "\n".join(lines) if lines else "(无工具调用)"


def _summarize_input(tool_input: Any) -> str:
    """提取工具输入的关键信息。"""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:80]
    for key in ("file_path", "path", "command", "pattern", "description"):
        if key in tool_input:
            return f'{key}="{str(tool_input[key])[:60]}"'
    return json.dumps(tool_input, ensure_ascii=False)[:80]
