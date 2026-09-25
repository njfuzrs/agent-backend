"""请求校验错误的出口（方案 §3.7）。

422 不再回显提交的字段值。Starlette 默认把 ``input`` 放进 ``detail``，
轨迹上传、事件 metadata 这种接口的字段值不能回显。

未捕获异常不在这里。FastAPI 把 ``Exception`` 处理器放在用户中间件的外面，
等它运行时请求上下文已经被清掉，``request_id`` 带不回来。那条兜底因此放在
``RequestContextMiddleware`` 里，在清上下文之前记。

``HTTPException`` 也不在这里。4xx 是预期失败，由访问日志表达；需要上下文的
在抛出点已经记了。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import context_value, get_logger
from app.core.middleware import route_template

logger = get_logger("agent")


def _request_id() -> str:
    """从上下文取。中间件先于路由运行，正常路径总有值；没有就给空串。

    空串只出现在中间件之外抛出的异常上（今天没有这种路径）。响应用空串而不是
    省略字段：调用方按 ``request_id`` 取值，缺字段和空串对它是两回事。
    """
    return context_value("request_id") or ""


def install_exception_handlers(app: FastAPI) -> None:
    """注册校验错误处理器。它在 ExceptionMiddleware 里，运行时上下文还在。"""

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # 只记哪些字段没过。loc 是字段路径（body.reason 这种），不含值。
        # input 是提交的值，不记。
        logger.warning(
            "request validation failed",
            event="request_rejected",
            reason="invalid_request",
            route=route_template(request),
            count=len(exc.errors()),
            # loc 是字段路径。input 是提交的值，不取。
            fields=_locs(exc),
        )
        return JSONResponse(
            status_code=422,
            content={"detail": "invalid request", "request_id": _request_id()},
        )


def _locs(exc: RequestValidationError) -> str:
    """没过校验的字段路径，点号连接。不含值。

    路径段只留 str 和 int：别的类型不是字段名，留着会把异常对象的字符串带进来。
    """
    locs = []
    for err in exc.errors():
        parts = [str(p) for p in err.get("loc", ()) if isinstance(p, (str, int))]
        if parts:
            locs.append(".".join(parts))
    return ",".join(locs)
