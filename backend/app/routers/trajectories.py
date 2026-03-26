"""轨迹 CRUD + 搜索 + 详情分段加载 API"""

import gzip
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Trajectory
from app.schemas import (
    TrajectoryListResponse, TrajectoryListItem, TrajectoryMeta,
    TrajectoryStepsResponse, TrajectoryUpdate,
)
from app.services.storage import storage
from app.utils.auth import verify_basic_auth

router = APIRouter(prefix="/trajectories", tags=["trajectories"])

# 允许排序的字段
SORTABLE_FIELDS = {
    "start_time": Trajectory.start_time,
    "total_steps": Trajectory.total_steps,
    "total_tokens": Trajectory.total_tokens,
    "total_cost_usd": Trajectory.total_cost_usd,
    "duration_ms": Trajectory.duration_ms,
    "uploaded_at": Trajectory.uploaded_at,
}


@router.get("", response_model=TrajectoryListResponse, dependencies=[Depends(verify_basic_auth)])
async def list_trajectories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tool_source: Optional[str] = None,
    model: Optional[str] = None,
    exit_status: Optional[str] = None,
    task_type: Optional[str] = None,
    quality_status: Optional[str] = None,
    project_name: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_steps: Optional[int] = None,
    max_steps: Optional[int] = None,
    sort: str = "-start_time",
    db: AsyncSession = Depends(get_db),
):
    """轨迹列表（分页 + 过滤 + 排序）"""
    query = select(Trajectory)
    count_query = select(func.count(Trajectory.id))

    # 软删除过滤：只返回未删除的记录
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
    if min_steps is not None:
        filters.append(Trajectory.total_steps >= min_steps)
    if max_steps is not None:
        filters.append(Trajectory.total_steps <= max_steps)

    for f in filters:
        query = query.where(f)
        count_query = count_query.where(f)

    # 排序
    descending = sort.startswith("-")
    sort_field = sort.lstrip("-")
    column = SORTABLE_FIELDS.get(sort_field, Trajectory.start_time)
    query = query.order_by(desc(column) if descending else asc(column))

    # 分页
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    # 执行
    result = await db.execute(query)
    rows = result.scalars().all()

    count_result = await db.execute(count_query)
    total = count_result.scalar()

    items = [_to_list_item(r) for r in rows]
    return TrajectoryListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/{session_id}", response_model=TrajectoryMeta, dependencies=[Depends(verify_basic_auth)])
async def get_trajectory(session_id: str, db: AsyncSession = Depends(get_db)):
    """获取轨迹元数据"""
    traj = await _get_traj_or_404(session_id, db)
    return _to_meta(traj)


