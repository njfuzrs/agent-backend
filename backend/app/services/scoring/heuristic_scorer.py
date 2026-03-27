"""第二层：基于 .traj 文件内容的启发式分析。"""

from .models import HeuristicScoreResult

# 各维度权重
_WEIGHTS = {
    "H01": 0.20,  # 重复调用检测
    "H02": 0.20,  # 工具链合理性
    "H03": 0.15,  # 错误恢复能力
    "H04": 0.15,  # 步骤效率
    "H05": 0.15,  # 编辑后验证
    "H06": 0.15,  # 搜索先行
}

# 工具分类集合
_SEARCH_TOOLS = {"grep", "glob", "Grep", "Glob", "search"}
_READ_TOOLS = {"read", "Read", "cat", "view"}
_EDIT_TOOLS = {"edit", "Edit", "write", "Write", "apply_patch", "patch"}
_EXEC_TOOLS = {"bash", "Bash", "exec_command", "terminal"}


def score_by_heuristics(traj_data: dict) -> HeuristicScoreResult:
    """对 .traj 文件内容执行启发式分析。"""
    trajectory = traj_data.get("trajectory", [])
    if not trajectory:
        return HeuristicScoreResult(heuristic_score=0)

    # 提取 action 步骤序列
    actions = [
        s for s in trajectory
        if s.get("message_type") == "action" and s.get("tool_name")
    ]
    observations = [
        s for s in trajectory
        if s.get("message_type") == "observation"
    ]

    details: dict[str, int] = {}
    patterns: list[str] = []

    # --- H01 重复调用检测 ---
    details["H01"], dup_ratio = _score_duplicate(actions)
    if dup_ratio > 0.3:
        patterns.append("bad:high_duplicate_ratio")

    # --- H02 工具链合理性 ---
    details["H02"], chain_patterns = _score_tool_chain(actions)
    patterns.extend(chain_patterns)

    # --- H03 错误恢复能力 ---
    error_count = sum(1 for s in observations if s.get("is_error"))
    details["H03"] = _score_error_recovery(trajectory, error_count)
    if error_count > 0:
        patterns.append(f"info:error_count={error_count}")

    # --- H04 步骤效率 ---
    effective_ratio = _calc_effective_ratio(actions, dup_ratio)
    details["H04"] = int(effective_ratio * 100)

    # --- H05 编辑后验证 ---
    details["H05"], h05_patterns = _score_edit_then_verify(actions)
    patterns.extend(h05_patterns)

    # --- H06 搜索先行 ---
    details["H06"], h06_patterns = _score_search_first(actions)
    patterns.extend(h06_patterns)

    # 加权总分
    total = sum(details[k] * _WEIGHTS[k] for k in _WEIGHTS)
    score = int(round(total))

    return HeuristicScoreResult(
        heuristic_score=score,
        heuristic_details=details,
        patterns_found=patterns,
        duplicate_ratio=dup_ratio,
        error_count=error_count,
        effective_step_ratio=effective_ratio,
    )


def _score_duplicate(actions: list[dict]) -> tuple[int, float]:
    """H01：检测连续重复调用。返回 (分数, 重复比例)。"""
    if len(actions) < 2:
        return 100, 0.0

    dup_count = 0
    for i in range(1, len(actions)):
        prev, curr = actions[i - 1], actions[i]
        if prev.get("tool_name") != curr.get("tool_name"):
            continue
        prev_input = str(prev.get("tool_input", ""))
        curr_input = str(curr.get("tool_input", ""))
        if prev_input == curr_input:
            dup_count += 1
        elif _jaccard_similarity(prev_input, curr_input) > 0.8:
            dup_count += 1

    ratio = dup_count / len(actions)
    score = max(0, int(100 - ratio * 200))
    return score, round(ratio, 3)


