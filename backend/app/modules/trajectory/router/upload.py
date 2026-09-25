"""上传 API：单个/批量上传 .traj 文件，会话维度上传"""

import gzip
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.data_plane import verify_upload_token
from app.core.config import settings
from app.core.db import get_db
from app.core.logging import get_logger
from app.modules.trajectory.model import Trajectory
from app.modules.trajectory.schemas import UploadResponse
from app.modules.trajectory.service.storage import compute_sha256, storage
from app.modules.trajectory.service.tool_steps import sync_tool_steps
from app.modules.trajectory.service.traj_parser import parse_traj_content

logger = get_logger("agent.trajectory")

router = APIRouter(prefix="/upload", tags=["upload"])

SESSIONS_DIR = Path(settings.SESSIONS_DIR)

# 会话维度上传：file_type → 磁盘文件名
_FILE_TYPE_MAP = {
    "traj": "session.traj",
    "raw": "raw.jsonl",
    "events": "events.jsonl",
}


@router.post("/session-file", dependencies=[Depends(verify_upload_token)])
async def upload_session_file(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    file_type: str = Form(...),
    tool_source: str = Form("claude-code"),
    force: bool = Query(False),
    compressed: bool = Form(False),
    user_id: Optional[str] = Form(None),
    device_id: Optional[str] = Form(None),
    x_content_sha256: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """会话维度上传：按 session_id + file_type 存储

    file_type: traj | raw | events
    - traj: 解析元数据写入 DB + 存储文件
    - raw/events: 仅存储文件，不写 DB

    新增参数（均可选，向后兼容）：
    - compressed: 客户端是否已 gzip 压缩
    - user_id / device_id: 上传来源标识
    - x_content_sha256: 客户端计算的 SHA256，用于传输校验
    """
    if file_type not in _FILE_TYPE_MAP:
        raise HTTPException(status_code=400, detail=f"无效的 file_type: {file_type}，允许: {list(_FILE_TYPE_MAP.keys())}")

    content = await file.read()

    # 自动检测 gzip：0x1f8b 是 gzip magic number
    is_gzip = len(content) >= 2 and content[0] == 0x1f and content[1] == 0x8b
    if is_gzip:
        compressed = True

    # SHA256 校验。两端哈希都不进日志：哈希能反查内容（方案 §3.6）。
    server_hash = compute_sha256(content)
    if x_content_sha256 and server_hash != x_content_sha256:
        logger.warning(
            "upload rejected",
            event="upload_rejected",
            reason="checksum_mismatch",
            session_id=session_id,
            file_type=file_type,
        )
        raise HTTPException(status_code=400, detail={
            "error": "hash_mismatch",
            "expected": x_content_sha256,
            "actual": server_hash,
        })

    # 确定存储 key
    filename = _FILE_TYPE_MAP[file_type]
    if compressed:
        storage_key = f"sessions/{session_id}/{filename}.gz"
    else:
        storage_key = f"sessions/{session_id}/{filename}"

    # 去重：文件已存在则跳过（除非 force）
    if storage.exists(storage_key) and not force:
        if file_type == "traj":
            raise HTTPException(status_code=409, detail="trajectory already exists",
                                headers={"X-Session-Id": session_id})
        return {"session_id": session_id, "file_type": file_type, "status": "skipped",
                "reason": "already exists", "sha256": server_hash}

    # 存储文件
    storage.put(storage_key, content)

    # traj 类型需要解析元数据并写入/更新 DB
    if file_type == "traj":
        try:
            raw_content = gzip.decompress(content) if compressed else content
            traj_data = json.loads(raw_content)
            parsed = parse_traj_content(raw_content, tool_source)
        except (json.JSONDecodeError, KeyError) as e:
            raise HTTPException(status_code=400, detail=f"无效的 .traj 文件: {e}") from e

        parsed["session_id"] = session_id
        parsed["traj_file_path"] = storage_key
        parsed["oss_key"] = storage_key if settings.is_oss else None
        parsed["sha256"] = server_hash
        parsed["file_size"] = len(content)
        parsed["user_id"] = user_id
        parsed["device_id"] = device_id

        result = await db.execute(select(Trajectory).where(Trajectory.session_id == session_id))
        existing = result.scalar_one_or_none()

        if existing:
            for key, value in parsed.items():
                if key != "session_id":
                    setattr(existing, key, value)
            await db.flush()
            await sync_tool_steps(db, existing, traj_data)
            await db.commit()
            return UploadResponse(session_id=session_id, status="updated",
                                  metadata=_summary(parsed), sha256=server_hash, oss_key=storage_key)

        record = Trajectory(**parsed)
        db.add(record)
        await db.flush()
        await sync_tool_steps(db, record, traj_data)
        await db.commit()
        return UploadResponse(session_id=session_id, status="created",
                              metadata=_summary(parsed), sha256=server_hash, oss_key=storage_key)

    # raw / events 类型：仅存储文件
    return {"session_id": session_id, "file_type": file_type, "status": "saved",
            "size": len(content), "sha256": server_hash, "oss_key": storage_key}


@router.post("/traj", response_model=UploadResponse, dependencies=[Depends(verify_upload_token)])
async def upload_traj(
    file: UploadFile = File(...),
    tool_source: str = Form("claude-code"),
    project_name: str = Form(""),
    tags: str = Form(""),
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """上传单个 .traj 文件（旧接口，向后兼容）"""
    content = await file.read()

    # 解析 .traj
    try:
        traj_data = json.loads(content)
        parsed = parse_traj_content(content, tool_source)
    except (json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"无效的 .traj 文件: {e}") from e

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

    # 存储文件
    storage_key = f"sessions/{session_id}/session.traj"
    storage.put(storage_key, content)

    server_hash = compute_sha256(content)
    parsed["traj_file_path"] = storage_key
    parsed["oss_key"] = storage_key if settings.is_oss else None
    parsed["sha256"] = server_hash
    parsed["file_size"] = len(content)

    # 覆盖 project_name 和 tags（如果上传时指定了）
    if project_name:
        parsed["project_name"] = project_name
    if tags:
        parsed["tags"] = json.dumps([t.strip() for t in tags.split(",") if t.strip()])

    if existing and force:
        for key, value in parsed.items():
            if key != "session_id":
                setattr(existing, key, value)
        await db.flush()
        await sync_tool_steps(db, existing, traj_data)
        await db.commit()
        return UploadResponse(session_id=session_id, status="updated",
                              metadata=_summary(parsed), sha256=server_hash, oss_key=storage_key)

    record = Trajectory(**parsed)
    db.add(record)
    await db.flush()
    await sync_tool_steps(db, record, traj_data)
    await db.commit()
    return UploadResponse(session_id=session_id, status="created",
                          metadata=_summary(parsed), sha256=server_hash, oss_key=storage_key)


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
        # 解析得出才有。失败发生在那之前时日志里不带，不写空串。
        session_id = ""
        try:
            content = await file.read()
            traj_data = json.loads(content)
            parsed = parse_traj_content(content, tool_source)
            session_id = parsed["session_id"] or str(uuid.uuid4())
            parsed["session_id"] = session_id

            result = await db.execute(select(Trajectory).where(Trajectory.session_id == session_id))
            existing = result.scalar_one_or_none()

            if existing and not force:
                results.append({"session_id": session_id, "status": "skipped", "reason": "already exists"})
                continue

            storage_key = f"sessions/{session_id}/session.traj"
            storage.put(storage_key, content)

            server_hash = compute_sha256(content)
            parsed["traj_file_path"] = storage_key
            parsed["oss_key"] = storage_key if settings.is_oss else None
            parsed["sha256"] = server_hash
            parsed["file_size"] = len(content)

            if existing and force:
                for key, value in parsed.items():
                    if key != "session_id":
                        setattr(existing, key, value)
                await db.flush()
                await sync_tool_steps(db, existing, traj_data)
                results.append({"session_id": session_id, "status": "updated"})
            else:
                record = Trajectory(**parsed)
                db.add(record)
                await db.flush()
                await sync_tool_steps(db, record, traj_data)
                results.append({"session_id": session_id, "status": "created"})

        except Exception as exc:
            # reason 只回固定类别。str(exc) 里有解析器的原文，可能带轨迹内容。
            fields = {"event": "upload_failed", "exc_type": type(exc).__name__}
            if session_id:
                fields["session_id"] = session_id
            logger.error("upload failed", **fields)
            results.append({"file": file.filename, "status": "error", "reason": "parse_error"})

    await db.commit()
    return {"total": len(files), "results": results}


@router.post("/reindex", dependencies=[Depends(verify_upload_token)])
async def reindex(db: AsyncSession = Depends(get_db)):
    """扫描本地 sessions/ 和 traj_files/ 目录，将未入库的 .traj 文件解析并写入数据库

    注意：OSS 模式下不支持 reindex（数据在上传时已入库），仅本地模式可用。
    """
    if settings.is_oss:
        return {"message": "OSS 模式下不需要 reindex，数据在上传时已入库"}

    DATA_DIR = Path(settings.TRAJ_FILES_DIR)
    scanned = 0
    new = 0
    errors = 0
    tool_steps_rebuilt = 0

    # 新布局：sessions/*/session.traj
    for traj_file in SESSIONS_DIR.glob("*/session.traj"):
        scanned += 1
        try:
            content = traj_file.read_bytes()
            parsed = parse_traj_content(content)
            session_id = parsed["session_id"]
            if not session_id:
                continue

            result = await db.execute(select(Trajectory).where(Trajectory.session_id == session_id))
            existing = result.scalar_one_or_none()

            parsed["tool_source"] = parsed.get("tool_source", "claude-code")
            parsed["traj_file_path"] = str(traj_file.relative_to(SESSIONS_DIR.parent))

            if existing:
                await sync_tool_steps(db, existing, json.loads(content))
                tool_steps_rebuilt += 1
                continue

            record = Trajectory(**parsed)
            db.add(record)
            await db.flush()
            await sync_tool_steps(db, record, json.loads(content))
            new += 1
            tool_steps_rebuilt += 1
        except Exception as exc:
            errors += 1
            _log_reindex_failure(traj_file, exc)

    # 旧布局兼容：traj_files/**/*.traj
    if DATA_DIR.exists():
        for traj_file in DATA_DIR.rglob("*.traj"):
            scanned += 1
            try:
                content = traj_file.read_bytes()
                parsed = parse_traj_content(content)
                session_id = parsed["session_id"]
                if not session_id:
                    continue

                result = await db.execute(select(Trajectory).where(Trajectory.session_id == session_id))
                existing = result.scalar_one_or_none()

                tool_source = traj_file.parent.name
                if tool_source == "traj_files":
                    tool_source = "claude-code"
                parsed["tool_source"] = tool_source
                parsed["traj_file_path"] = str(traj_file.relative_to(DATA_DIR.parent))

                if existing:
                    await sync_tool_steps(db, existing, json.loads(content))
                    tool_steps_rebuilt += 1
                    continue

                record = Trajectory(**parsed)
                db.add(record)
                await db.flush()
                await sync_tool_steps(db, record, json.loads(content))
                new += 1
                tool_steps_rebuilt += 1
            except Exception as exc:
                errors += 1
                _log_reindex_failure(traj_file, exc)

    await db.commit()
    return {
        "scanned": scanned,
        "new": new,
        "skipped": scanned - new - errors,
        "errors": errors,
        "tool_steps_rebuilt": tool_steps_rebuilt,
    }


def _log_reindex_failure(traj_file: Path, exc: Exception) -> None:
    """单个文件解析失败。记路径与异常类名，不记文件内容。

    路径相对 SESSIONS_DIR 的父目录（即 data/）。绝对路径会把部署目录带进日志，
    而文件名本身（session.traj）不是密钥，可以记。
    """
    base = SESSIONS_DIR.parent
    try:
        rel = traj_file.relative_to(base)
    except ValueError:
        rel = traj_file.name
    logger.warning(
        "reindex failed",
        event="reindex_failed",
        path=str(rel),
        exc_type=type(exc).__name__,
    )


def _summary(parsed: dict) -> dict:
    """返回上传响应中的元数据摘要"""
    return {
        "model": parsed.get("model", ""),
        "total_steps": parsed.get("total_steps", 0),
        "total_cost_usd": parsed.get("total_cost_usd", 0.0),
        "exit_status": parsed.get("exit_status", ""),
        "first_prompt": parsed.get("first_prompt", "")[:100],
    }