@router.get("/{session_id}/detail/trajectory", response_model=TrajectoryStepsResponse, dependencies=[Depends(verify_basic_auth)])
async def get_trajectory_steps(
    session_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """分页返回 trajectory 数组"""
    traj = await _get_traj_or_404(session_id, db)
    content = _read_traj_content(traj)

    traj_data = json.loads(content)
    all_steps = traj_data.get("trajectory", [])
    total = len(all_steps)
    items = all_steps[offset:offset + limit]

    return TrajectoryStepsResponse(total=total, offset=offset, limit=limit, items=items)


@router.get("/{session_id}/detail/history", dependencies=[Depends(verify_basic_auth)])
async def get_trajectory_history(session_id: str, db: AsyncSession = Depends(get_db)):
    """返回 history 数组"""
    traj = await _get_traj_or_404(session_id, db)
    content = _read_traj_content(traj)

    traj_data = json.loads(content)
    return traj_data.get("history", [])


@router.get("/{session_id}/detail/info", dependencies=[Depends(verify_basic_auth)])
async def get_trajectory_info(session_id: str, db: AsyncSession = Depends(get_db)):
    """返回 info 字段"""
    traj = await _get_traj_or_404(session_id, db)
    content = _read_traj_content(traj)

    traj_data = json.loads(content)
    return traj_data.get("info", {})


@router.get("/{session_id}/detail/raw", dependencies=[Depends(verify_basic_auth)])
async def get_raw_file(session_id: str, db: AsyncSession = Depends(get_db)):
    """下载原始 .traj 文件"""
    traj = await _get_traj_or_404(session_id, db)
    content = _read_traj_content(traj)

    return StreamingResponse(
        iter([content]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={session_id}.traj"},
    )


@router.get("/{session_id}/detail/raw-data", dependencies=[Depends(verify_basic_auth)])
async def get_raw_data(session_id: str, db: AsyncSession = Depends(get_db)):
    """返回 raw.jsonl 原始采集数据"""
    await _get_traj_or_404(session_id, db)
    content = _read_session_file(session_id, "raw.jsonl")
    if content is None:
        raise HTTPException(status_code=404, detail="raw.jsonl not found")

    lines = []
    for line in content.decode("utf-8").splitlines():
        if line.strip():
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return {"session_id": session_id, "total": len(lines), "items": lines}


@router.get("/{session_id}/detail/events", dependencies=[Depends(verify_basic_auth)])
async def get_events(session_id: str, db: AsyncSession = Depends(get_db)):
    """返回 events.jsonl hook 事件数据"""
    await _get_traj_or_404(session_id, db)
    content = _read_session_file(session_id, "events.jsonl")
    if content is None:
        raise HTTPException(status_code=404, detail="events.jsonl not found")

    events = []
    for line in content.decode("utf-8").splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return {"session_id": session_id, "total": len(events), "items": events}


@router.patch("/{session_id}", dependencies=[Depends(verify_basic_auth)])
async def update_trajectory(
    session_id: str,
    update: TrajectoryUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新轨迹标注（评分/标签/任务类型）"""
    traj = await _get_traj_or_404(session_id, db)

    if update.quality_rating is not None:
        traj.quality_rating = update.quality_rating
    if update.quality_status is not None:
        traj.quality_status = update.quality_status
    if update.quality_notes is not None:
        traj.quality_notes = update.quality_notes
    if update.task_type is not None:
        traj.task_type = update.task_type
    if update.project_name is not None:
        traj.project_name = update.project_name
    if update.tags is not None:
        traj.tags = json.dumps(update.tags)

    traj.updated_at = datetime.now(timezone.utc).isoformat()
    await db.commit()
    return {"status": "updated", "session_id": session_id}


@router.delete("/{session_id}", dependencies=[Depends(verify_basic_auth)])
async def delete_trajectory(session_id: str, db: AsyncSession = Depends(get_db)):
    """软删除轨迹（设置 deleted_at，不删除文件）

    文件由定时任务 cleanup_deleted.sh 在 30 天后真正清理。
    """
    traj = await _get_traj_or_404(session_id, db)

    traj.deleted_at = datetime.now(timezone.utc).isoformat()
    await db.commit()
    return {"status": "soft_deleted", "session_id": session_id, "recoverable_until": "30天"}


# ── 内部工具函数 ──

async def _get_traj_or_404(session_id: str, db: AsyncSession) -> Trajectory:
    """获取未删除的轨迹记录，不存在则 404"""
    result = await db.execute(
        select(Trajectory)
        .where(Trajectory.session_id == session_id)
        .where(Trajectory.deleted_at.is_(None))
    )
    traj = result.scalar_one_or_none()
    if not traj:
        raise HTTPException(status_code=404, detail="trajectory not found")
    return traj


def _read_traj_content(traj: Trajectory) -> bytes:
    """读取 traj 文件内容，自动处理压缩和非压缩格式"""
    # 优先使用 oss_key，其次 traj_file_path
    key = traj.oss_key or traj.traj_file_path
    try:
        content = storage.get(key)
        if key.endswith(".gz"):
            content = gzip.decompress(content)
        return content
    except FileNotFoundError:
        pass

    # 兼容：尝试不带 .gz 后缀（旧数据）
    fallback_key = f"sessions/{traj.session_id}/session.traj"
    try:
        return storage.get(fallback_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="traj file not found")


def _read_session_file(session_id: str, filename: str) -> Optional[bytes]:
    """读取 session 目录下的文件，自动尝试 .gz 和非 .gz 格式"""
    # 先尝试压缩版本
    gz_key = f"sessions/{session_id}/{filename}.gz"
    try:
        content = storage.get(gz_key)
        return gzip.decompress(content)
    except FileNotFoundError:
        pass

    # 再尝试非压缩版本
    key = f"sessions/{session_id}/{filename}"
    try:
        return storage.get(key)
    except FileNotFoundError:
        return None


def _parse_json_field(value: str) -> list:
    """安全解析 JSON 数组字段"""
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _to_list_item(r: Trajectory) -> TrajectoryListItem:
    return TrajectoryListItem(
        session_id=r.session_id,
        tool_source=r.tool_source,
        model=r.model,
        start_time=r.start_time,
        end_time=r.end_time,
        duration_ms=r.duration_ms,
        total_steps=r.total_steps,
        total_api_calls=r.total_api_calls,
        total_tokens=r.total_tokens,
        total_cost_usd=r.total_cost_usd,
        exit_status=r.exit_status,
        tools_used=_parse_json_field(r.tools_used),
        first_prompt=r.first_prompt,
        quality_status=r.quality_status,
        quality_rating=r.quality_rating,
        traj_file_size=r.traj_file_size,
        has_thinking=r.has_thinking,
        has_sub_agent=r.has_sub_agent,
        task_type=r.task_type,
        project_name=r.project_name,
        tags=_parse_json_field(r.tags),
    )


def _to_meta(r: Trajectory) -> TrajectoryMeta:
    return TrajectoryMeta(
        session_id=r.session_id,
        tool_source=r.tool_source,
        model=r.model,
        start_time=r.start_time,
        end_time=r.end_time,
        duration_ms=r.duration_ms,
        total_steps=r.total_steps,
        total_api_calls=r.total_api_calls,
        total_tokens=r.total_tokens,
        total_cost_usd=r.total_cost_usd,
        exit_status=r.exit_status,
        tools_used=_parse_json_field(r.tools_used),
        first_prompt=r.first_prompt,
        quality_status=r.quality_status,
        quality_rating=r.quality_rating,
        traj_file_size=r.traj_file_size,
        has_thinking=r.has_thinking,
        has_sub_agent=r.has_sub_agent,
        task_type=r.task_type,
        project_name=r.project_name,
        tags=_parse_json_field(r.tags),
        tokens_sent=r.tokens_sent,
        tokens_received=r.tokens_received,
        cache_read_tokens=r.cache_read_tokens,
        cache_creation_tokens=r.cache_creation_tokens,
        working_directory=r.working_directory,
        files_edited=_parse_json_field(r.files_edited),
        quality_notes=r.quality_notes,
        uploaded_at=r.uploaded_at,
        updated_at=r.updated_at,
    )
