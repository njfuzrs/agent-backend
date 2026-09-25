"""POST /api/v1/events —— analytics 事件上报，挂 `require_device`。

路径**故意不在 `/ctl/` 下**（契约 §1）：`/ctl/` 是策略向客户端流入，事件是事实从
客户端流出。但鉴权用控制面的 `require_device` —— 这是本仓第一个「数据流向是数据面、
鉴权用控制面」的端点。理由：事件上报写入他人可见的审计记录，接受匿名写入等于让
任何人往审计里灌数据。

现有门禁 ②（`test_all_control_endpoints_require_device`）按路径含 `/ctl/` 筛，
**扫不到本端点**。漏挂鉴权不会有任何东西红。所以 tests/test_boundaries.py 有
`test_events_ingest_requires_device` 专门盯这里。删掉那条等于把本端点变成无认证。

失败语义（契约 §6）：
    鉴权 fail-closed —— 无头 / 坏头 / 吊销 / 过期 → 401。禁止 X-Upload-Token 当鉴权。
    写入 fail-open —— 单条坏事件只计 rejected，不退整批。
    白名单 fail-closed —— 未知事件名不入库。

`org_id` / `team_id` / `device_id` 一律从 `DeviceContext` 取，忽略 body 里的任何
同名字段。body 解析不用 Pydantic 模型：缺 `events` 键要 400 不是 422（契约 §1），
一条坏事件也绝不能变成整批 422。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.db import get_db
from app.core.logging import get_logger
from app.modules.event.schemas import EventIngestResponse
from app.modules.event.service.guard import MAX_BODY_BYTES, MAX_EVENTS_PER_REQUEST
from app.modules.event.service.ingest import ingest_events

logger = get_logger("agent.event")

router = APIRouter(tags=["events-ingest"])


@router.post("/events", response_model=EventIngestResponse, status_code=202)
async def post_events(
    request: Request,
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    """写入即 202。空数组也是 202 全 0，不是错误。

    鉴权依赖写在函数签名上，**必须先于 body 解析执行**（FastAPI 先解析 Depends）。
    所以无凭据时即使 body 非法也是 401，不会先 400 —— 冒烟用空数组就是为了走到鉴权。
    """
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        logger.warning(
            "events rejected",
            event="events_rejected",
            reason="too_large",
            device_id=ctx.device_id,
            bytes_in=len(raw),
        )
        raise HTTPException(status_code=413, detail="payload too large")

    try:
        body = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="body must be JSON") from None
    if not isinstance(body, dict) or "events" not in body:
        raise HTTPException(status_code=400, detail="missing events")
    events = body["events"]
    if not isinstance(events, list):
        raise HTTPException(status_code=400, detail="events must be an array")

    n = len(events)
    if n > MAX_EVENTS_PER_REQUEST:
        logger.warning(
            "events rejected",
            event="events_rejected",
            reason="too_many",
            device_id=ctx.device_id,
            count=n,
        )
        raise HTTPException(
            status_code=413,
            detail=f"too many events: {n} > {MAX_EVENTS_PER_REQUEST}",
        )

    return await ingest_events(
        db,
        events,
        device_id=ctx.device_id,
        org_id=ctx.org_id,
        team_id=ctx.team_id or "",
    )
