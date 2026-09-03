"""第一层：基于元数据的规则评分，零成本即时执行。"""

import json
import re

from app.modules.trajectory.model import Trajectory

from .models import RuleScoreResult

# 问候/无效 prompt 模式
_TRIVIAL_PATTERNS = re.compile(
    r"^(你好|hi|hello|hey|test|测试|哈喽|嗨)\s*[!！.。]?\s*$",
    re.IGNORECASE,
)

# 各规则权重
# R01 降权：未完成的轨迹同样有训练价值，完成状态不应是主要评判标准
_WEIGHTS = {
    "R01": 0.10,  # 完成状态（降权，未完成轨迹也有价值）
    "R02": 0.15,  # 步骤数合理性
    "R03": 0.15,  # 工具多样性（提权，工具链丰富度更重要）
    "R04": 0.10,  # 有实质内容
    "R05": 0.10,  # Token 效率
    "R06": 0.05,  # 有思考过程
    "R07": 0.05,  # 成本合理性
    "R08": 0.05,  # 时长合理性
    "R09": 0.15,  # 有文件编辑（提权，有实际编辑更有训练价值）
    "R10": 0.10,  # 非空任务类型（提权）
}


def score_by_rules(traj: Trajectory) -> RuleScoreResult:
    """对单条轨迹执行规则评分。"""
    details: dict[str, int] = {}
    flags: list[str] = []

    # --- R01 完成状态 ---
    # 未完成的轨迹也有训练价值，不应给 0 分
    exit_status = (traj.exit_status or "").strip()
    if exit_status == "end_turn":
        details["R01"] = 100
    elif exit_status in ("tool_use", "partial"):
        details["R01"] = 70
    else:
        details["R01"] = 50

    # --- R02 步骤数合理性 ---
    steps = traj.total_steps or 0
    if steps == 0:
        details["R02"] = 0
        flags.append("EMPTY_TRAJECTORY")
    elif steps == 1:
        details["R02"] = 20
    elif 2 <= steps <= 15:
        details["R02"] = 100
    elif 16 <= steps <= 25:
        details["R02"] = 60
    elif 26 <= steps <= 50:
        details["R02"] = 30
    else:
        details["R02"] = 10
        flags.append("EXCESSIVE_STEPS")

    # --- R03 工具多样性 ---
    tools = _parse_json_list(traj.tools_used)
    tool_count = len(tools)
    if tool_count == 0:
        details["R03"] = 0
        flags.append("ZERO_TOOLS")
    elif tool_count == 1:
        details["R03"] = 40
    elif tool_count == 2:
        details["R03"] = 70
    else:
        details["R03"] = 100

    # --- R04 有实质内容 ---
    prompt = (traj.first_prompt or "").strip()
    if not prompt or len(prompt) < 5:
        details["R04"] = 10
        if steps < 2 and exit_status != "end_turn":
            flags.append("TRIVIAL_PROMPT")
    elif _TRIVIAL_PATTERNS.match(prompt):
        details["R04"] = 20
        flags.append("TRIVIAL_PROMPT")
    elif len(prompt) < 10:
        details["R04"] = 50
    else:
        details["R04"] = 100

    # --- R05 Token 效率 ---
    total_tokens = traj.total_tokens or 0
    if steps > 0 and total_tokens > 0:
        tokens_per_step = total_tokens / steps
        if tokens_per_step < 50000:
            details["R05"] = 100
        elif tokens_per_step < 100000:
            details["R05"] = 70
        else:
            details["R05"] = 40
    else:
        details["R05"] = 50

    # --- R06 有思考过程 ---
    details["R06"] = 100 if traj.has_thinking else 60

    # --- R07 成本合理性 ---
    cost = traj.total_cost_usd or 0.0
    if 0 < cost < 5.0:
        details["R07"] = 100
    elif cost == 0:
        details["R07"] = 50
    else:
        details["R07"] = 30

    # --- R08 时长合理性 ---
    duration = traj.duration_ms or 0
    if 10_000 <= duration <= 1_800_000:
        details["R08"] = 100
    elif duration < 10_000:
        details["R08"] = 30
    else:
        details["R08"] = 50

    # --- R09 有文件编辑 ---
    files = _parse_json_list(traj.files_edited)
    details["R09"] = 100 if files else 40

    # --- R10 非空任务类型 ---
    details["R10"] = 100 if traj.task_type else 60

    # 加权计算总分
    total = sum(details[k] * _WEIGHTS[k] for k in _WEIGHTS)
    rule_score = int(round(total))

    # 判断是否跳过后续层
    fatal_flags = {"EMPTY_TRAJECTORY", "EXCESSIVE_STEPS", "ZERO_TOOLS"}
    skip = bool(fatal_flags & set(flags))

    return RuleScoreResult(
        rule_score=rule_score,
        rule_details=details,
        rule_flags=flags,
        skip_further=skip,
    )


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []
