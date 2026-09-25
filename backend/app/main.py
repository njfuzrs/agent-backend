"""FastAPI 入口。只做装配，不含业务逻辑。

⚠️ 冻结区（规划 §1.2）：以下 URL 是 sid-code 线上在用的，改了就断真实数据采集 ——
    POST /api/v1/upload/session-file   （trace/uploader.ts:272）
    GET  /api/v1/health                （trace/uploader.ts:169 心跳，60s）
由 tests/test_boundaries.py 的快照测试锁定。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core import db as db_mod
from app.core.config import settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.ready import router as ready_router

# 必须在业务路由 import 之前配置。那些模块的 logger 若在配置前被创建，
# 会沿用上次进程或测试留下的 handler，表现为「本地偶现没日志」（方案 §5）。
configure_logging()
logger = get_logger("agent")

from app.core.middleware import RequestContextMiddleware
from app.core.router import auth
from app.modules.bridge.router import admin as bridge_admin
from app.modules.bridge.router import serve as bridge_serve
from app.modules.cost.router import admin as cost_admin
from app.modules.cost.router import ingest as cost_ingest
from app.modules.cost.router import serve as cost_serve
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
        # 启动日志没有请求，不带 request_id（方案 §3.10）。处置方式写在本函数的
        # docstring 里，不进日志：msg 只留固定短句，检索靠 event 与 outcome。
        logger.warning("schema check failed", event="schema_checked", outcome="error")
        return
    # 版本号是这次检查唯一的事实。空表读到 None 时省略，不写空串。
    fields = {"event": "schema_checked", "outcome": "ok"}
    if current:
        fields["schema_version"] = str(current)
    logger.info("schema checked", **fields)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 — FastAPI 要求这个签名
    await _check_schema_version()
    yield


app = FastAPI(
    title="企业级 Agent 后端 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 在最外层：请求先过它再生成编号，这样 4xx 的预检也带得上 X-Request-ID。
# expose_headers 不带的话浏览器里的采集端读不到这个响应头，两侧对不上号。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
# 后加的中间件在外层。访问日志要包住全部路由，所以加在 CORS 之前（运行时在其内）。
app.add_middleware(RequestContextMiddleware)

# 兜底在中间件之内：处理器运行时 request_id 已经在上下文里，响应才能把它带回去。
install_exception_handlers(app)

# ---- 平台内核：管理台会话（凭据不进 localStorage，见 §PR-0.5）----
app.include_router(auth.router, prefix="/api/v1")
# 就绪检查。不进冻结区、不挂鉴权、不探 OSS（方案 §3.8）。
app.include_router(ready_router, prefix="/api/v1")

# ---- 数据面：事实从客户端流出 ----
app.include_router(upload.router, prefix="/api/v1")
app.include_router(trajectories.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")
# events 上报：数据面方向、控制面鉴权（require_device）。路径故意不在 /ctl/ 下，
# 现有门禁 ② 扫不到它 —— 见 test_events_ingest_requires_device。
app.include_router(event_ingest.router, prefix="/api/v1")
# usage/ledger 上报：同款「数据面方向、控制面鉴权」。upsert 能覆盖，漏挂鉴权
# 比 events 灌水更严重。见 test_usage_ledger_ingest_requires_device。
app.include_router(cost_ingest.router, prefix="/api/v1")

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
app.include_router(cost_serve.router, prefix="/api/v1")
# bridge 的配对 REST。WebSocket 不在这里：它在独立 sidecar 进程里，
# 主进程 --workers 2 装不下连接配对。见 modules/bridge/sidecar/main.py。
app.include_router(bridge_serve.router, prefix="/api/v1")

# ---- 管理台：身份 / flag / policy / event / cost（cookie 会话，给人看，不给客户端下发策略）----
app.include_router(identity_admin.router, prefix="/api/v1")
app.include_router(flag_admin.router, prefix="/api/v1")
app.include_router(policy_admin.router, prefix="/api/v1")
app.include_router(event_admin.router, prefix="/api/v1")
app.include_router(cost_admin.ledger_router, prefix="/api/v1")
app.include_router(cost_admin.budget_router, prefix="/api/v1")
app.include_router(bridge_admin.router, prefix="/api/v1")


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    """冻结区：sid-code 心跳端点，60s 一次，用于更新 serverReachable。"""
    return HealthResponse(status="ok", version="1.0.0")
