"""评分引擎：编排三层评分流水线。"""

import gzip
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trajectory.model import Trajectory
from app.modules.trajectory.service.storage import storage

from .heuristic_scorer import score_by_heuristics
from .llm_scorer import build_tool_sequence_summary, score_by_llm
from .models import ScoringResult
from .rule_scorer import score_by_rules

logger = logging.getLogger(__name__)

# 当前评分规则版本，规则变更时递增
SCORE_VERSION = 3


async def score_trajectory(
    traj: Trajectory,
    db: AsyncSession,
    *,
    run_heuristic: bool = True,
    run_llm: bool = False,
) -> ScoringResult:
    """对单条轨迹执行评分并写入数据库。

    Args:
        traj: 轨迹 ORM 对象
        db: 数据库 session
        run_heuristic: 是否执行第二层启发式分析
        run_llm: 是否执行第三层 LLM 评估
    """
    result = ScoringResult(score_version=SCORE_VERSION)

    try:
        # 第一层：规则引擎（始终执行）
        rule_result = score_by_rules(traj)
        result.rule = rule_result

        # 如果有致命 flag，跳过后续层
        if rule_result.skip_further:
            result.ai_score = rule_result.rule_score
            result.ai_quality_status = "auto_rejected"
            # 有致命 flag 时 grade 最高为 D，避免高分但被拒绝的困惑
            result.ai_grade = _score_to_grade(min(result.ai_score, 39))
            _persist_result(traj, result)
            await db.commit()
            return result

        heuristic_result = None
        llm_result = None

        # 第二层：启发式分析
        if run_heuristic:
            traj_data = _load_traj_data(traj)
            if traj_data:
                heuristic_result = score_by_heuristics(traj_data)
                result.heuristic = heuristic_result

                # 第三层：LLM 评估
                if run_llm:
                    meta = _build_meta_dict(traj)
                    tool_seq = build_tool_sequence_summary(traj_data)
                    llm_result = await score_by_llm(meta, tool_seq)
                    result.llm = llm_result

        # 综合评分
        result.ai_score, result.ai_quality_status = _compute_final(
            rule_result, heuristic_result, llm_result,
        )
        result.ai_grade = _score_to_grade(result.ai_score)

    except Exception as e:
        logger.error("评分异常 session_id=%s: %s", traj.session_id, e)
        result.ai_quality_status = "error"
        result.error = str(e)

    _persist_result(traj, result)
    await db.commit()
    return result


async def batch_score(
    trajectories: list[Trajectory],
    db: AsyncSession,
    *,
    run_heuristic: bool = True,
    run_llm: bool = False,
) -> list[ScoringResult]:
    """批量评分。"""
    results = []
    for traj in trajectories:
        r = await score_trajectory(
            traj, db,
            run_heuristic=run_heuristic,
            run_llm=run_llm,
        )
        results.append(r)
    return results


def _compute_final(rule, heuristic, llm) -> tuple[int, str]:
    """综合三层分数。"""
    from app.core.config import settings

    if llm is not None and heuristic is not None:
        score = int(
            rule.rule_score * 0.2
            + heuristic.heuristic_score * 0.3
            + llm.llm_score * 0.5
        )
    elif heuristic is not None:
        score = int(
            rule.rule_score * 0.35
            + heuristic.heuristic_score * 0.65
        )
    else:
        score = rule.rule_score

    if score >= settings.SCORING_THRESHOLD_APPROVED:
        status = "auto_approved"
    elif score >= settings.SCORING_THRESHOLD_REJECTED:
        status = "needs_review"
    else:
        status = "auto_rejected"

    return score, status


def _score_to_grade(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "F"


def _persist_result(traj: Trajectory, result: ScoringResult) -> None:
    """将评分结果写入 Trajectory ORM 对象。"""
    traj.ai_score = result.ai_score
    traj.ai_quality_status = result.ai_quality_status
    traj.ai_grade = result.ai_grade
    traj.score_version = result.score_version
    traj.scored_at = datetime.now(timezone.utc).isoformat()

    if result.rule:
        traj.rule_score = result.rule.rule_score
        traj.rule_details = json.dumps(result.rule.rule_details, ensure_ascii=False)
        traj.rule_flags = json.dumps(result.rule.rule_flags, ensure_ascii=False)

    if result.heuristic:
        traj.heuristic_score = result.heuristic.heuristic_score
        traj.heuristic_details = json.dumps(result.heuristic.heuristic_details, ensure_ascii=False)
        traj.heuristic_patterns = json.dumps(result.heuristic.patterns_found, ensure_ascii=False)

    if result.llm:
        traj.llm_score = result.llm.llm_score
        traj.llm_details = json.dumps(result.llm.llm_details, ensure_ascii=False)
        traj.llm_reasoning = result.llm.llm_reasoning
        traj.llm_suggested_task_type = result.llm.suggested_task_type
        traj.llm_eval_model = result.llm.llm_model


def _load_traj_data(traj: Trajectory) -> dict | None:
    """读取 .traj 文件内容。"""
    key = traj.oss_key or traj.traj_file_path
    try:
        content = storage.get(key)
        if key.endswith(".gz"):
            content = gzip.decompress(content)
        return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # fallback
    fallback = f"sessions/{traj.session_id}/session.traj"
    try:
        content = storage.get(fallback)
        return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _build_meta_dict(traj: Trajectory) -> dict:
    """构建传给 LLM 的元数据字典。"""
    return {
        "first_prompt": traj.first_prompt,
        "tool_source": traj.tool_source,
        "model": traj.model,
        "exit_status": traj.exit_status,
        "total_steps": traj.total_steps,
        "tools_used": traj.tools_used,
        "files_edited": traj.files_edited,
        "duration_ms": traj.duration_ms,
        "total_tokens": traj.total_tokens,
    }
