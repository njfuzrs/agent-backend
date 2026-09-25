"""请求上下文与访问日志。

一条请求结束时打一条 ``agent.access``，字段用方案 §3.2 的三层。
uvicorn 的 access log 关掉（unit 带 ``--no-access-log``），否则同一条请求
在 journald 里有两条对不上的记录。

``device_id`` / ``org_id`` / ``actor`` 不在这里填：本中间件执行时鉴权依赖
还没跑，没有 ``DeviceContext``。它们由鉴权依赖在通过后写进同一个 contextvar，
本中间件在请求结束时读（方案 §3.4，已裁决）。
"""

import re
import secrets
import time

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import (
    bind_context,
    clear_context,
    context_value,
    db_error_fields,
    get_logger,
)

logger = get_logger("agent.access")
# 未捕获异常的兜底记在 agent 上，不记在访问日志上（方案 §3.7）。
error_logger = get_logger("agent")

# 客户端传来的编号只在符合这个形状时采用。不设限制的话，一个换行就能把
# 一条日志拆成两条（方案 §3.3）。
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# 永远成功、只有心跳意义的路由。只在 2xx 时跳过，非 2xx 照记——
# 跳过规则把失败也吞掉，就退回「出事只能靠客户端状态码倒推」。
# 用路由模板而不是真实路径：真实路径的基数等于 session 数，无法枚举。
# ready 的失败另有一条 error（event=ready_failed），访问日志照记，不靠这里。
QUIET_ROUTES = ("/api/v1/health", "/api/v1/ready")

# 轮询接口。量大，但失败恰恰要看见，所以不丢弃，用级别解决（方案 §3.4）：
# 快速 2xx 降到 debug（默认 info 下不输出），慢或失败照记。
# 不设配置项。运行时能把一个接口静音，就一定会有人静音掉不该静的那个。
POLLED_ROUTES = ("/api/v1/ctl/flags", "/api/v1/ctl/policy", "/api/v1/ctl/budget")

# 慢的阈值。超过它，即便是轮询的 2xx 也升到 info。
SLOW_REQUEST_MS = 1000

# 耗时用的时钟。单独一个名字是为了让测试能替换它——替换 time.monotonic
# 会冻结整个进程（sqlite、httpx 超时都靠它），测试会卡死。
_now = time.monotonic

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def generate_request_id() -> str:
    """32 个十六进制字符，与 W3C traceparent 的 trace-id 同长。

    将来接 OpenTelemetry 是「把同一个值同时写进 traceparent」，
    不是推翻重来（方案 §3.3）。不为此引入新依赖。
    """
    return secrets.token_hex(16)


def adopt_request_id(header_value: str) -> str:
    """采用客户端传来的编号；形状不对（含换行、太短、太长）就自己生成。"""
    candidate = header_value.strip()
    if _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return generate_request_id()


def client_ip(scope: Scope) -> str:
    """取调用方地址。

    ``X-Forwarded-For`` 只在直连对端是 loopback 时信任——前面只有本机 nginx。
    不做成可配置的信任列表：一旦可配置，就会有人配成信任所有人，伪造来源。
    真出现第二层反代时改这里，那是一次有意的变更（方案 §3.4）。
    """
    client = scope.get("client")
    peer = client[0] if client else ""
    if peer in _LOOPBACK:
        for key, value in scope.get("headers") or ():
            if key == b"x-forwarded-for":
                first = value.decode("latin-1", errors="replace").split(",")[0].strip()
                if first:
                    return first
                break
    return peer or "-"


def route_template(request: Request) -> str:
    """路由模板，基数等于接口数，才能回答「哪个接口变慢了」。

    真实路径的基数等于 session 数、设备数，无法聚合。匹配不上（404）记 ``-``，
    而不是把真实路径填进来——填进来就退回无法聚合的状态。

    必须在请求**结束**时取：进入时路由还没匹配，``scope["endpoint"]`` 是空的。

    不能只扫 ``app.routes`` 的顶层。FastAPI 0.137 起 ``include_router`` 不再把
    子路由克隆成扁平列表，顶层是一棵树（release notes 0.137.0 写明 ``router.routes``
    不再是扁平的 APIRoute 列表）。被 include 进来的路由，顶层节点既没有 ``path``
    也没有 ``endpoint``，真实 URL 在 ``effective_candidates()`` 展开的节点上，
    前缀也已经拼进去了。0.137 之前没有这个方法，顶层本身就是带完整前缀的扁平
    APIRoute，走下面的普通分支。

    两种形状都要认。只认扁平那一种的话，FastAPI 一过 0.137，所有请求的 route
    都变成 ``-``：轮询降级匹配不上，按接口聚合也退回到无法聚合的真实路径。
    CI 装到 0.141、本地还是 0.135 时，两条测试一起红，就是这个原因。

    也不读 ``scope["route"]``。0.141 里它指向被 include 之前的原始路由，``path``
    只剩子路由自己的那一段（``/ctl/flags``），前缀丢了；0.135 里却是拼好前缀的。
    同一个字段两个版本含义不同。
    """
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return "-"
    for node in request.app.routes:
        found = _route_path_for_endpoint(node, endpoint)
        if found is not None:
            return found
    return "-"


