"""sidecar 入口。独立进程，独立 FastAPI 实例。

启动：
    uvicorn app.modules.bridge.sidecar.main:app --host 127.0.0.1 --port 8901 --workers 1

**`--workers` 必须是 1。** 配对表是进程内存。两个 worker 等于两条互不相通的
中继：CLI 连上一个、控制端连上另一个，两边都在等，谁也不报错。不要在 unit
文件里改成 2，也不要把本文件的路由 include 进 app.main——那会让同一份代码
跑在 `--workers 2` 的主进程里，是同一种静默失败。

本进程只做三件事：Upgrade、首帧鉴权、双向转发。签发与列表在主进程。
两边只共享数据库。主进程挂了，这里已经配上的对继续转发，直到探活超时；
这里挂了，主进程的列表还在，只是新的连接连不上。
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse

from app.core.logging import configure_logging, get_logger

# 与主应用同一条纪律：业务模块 import 之前先配日志，否则 logger 沿用配置前的 handler。
configure_logging()
logger = get_logger("agent.bridge")

# 模块级导入：测试要能换掉这份中继内存。放进函数里就换不了，用例之间会串。
from app.modules.bridge.sidecar.relay import relay  # noqa: E402

# 轮询「管理台点了断开」的间隔，与转发循环里的等待同一值。
_POLL_SECONDS = 2


async def _watch_disconnects() -> None:
    """进程级的断开轮询。连接自己的循环只看见自己，这里看见全部。"""
    while True:
        await asyncio.sleep(_POLL_SECONDS)
        try:
            await relay.poll_disconnects()
        except Exception:
            logger.warning(
                "bridge disconnect poll crashed",
                event="bridge_state_read_failed",
                outcome="error",
            )


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 — FastAPI 要求这个签名
    task = asyncio.create_task(_watch_disconnects())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Agent Backend Bridge", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    """只给本机探活。不经 nginx，不挂鉴权，不返回任何 session 信息。"""
    return JSONResponse({"status": "ok"})


@app.websocket("/api/v1/bridge/ws")
async def bridge_ws(ws: WebSocket):
    """Upgrade。鉴权不在握手头里，在第一条文本帧里。

    URL 上出现 `token` 查询参数时直接关闭，即使值是对的。凭证进了 URL 就会
    进 nginx access log，这正是本里程碑要修掉的事。
    """
    await relay.handle(ws)
