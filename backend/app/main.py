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

from app.core import db as db_mod
from app.core.config import settings
from app.core.router import auth
from app.modules.event.router import admin as event_admin
from app.modules.event.router import ingest as event_ingest
from app.modules.flag.router import admin as flag_admin
from app.modules.flag.router import flags as flag_serve
from app.modules.identity.router import admin as identity_admin
from app.modules.identity.router import enroll as identity_enroll
from app.modules.identity.router import whoami as identity_whoami
from app.modules.policy.router import admin as policy_admin
from app.modules.policy.router import serve as policy_serve
from app.modules.trajectory.router import (
    export,
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
        async with db_mod.engine.connect() as conn:
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
async def lifespan(app: FastAPI):  # noqa: ARG001 — FastAPI 要求这个签名
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
app.include_router(export.router, prefix="/api/v1")
# events 上报：数据面方向、控制面鉴权（require_device）。路径故意不在 /ctl/ 下，
# 现有门禁 ② 扫不到它 —— 见 test_events_ingest_requires_device。
app.include_router(event_ingest.router, prefix="/api/v1")

# ---- 控制面：策略向客户端流入 ----
# /ctl/** 鉴权一律 Depends(require_device)，两个显式例外：
#   /ctl/enroll     签发入口（一次性注册码，鸡生蛋）
#   GET /ctl/flags  客户端 feature-flags.ts 发裸 fetch，没有 Authorization 头；
#                   挂鉴权会让它 catch {} 静默吞 401，表现为「功能全在、零生效」。
#                   代价用「只读」+「写入侧门禁禁放宽类 flag」补回来（见 flag/service/guard.py）。
# 两者都由边界测试列为豁免，并断言它们不走数据面凭据。
app.include_router(identity_enroll.router, prefix="/api/v1")
app.include_router(identity_whoami.router, prefix="/api/v1")
app.include_router(flag_serve.router, prefix="/api/v1")
app.include_router(policy_serve.router, prefix="/api/v1")

# ---- 管理台：身份 / flag / policy / event（cookie 会话，给人看，不给客户端下发策略）----
app.include_router(identity_admin.router, prefix="/api/v1")
app.include_router(flag_admin.router, prefix="/api/v1")
app.include_router(policy_admin.router, prefix="/api/v1")
app.include_router(event_admin.router, prefix="/api/v1")


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    """冻结区：sid-code 心跳端点，60s 一次，用于更新 serverReachable。"""
    return HealthResponse(status="ok", version="1.0.0")
