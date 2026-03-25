"""上传 API：单个/批量上传 .traj 文件"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Trajectory
from app.schemas import UploadResponse
from app.services.traj_parser import parse_traj_content
from app.utils.auth import verify_upload_token

router = APIRouter(prefix="/upload", tags=["upload"])

DATA_DIR = Path(settings.TRAJ_FILES_DIR)


@router.post("/traj", response_model=UploadResponse, dependencies=[Depends(verify_upload_token)])
async def upload_traj(
    file: UploadFile = File(...),
    tool_source: str = Form("claude-code"),
    project_name: str = Form(""),
    tags: str = Form(""),
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """上传单个 .traj 文件"""
    content = await file.read()

    # 解析 .traj
    try:
        parsed = parse_traj_content(content, tool_source)
    except (json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"无效的 .traj 文件: {e}")

    session_id = parsed["session_id"]
    if not session_id:
        session_id = str(uuid.uuid4())
        parsed["session_id"] = session_id

    # 检查是否已存在
    result = await db.execute(select(Trajectory).where(Trajectory.session_id == session_id))
    existing = result.scalar_one_or_none()

    if existing and not force:
        raise HTTPException(
            status_code=409,
            detail="trajectory already exists",
            headers={"X-Session-Id": session_id},
        )

    # 存储 .traj 文件到磁盘
    dest = DATA_DIR / tool_source / f"{session_id}.traj"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)

    parsed["traj_file_path"] = str(dest.relative_to(DATA_DIR.parent))

    # 覆盖 project_name 和 tags（如果上传时指定了）
    if project_name:
        parsed["project_name"] = project_name
    if tags:
        parsed["tags"] = json.dumps([t.strip() for t in tags.split(",") if t.strip()])

    if existing and force:
        # 更新已有记录
        for key, value in parsed.items():
            if key != "session_id":
                setattr(existing, key, value)
        await db.commit()
        return UploadResponse(session_id=session_id, status="updated", metadata=_summary(parsed))

    # 新建记录
    record = Trajectory(**parsed)
    db.add(record)
    await db.commit()
    return UploadResponse(session_id=session_id, status="created", metadata=_summary(parsed))


@router.post("/batch", dependencies=[Depends(verify_upload_token)])
async def upload_batch(
    files: list[UploadFile] = File(...),
    tool_source: str = Form("claude-code"),
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """批量上传 .traj 文件"""
    results = []
    for file in files:
        try:
            content = await file.read()
            parsed = parse_traj_content(content, tool_source)
            session_id = parsed["session_id"] or str(uuid.uuid4())
            parsed["session_id"] = session_id

            result = await db.execute(select(Trajectory).where(Trajectory.session_id == session_id))
            existing = result.scalar_one_or_none()

            if existing and not force:
                results.append({"session_id": session_id, "status": "skipped", "reason": "already exists"})
                continue

            dest = DATA_DIR / tool_source / f"{session_id}.traj"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            parsed["traj_file_path"] = str(dest.relative_to(DATA_DIR.parent))

            if existing and force:
                for key, value in parsed.items():
                    if key != "session_id":
                        setattr(existing, key, value)
                results.append({"session_id": session_id, "status": "updated"})
            else:
                db.add(Trajectory(**parsed))
                results.append({"session_id": session_id, "status": "created"})

        except Exception as e:
            results.append({"file": file.filename, "status": "error", "reason": str(e)})

    await db.commit()
    return {"total": len(files), "results": results}


@router.post("/reindex", dependencies=[Depends(verify_upload_token)])
async def reindex(db: AsyncSession = Depends(get_db)):
    """扫描 traj_files/ 目录，将未入库的 .traj 文件解析并写入 SQLite"""
    scanned = 0
    new = 0
    errors = 0

    for traj_file in DATA_DIR.rglob("*.traj"):
        scanned += 1
        try:
            content = traj_file.read_bytes()
            parsed = parse_traj_content(content)
            session_id = parsed["session_id"]
            if not session_id:
                continue

            result = await db.execute(select(Trajectory).where(Trajectory.session_id == session_id))
            if result.scalar_one_or_none():
                continue

            # 推断 tool_source 从目录名
            tool_source = traj_file.parent.name
            if tool_source == "traj_files":
                tool_source = "claude-code"
            parsed["tool_source"] = tool_source
            parsed["traj_file_path"] = str(traj_file.relative_to(DATA_DIR.parent))

            db.add(Trajectory(**parsed))
            new += 1
        except Exception:
            errors += 1

    await db.commit()
    return {"scanned": scanned, "new": new, "skipped": scanned - new - errors, "errors": errors}


def _summary(parsed: dict) -> dict:
    """返回上传响应中的元数据摘要"""
    return {
        "model": parsed.get("model", ""),
        "total_steps": parsed.get("total_steps", 0),
        "total_cost_usd": parsed.get("total_cost_usd", 0.0),
        "exit_status": parsed.get("exit_status", ""),
        "first_prompt": parsed.get("first_prompt", "")[:100],
    }
