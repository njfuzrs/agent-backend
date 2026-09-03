"""AI 评分 API 路由。"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.data_plane import verify_basic_auth
from app.core.db import get_db
from app.modules.trajectory.model import Trajectory
from app.modules.trajectory.service.scoring.engine import (
    SCORE_VERSION,
    score_trajectory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scoring", tags=["scoring"])


# ── 请求模型 ──

class BatchScoreRequest(BaseModel):
    """批量评分请求。"""
    session_ids: list[str] | None = None
    ai_quality_status: str | None = None
    score_version_lt: int | None = None
    limit: int = Field(100, ge=1, le=1000)
    run_heuristic: bool = True
    run_llm: bool = False


# ── 接口 ──

@router.post("/score/{session_id}", dependencies=[Depends(verify_basic_auth)])
async def score_single(
    session_id: str,
    run_heuristic: bool = Query(True),
    run_llm: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """对单条轨迹执行 AI 评分。"""
    traj = await _get_traj_or_404(db, session_id)
    result = await score_trajectory(
        traj, db,
        run_heuristic=run_heuristic,
        run_llm=run_llm,
    )
    return _to_score_response(traj, result)


@router.post("/batch", dependencies=[Depends(verify_basic_auth)])
async def score_batch(
    request: BatchScoreRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """批量评分（异步执行）。"""
    query = select(Trajectory).where(Trajectory.deleted_at.is_(None))
    if request.session_ids:
        query = query.where(Trajectory.session_id.in_(request.session_ids))
    if request.ai_quality_status:
        query = query.where(Trajectory.ai_quality_status == request.ai_quality_status)
    if request.score_version_lt is not None:
        query = query.where(Trajectory.score_version < request.score_version_lt)
    query = query.limit(request.limit)

    result = await db.execute(query)
    trajectories = list(result.scalars().all())

    if not trajectories:
        return {"status": "no_trajectories_to_score", "count": 0}

    # 收集 session_id 后在后台任务中重新查询并评分
    session_ids = [t.session_id for t in trajectories]
    background_tasks.add_task(
        _run_batch_score, session_ids, request.run_heuristic, request.run_llm,
    )

    return {
        "status": "scoring_started",
        "count": len(session_ids),
        "session_ids": session_ids,
    }


@router.post("/rescore-all", dependencies=[Depends(verify_basic_auth)])
async def rescore_all(
    background_tasks: BackgroundTasks,
    run_heuristic: bool = Query(True),
    run_llm: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """全量重新评分（规则版本变更后）。"""
    count_result = await db.execute(
        select(func.count(Trajectory.id))
        .where(Trajectory.deleted_at.is_(None))
        .where(Trajectory.score_version < SCORE_VERSION)
    )
    total = count_result.scalar() or 0

    if total == 0:
        return {"status": "all_up_to_date", "total": 0}

    background_tasks.add_task(
        _run_rescore_all, run_heuristic, run_llm,
    )

    return {
        "status": "rescore_started",
        "total_pending": total,
        "target_version": SCORE_VERSION,
    }


@router.get("/stats", dependencies=[Depends(verify_basic_auth)])
async def scoring_stats(db: AsyncSession = Depends(get_db)):
    """评分统计概览。"""
    # 各状态分布
    status_result = await db.execute(
        select(Trajectory.ai_quality_status, func.count(Trajectory.id))
        .where(Trajectory.deleted_at.is_(None))
        .group_by(Trajectory.ai_quality_status)
    )
    status_dist = {row[0]: row[1] for row in status_result.all()}

    # 等级分布
    grade_result = await db.execute(
        select(Trajectory.ai_grade, func.count(Trajectory.id))
        .where(Trajectory.deleted_at.is_(None))
        .where(Trajectory.ai_grade != "")
        .group_by(Trajectory.ai_grade)
    )
    grade_dist = {row[0]: row[1] for row in grade_result.all()}

    # 平均分
    avg_result = await db.execute(
        select(func.avg(Trajectory.ai_score))
        .where(Trajectory.deleted_at.is_(None))
        .where(Trajectory.ai_score.isnot(None))
    )
    avg_score = round(avg_result.scalar() or 0, 1)

    return {
        "status_distribution": status_dist,
        "grade_distribution": grade_dist,
        "avg_score": avg_score,
        "total_scored": sum(v for k, v in status_dist.items() if k != "pending"),
        "total_pending": status_dist.get("pending", 0),
    }


@router.get("/{session_id}", dependencies=[Depends(verify_basic_auth)])
async def get_score_detail(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """获取单条轨迹的完整评分详情。"""
    traj = await _get_traj_or_404(db, session_id)
    return {
        "session_id": traj.session_id,
        "ai_score": traj.ai_score,
        "ai_grade": traj.ai_grade,
        "ai_quality_status": traj.ai_quality_status,
        "rule_score": traj.rule_score,
        "rule_details": _safe_json(traj.rule_details, {}),
        "rule_flags": _safe_json(traj.rule_flags, []),
        "heuristic_score": traj.heuristic_score,
        "heuristic_details": _safe_json(traj.heuristic_details, {}),
        "heuristic_patterns": _safe_json(traj.heuristic_patterns, []),
        "llm_score": traj.llm_score,
        "llm_details": _safe_json(traj.llm_details, {}),
        "llm_reasoning": traj.llm_reasoning,
        "llm_suggested_task_type": traj.llm_suggested_task_type,
        "llm_eval_model": traj.llm_eval_model,
        "scored_at": traj.scored_at,
        "score_version": traj.score_version,
    }


# ── 内部函数 ──

async def _get_traj_or_404(db: AsyncSession, session_id: str) -> Trajectory:
    result = await db.execute(
        select(Trajectory)
        .where(Trajectory.session_id == session_id)
        .where(Trajectory.deleted_at.is_(None))
    )
    traj = result.scalar_one_or_none()
    if not traj:
        raise HTTPException(status_code=404, detail="trajectory not found")
    return traj


def _safe_json(value: str | None, default):
    """安全解析 JSON 字段，容错空字符串和无效值。"""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _to_score_response(traj: Trajectory, result) -> dict:
    return {
        "session_id": traj.session_id,
        "ai_score": traj.ai_score,
        "ai_grade": traj.ai_grade,
        "ai_quality_status": traj.ai_quality_status,
        "rule_score": traj.rule_score,
        "rule_details": _safe_json(traj.rule_details, {}),
        "rule_flags": _safe_json(traj.rule_flags, []),
        "heuristic_score": traj.heuristic_score,
        "heuristic_details": _safe_json(traj.heuristic_details, {}),
        "heuristic_patterns": _safe_json(traj.heuristic_patterns, []),
        "llm_score": traj.llm_score,
        "llm_reasoning": traj.llm_reasoning or "",
        "scored_at": traj.scored_at,
        "score_version": traj.score_version,
    }


async def _run_batch_score(
    session_ids: list[str],
    run_heuristic: bool,
    run_llm: bool,
):
    """后台批量评分任务。"""
    from app.core.db import async_session

    async with async_session() as db:
        for sid in session_ids:
            try:
                result = await db.execute(
                    select(Trajectory)
                    .where(Trajectory.session_id == sid)
                    .where(Trajectory.deleted_at.is_(None))
                )
                traj = result.scalar_one_or_none()
                if traj:
                    await score_trajectory(
                        traj, db,
                        run_heuristic=run_heuristic,
                        run_llm=run_llm,
                    )
            except Exception as e:
                logger.error("批量评分失败 session_id=%s: %s", sid, e)

    logger.info("批量评分完成，共 %d 条", len(session_ids))


async def _run_rescore_all(run_heuristic: bool, run_llm: bool):
    """后台全量重新评分任务。"""
    from app.core.db import async_session

    batch_size = 100
    total_scored = 0

    async with async_session() as db:
        while True:
            result = await db.execute(
                select(Trajectory)
                .where(Trajectory.deleted_at.is_(None))
                .where(Trajectory.score_version < SCORE_VERSION)
                .limit(batch_size)
            )
            trajectories = list(result.scalars().all())
            if not trajectories:
                break

            for traj in trajectories:
                try:
                    await score_trajectory(
                        traj, db,
                        run_heuristic=run_heuristic,
                        run_llm=run_llm,
                    )
                    total_scored += 1
                except Exception as e:
                    logger.error("重新评分失败 session_id=%s: %s", traj.session_id, e)

    logger.info("全量重新评分完成，共 %d 条", total_scored)
