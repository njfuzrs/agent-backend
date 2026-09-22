"""GET /api/v1/ctl/flags —— flag 下发，**无认证**（客户端契约，不能改）。

为什么这条 /ctl/ 端点不挂 require_device：客户端 `feature-flags.ts::refreshFromRemote`
发的是裸 `fetch(endpoint, { headers: { Accept: "application/json" } })`，**没有
Authorization 头**。挂上 require_device 会让所有客户端拿 401，而客户端 `catch {}`
静默吞掉 —— 表现是「flag 功能全在、单测全过、真实会话零生效」，正是规划 §0.3
第二条纪律说的那种失败。

所以这是边界测试 ② 的第二个显式豁免（enroll 之后），代价用两道锁补回来：
  1. **只读。** 本路由只有 GET，写全在管理台侧（cookie 会话）。
  2. **只能施加约束。** guard.py 在写入时拦掉放宽安全类的 key/description，
     所以无认证通道里流出去的东西不可能放宽任何限制。

响应头 `Cache-Control: max-age=300` 对齐 PR-2.1 用 nginx 静态文件验链路时的口径 ——
客户端默认 6 小时刷一次，中间加 5 分钟缓存不影响生效速度，但能挡住
「大量客户端同时冷启动」把查询打到库上。
"""

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.modules.flag.service.flags import serve_flags

router = APIRouter(prefix="/ctl", tags=["control-plane"])

# 与 PR-2.1 的 nginx `Cache-Control: max-age=300` 一致
FLAGS_CACHE_SECONDS = 300


@router.get("/flags")
async def get_flags(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """扁平 JSON `{key: value}`。全量替换语义，不是增量。

    返回类型故意是 dict 而不是 Pydantic 模型：key 在运行时才知道，
    response_model 会把未声明的字段过滤掉（实测会返回空对象）。
    """
    response.headers["Cache-Control"] = f"public, max-age={FLAGS_CACHE_SECONDS}"
    return await serve_flags(db)
