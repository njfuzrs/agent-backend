"""导出 API。"""

import gzip
import io
import json
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Trajectory
from app.schemas import TrajectoryExportRequest
from app.services.storage import storage
from app.utils.auth import verify_basic_auth

router = APIRouter(prefix="/export", tags=["export"])


@router.post("/trajectories", dependencies=[Depends(verify_basic_auth)])
async def export_trajectories(
    request: TrajectoryExportRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Trajectory)
        .where(Trajectory.session_id.in_(request.session_ids))
        .where(Trajectory.deleted_at.is_(None))
    )
    trajectories = list(result.scalars().all())

    if not trajectories:
        raise HTTPException(status_code=404, detail="no trajectories found")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = []
        for trajectory in trajectories:
            content = _read_traj_content(trajectory)
            filename = f"{trajectory.session_id}.traj"
            archive.writestr(filename, content)
            manifest.append(
                {
                    "session_id": trajectory.session_id,
                    "tool_source": trajectory.tool_source,
                    "model": trajectory.model,
                    "start_time": trajectory.start_time,
                    "total_steps": trajectory.total_steps,
                    "total_tokens": trajectory.total_tokens,
                    "total_cost_usd": trajectory.total_cost_usd,
                    "quality_status": trajectory.quality_status,
                }
            )

        archive.writestr(
            "manifest.json",
            json.dumps({"total": len(manifest), "items": manifest}, ensure_ascii=False, indent=2),
        )

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="trajectories-export.zip"'},
    )


def _read_traj_content(traj: Trajectory) -> bytes:
    key = traj.oss_key or traj.traj_file_path
    try:
        content = storage.get(key)
        if key.endswith(".gz"):
            content = gzip.decompress(content)
        return content
    except FileNotFoundError:
        pass

    fallback_key = f"sessions/{traj.session_id}/session.traj"
    try:
        return storage.get(fallback_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"traj file not found: {traj.session_id}")
