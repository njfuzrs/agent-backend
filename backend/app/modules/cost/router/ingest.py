"""POST /api/v1/usage/ledger —— 账本上报，挂 `require_device`。

路径**故意不在 `/ctl/` 下**（契约 §1）：`/ctl/` 是策略向客户端流入，账本是事实从
客户端流出。但鉴权用控制面的 `require_device` —— 与 M4 `POST /events` 同款
「数据流向是数据面、鉴权用控制面」。理由：upsert 能覆盖，接受匿名写入等于让
任何人把这台设备这个会话的成本改成 0。比 events 灌水更严重。

现有门禁 ②（`test_all_control_endpoints_require_device`）按路径含 `/ctl/` 筛，
**扫不到本端点**。漏挂鉴权不会有任何东西红。所以 tests/test_boundaries.py 有
`test_usage_ledger_ingest_requires_device` 专门盯这里。删掉那条等于把本端点
变成无认证覆盖账本。

失败语义（契约 §6）：
    鉴权 fail-closed —— 无头 / 坏头 / 吊销 / 过期 → 401。禁止 X-Upload-Token 当鉴权。
    写入 fail-open —— 一行 upsert 失败 5xx，让客户端重试。

`org_id` / `team_id` / `device_id` 一律从 `DeviceContext` 取，忽略 body 里的任何
同名字段。body 解析不用 Pydantic 模型：缺 `sessionId` 要 400 不是 422。
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.db import get_db
from app.core.logging import get_logger
from app.modules.cost.schemas import UsageIngestResponse
from app.modules.cost.service.guard import MAX_BODY_BYTES, BadLedger, parse_ledger_body
from app.modules.cost.service.ingest import upsert_ledger

logger = get_logger("agent.cost")

router = APIRouter(tags=["usage-ingest"])


@router.post("/usage/ledger", response_model=UsageIngestResponse, status_code=200)
async def post_usage_ledger(
    request: Request,
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    """写入即 200。首次 inserted，同键覆盖 updated。

    鉴权依赖写在函数签名上，**必须先于 body 解析执行**（FastAPI 先解析 Depends）。
    所以无凭据时即使 body 非法也是 401，不会先 400 —— 冒烟用齐字段无 Bearer 就是
    为了走到鉴权。
    """
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        logger.warning(
            "ledger rejected",
            event="ledger_rejected",
            reason="too_large",
            device_id=ctx.device_id,
            bytes_in=len(raw),
        )
        raise HTTPException(status_code=413, detail="payload too large")

    try:
        body = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    try:
        parsed = parse_ledger_body(body)
    except BadLedger as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from None

    return await upsert_ledger(
        db,
        parsed,
        device_id=ctx.device_id,
        org_id=ctx.org_id,
        team_id=ctx.team_id or "",
    )
