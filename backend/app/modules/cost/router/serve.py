"""GET /api/v1/ctl/budget —— 预算下发，挂 `require_device`（Bearer 设备凭据）。

失败语义（契约 §6）：本端点是 fail-open。通道是「施加约束」不是「授予信任」——
客户端拉不到（5xx / 超时 / 401 / 204）就当没配远程预算，本地 costLimit 仍硬停。

为什么 fail-open 但**必须鉴权**：无认证下发 `enforcement=block` = 同网段中间人
关全公司 agent。不要把本路径加进 `CTL_AUTH_EXEMPTIONS`。门禁 ② 会扫到它，
仍单开 `test_budget_serve_requires_device`：豁免名单加错一行就是事故。

为什么不在 `/ctl/` 下写：浏览器没有设备凭据。写口在 `/api/v1/budgets/**`
（cookie 会话）。给下发端点加 POST 等于把「谁有设备凭据谁能改全公司预算」做成功能。

Cache-Control 用 `private, no-cache`：预算按设备不同，不能像 flag 那样
`public, max-age=300`。ETag 纳入 used_usd，用量变了下次 GET 不再 304。
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from app.core.auth.control_plane import DeviceContext, require_device
from app.core.db import get_db
from app.modules.cost.service.budgets import delivery_body, etag_of, evaluate, sum_used_usd
from app.modules.cost.service.guard import current_period_key

router = APIRouter(prefix="/ctl", tags=["control-plane"])


@router.get("/budget")
async def get_budget(
    request: Request,
    ctx: DeviceContext = Depends(require_device),
    db: AsyncSession = Depends(get_db),
):
    """命中一份 → 200；三层都没有 → 204；ETag 命中 → 304。

    204 带 `X-Budget-Generation: "none"`（与 200 的 ETag 不同）：负缓存友好。
    返回类型故意是 dict / Response 而不是 Pydantic 模型：response_model 会把
    未声明字段滤掉（flag 下发踩过这个坑）。
    """
    budget = await evaluate(db, ctx)
    if budget is None:
        return Response(
            status_code=204,
            headers={
                "Cache-Control": "private, no-cache",
                "X-Budget-Generation": '"none"',
            },
        )

    period_key = current_period_key(budget.period)
    used = await sum_used_usd(
        db,
        scope_type=budget.scope_type,
        scope_id=budget.scope_id,
        org_id=budget.org_id,
        period=budget.period,
        period_key=period_key,
    )
    body = delivery_body(budget, used, period_key)
    etag = etag_of(body)
    headers = {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "X-Budget-Generation": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=body, headers=headers)
