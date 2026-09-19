"""FastAPI 入口。只做装配，不含业务逻辑。

⚠️ 冻结区（规划 §1.2）：以下 URL 是 sid-code 线上在用的，改了就断真实数据采集 ——
    POST /api/v1/upload/session-file   （trace/uploader.ts:272）
    GET  /api/v1/health                （trace/uploader.ts:169 心跳，60s）
由 tests/test_boundaries.py 的快照测试锁定。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine
from app.core.router import auth
from app.modules.trajectory.router import (
    compare,
    export,
    scoring,
    stats,
    trajectories,
    upload,
)
from app.modules.trajectory.schemas import HealthResponse

# 用 uvicorn.error 这个 logger：uvicorn 只给自己的 logger 挂 handler，
# 挂在 __name__ 上的 INFO 会被丢掉（实测启动时看不到 schema 版本行）。
logger = logging.getLogger("uvicorn.error")


async def _check_schema_version() -> None:
    """启动时只做「检查」，不做「建表/加列」。

    schema 演进归 Alembic（`alembic upgrade head`）。这里故意不自动执行迁移：
    自动迁移在多 worker 下会并发抢锁（生产是 --workers 2），且让「部署」和「改库」
    这两件事失去独立的失败点。所以这里只警告，把决定权留给部署流程。
    """
    try:
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = row.scalar()
    except Exception:
        logger.warning(
            "未找到 alembic_version 表 —— 该库未被迁移管理。"
            "新库请执行 `alembic upgrade head`；已有数据的库请执行 `alembic stamp 0001`。"
        )
        return
    logger.info("数据库 schema 版本: %s", current)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _check_schema_version()
    yield


app = FastAPI(
    title="企业级 Agent 后端 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- 平台内核：管理台会话（凭据不进 localStorage，见 §PR-0.5）----
app.include_router(auth.router, prefix="/api/v1")

# ---- 数据面：事实从客户端流出 ----
app.include_router(upload.router, prefix="/api/v1")
app.include_router(trajectories.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(compare.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")
app.include_router(scoring.router, prefix="/api/v1")

# ---- 控制面：策略向客户端流入 ----
# M1 起在此挂载 /api/v1/ctl/** 路由，鉴权一律 Depends(require_device)，
# 绝不接受 verify_upload_token / verify_basic_auth（规划 §2.1，由边界测试机械化）。


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    """冻结区：sid-code 心跳端点，60s 一次，用于更新 serverReachable。"""
    return HealthResponse(status="ok", version="1.0.0")
