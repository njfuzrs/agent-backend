"""评分结果数据类。"""

from dataclasses import dataclass, field


@dataclass
class RuleScoreResult:
    """第一层：规则引擎评分结果。"""
    rule_score: int = 0                    # 0-100
    rule_details: dict[str, int] = field(default_factory=dict)
    rule_flags: list[str] = field(default_factory=list)
    skip_further: bool = False


@dataclass
class HeuristicScoreResult:
    """第二层：启发式分析评分结果。"""
    heuristic_score: int = 0               # 0-100
    heuristic_details: dict[str, int] = field(default_factory=dict)
    patterns_found: list[str] = field(default_factory=list)
    duplicate_ratio: float = 0.0
    error_count: int = 0
    effective_step_ratio: float = 1.0


@dataclass
class LLMScoreResult:
    """第三层：LLM 深度评估结果。"""
    llm_score: int = 0                     # 0-100
    llm_details: dict[str, int] = field(default_factory=dict)
    llm_reasoning: str = ""
    suggested_task_type: str = ""
    llm_model: str = ""
    llm_tokens_used: int = 0


@dataclass
class ScoringResult:
    """综合评分结果。"""
    ai_score: int = 0
    ai_grade: str = ""
    ai_quality_status: str = "pending"
    rule: RuleScoreResult | None = None
    heuristic: HeuristicScoreResult | None = None
    llm: LLMScoreResult | None = None
    score_version: int = 1
    error: str | None = None