def _route_path_for_endpoint(node: object, endpoint: object) -> str | None:
    """在路由树里按处理函数的身份找模板。找不到返回 None。

    与 tests/test_boundaries.py 的 ``_iter_route_node`` 是同一种展开：那边要
    全部路径来做门禁，这里只取命中的一条。展开方式得保持一致，否则门禁看到的
    路径和日志记下的会再次分叉。
    """
    effective = getattr(node, "effective_candidates", None)
    if callable(effective):
        for child in effective():
            found = _route_path_for_endpoint(child, endpoint)
            if found is not None:
                return found
        return None
    if getattr(node, "endpoint", None) is endpoint:
        return getattr(node, "path", None) or None
    return None


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1", errors="replace")
    return ""


class RequestContextMiddleware:
    """纯 ASGI 中间件。

    不用 ``BaseHTTPMiddleware``：它把请求包进独立的任务里跑，结束之后
    ``scope["endpoint"]`` 还在，但异常路径上的状态码要自己兜（进入时先记 500，
    正常发出响应后再改掉）。访问日志的价值在 ``route`` 字段上（方案 §3.4）。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = adopt_request_id(_header(scope, b"x-request-id"))
        method = scope.get("method", "")
        # path 不含 query。列表接口的 query 里有检索词，检索词可能是代码或内部名称。
        path = scope.get("path", "")
        bind_context(
            request_id=request_id,
            method=method,
            path=path,
            client_ip=client_ip(scope),
        )
        scope.setdefault("state", {})["request_id"] = request_id

        status_code = 500
        started = _now()

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            # 在清上下文之前记。FastAPI 把 Exception 处理器放在用户中间件的
            # 外面（ServerErrorMiddleware），等它运行时 request_id 已经被清掉，
            # 响应里就没有编号了。HTTPException 与校验错误被内层中间件处理成
            # 正常响应，到不了这里。
            self._log_unhandled(scope, exc)
            response = JSONResponse(
                status_code=500,
                content={"detail": "internal error", "request_id": request_id},
            )
            await response(scope, receive, send_with_id)
        finally:
            duration_ms = round((_now() - started) * 1000, 1)
            try:
                self._access_log(scope, status_code, duration_ms)
            finally:
                clear_context()

    def _access_log(self, scope: Scope, status_code: int, duration_ms: float) -> None:
        request = Request(scope)
        route = route_template(request)
        if route in QUIET_ROUTES and status_code < 400:
            return

        fields = {
            "event": "request_completed",
            "route": route,
            "status": status_code,
            "duration_ms": duration_ms,
        }
        # 鉴权依赖写进上下文的字段，读得到才带。读不到就省略，不写空串。
        for key in ("auth", "device_id", "org_id", "actor"):
            value = context_value(key)
            if value:
                fields[key] = value

        level = _access_level(route, status_code, duration_ms)
        log = getattr(logger, level)
        log("request completed", **fields)

    def _log_unhandled(self, scope: Scope, exc: Exception) -> None:
        """未捕获异常记一条 error，带栈。数据库异常先裁剪（方案 §4.5）。"""
        fields, logged = db_error_fields(exc)
        fields.update(
            event="unhandled_exception",
            route=route_template(Request(scope)),
        )
        error_logger.exception("unhandled exception", logged, **fields)


def _access_level(route: str, status_code: int, duration_ms: float) -> str:
    """按结果定级别，不按接口重要性。``journalctl -p warning`` 因此能看到全部失败。"""
    if status_code >= 500:
        return "error"
    if status_code >= 400:
        return "warning"
    if route in POLLED_ROUTES and status_code < 300 and duration_ms < SLOW_REQUEST_MS:
        return "debug"
    return "info"