def _score_tool_chain(actions: list[dict]) -> tuple[int, list[str]]:
    """H02：评估工具链是否合理。"""
    if not actions:
        return 50, []

    tool_seq = [_classify_tool(a.get("tool_name", "")) for a in actions]
    patterns: list[str] = []
    score = 60  # 基础分

    # 好模式：搜索 → 读取/编辑
    for i in range(len(tool_seq) - 1):
        if tool_seq[i] == "search" and tool_seq[i + 1] in ("read", "edit"):
            score += 5
            patterns.append("good:search_before_action")
            break

    # 好模式：读取 → 编辑
    for i in range(len(tool_seq) - 1):
        if tool_seq[i] == "read" and tool_seq[i + 1] == "edit":
            score += 5
            patterns.append("good:read_before_edit")
            break

    # 坏模式：连续 3+ 次相同类型
    for i in range(len(tool_seq) - 2):
        if tool_seq[i] == tool_seq[i + 1] == tool_seq[i + 2]:
            score -= 10
            patterns.append(f"bad:triple_{tool_seq[i]}")
            break

    # 坏模式：无 read/search 直接 edit
    if "edit" in tool_seq:
        first_edit = tool_seq.index("edit")
        if not any(t in ("search", "read") for t in tool_seq[:first_edit]):
            score -= 10
            patterns.append("bad:edit_without_context")

    return max(0, min(100, score)), patterns


def _score_error_recovery(trajectory: list[dict], error_count: int) -> int:
    """H03：评估错误恢复能力。"""
    if error_count == 0:
        return 80

    recovered = 0
    for i, step in enumerate(trajectory):
        if not step.get("is_error"):
            continue
        following = trajectory[i + 1: i + 4]
        has_correction = any(
            s.get("message_type") == "action" and s.get("tool_name")
            for s in following
        )
        if has_correction:
            recovered += 1

    if error_count <= 2 and recovered == error_count:
        return 100
    elif recovered > 0:
        return 60
    else:
        return 20


def _score_edit_then_verify(actions: list[dict]) -> tuple[int, list[str]]:
    """H05：编辑后是否有验证步骤。"""
    patterns: list[str] = []
    edit_indices = [
        i for i, a in enumerate(actions)
        if _classify_tool(a.get("tool_name", "")) == "edit"
    ]
    if not edit_indices:
        return 70, []

    verified = 0
    for idx in edit_indices:
        following = actions[idx + 1: idx + 4]
        if any(_classify_tool(a.get("tool_name", "")) == "exec" for a in following):
            verified += 1

    ratio = verified / len(edit_indices)
    if ratio >= 0.5:
        patterns.append("good:edit_then_verify")
        return 100, patterns
    elif ratio > 0:
        return 60, patterns
    else:
        patterns.append("bad:edit_no_verify")
        return 30, patterns


def _score_search_first(actions: list[dict]) -> tuple[int, list[str]]:
    """H06：是否先搜索再操作。"""
    if not actions:
        return 50, []

    patterns: list[str] = []
    first_tool_type = _classify_tool(actions[0].get("tool_name", ""))

    if first_tool_type in ("search", "read"):
        patterns.append("good:search_first")
        return 100, patterns
    elif first_tool_type == "edit":
        patterns.append("bad:edit_first")
        return 20, patterns
    else:
        return 60, patterns


def _classify_tool(name: str) -> str:
    """将工具名归类。"""
    if name in _SEARCH_TOOLS:
        return "search"
    if name in _READ_TOOLS:
        return "read"
    if name in _EDIT_TOOLS:
        return "edit"
    if name in _EXEC_TOOLS:
        return "exec"
    return "other"


def _calc_effective_ratio(actions: list[dict], dup_ratio: float) -> float:
    """计算有效步骤占比。"""
    if not actions:
        return 0.0
    return round(max(0.0, 1.0 - dup_ratio), 3)


def _jaccard_similarity(a: str, b: str) -> float:
    """简单的 Jaccard 相似度（基于 token 集合）。"""
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)
