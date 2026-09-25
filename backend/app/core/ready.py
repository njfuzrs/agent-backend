"""就绪检查。与 health 分开：health 证明进程活着，ready 证明数据库能用。

health 是冻结区，sid-code 心跳与 rollback.sh 都依赖它便宜且恒为 200，
所以探活不能塞进去。本端点不挂鉴权：它只返回一个状态词，且要能被本机 curl 直接用。
不探 OSS——对象存储抖动是上传路径的问题，不该变成整体不可用（方案 §3.8）。
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core import db as db_mod
from app.core.logging import get_logger

logger = get_logger("agent")

# 超时单独一个名字，测试要能把它换成一个立刻失败的值，而不是真等一秒。
DB_TIMEOUT_SECONDS = 1.0

router = APIRouter(tags=["ready"])


@router.get("/ready")
async def ready():
    """SELECT 1，超时 1 秒。失败 503，body 只给一个状态词。

    成功不打日志：本机每 60 秒打一次的话，和 health 是同一个刷屏问题。
    """
    try:
        await asyncio.wait_for(_select_one(), timeout=DB_TIMEOUT_SECONDS)
    except Exception as exc:
        # 只记异常类名。数据库异常的消息里有语句和绑定参数，不能进日志（方案 §4.5）。
        logger.error(
            "readiness check failed",
            event="ready_failed",
            outcome="db_unavailable",
            exc_type=type(exc).__name__,
        )
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return {"status": "ok"}


async def _select_one() -> None:
    async with db_mod.engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
