"""日志内核：一处配置、一个取 logger 的入口、一份字段白名单。

为什么业务代码不能直接用标准库 logger（方案 §3.1）：

- uvicorn 只给自己的 logger 挂 handler。写成 ``getLogger(__name__)`` 的 INFO
  在线上是静默的，这正是本模块要消灭的状态。
- 白名单只有在所有业务日志都经过这里时才有强制力。谁绕过它，
  ``password`` 这种字段就能进 journald。

本模块只做四件事：配置、取 logger、上下文注入、字段白名单。
不做轮转、不做采样、不做告警——那些都有明确的触发条件（方案 §7），现在做是负担。
"""

import logging
import os
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings

# ---- logger 名 -----------------------------------------------------------------
# 只接受稳定前缀。``app.modules.xxx`` 这种随文件移动的名字一旦出现，
# 过滤条件会在第一次改目录时全部失效。
LOGGER_ROOT = "agent"

# ---- 字段 -----------------------------------------------------------------------
# 框架填，每条都有（request_id 除外：没有请求上下文时省略）。
# 业务侧传了也拒绝：调用点伪造 request_id 会让「按编号找一次失败」失效。
FRAMEWORK_FIELDS = frozenset(
    {"ts", "level", "logger", "pid", "version", "request_id", "exc"}
)

# 业务侧可用。想加字段先改这里与方案文档 §3.2，不接受「这个模块特殊」。
# method / path / route / client_ip / device_id / org_id 正常由中间件注入，
# 业务侧只在中间件还没运行的地方传（例如启动日志）。
BUSINESS_FIELDS = frozenset(
    {
        "method",
        "path",
        "route",
        "status",
        "duration_ms",
        "bytes_in",
        "bytes_out",
        "client_ip",
        "device_id",
        "org_id",
        "session_id",
        "auth",
        "actor",
        "action",
        "reason",
        "event",
        "outcome",
        "count",
        "file_type",
        "exc_type",
        "sqlstate",
        # 422 时哪些字段没过校验。只含字段路径（body.reason 这种），不含提交的值。
        "fields",
        "target_id",
        # 启动时 schema 检查读到的 alembic 版本。只有这一处用，单独开字段是因为
        # 把它拼进 msg 就违反「msg 不承担检索」，而现有字段没有一个能装版本号。
        "schema_version",
    }
)

# 故意拒绝的名字。精确匹配，不做子串匹配：子串会误伤 target_id，
# 也会给「换个拼写就绕过」的错觉。防的是人手滑把 password 当字段名。
DENIED_FIELDS = frozenset(
    {
        "token",
        "authorization",
        "cookie",
        "password",
        "secret",
        "body",
        "query",
        "sql",
        "metadata",
        "prompt",
        "content",
        "input",
    }
)

# msg 是给人读的固定短句，不承担检索（检索靠 event / reason）。
# 拼了变量就有无穷多种写法，告警匹配不到。
_MSG_PLACEHOLDER = re.compile(r"%(?:\([^)]+\))?[#0-9 .*+\-]*[sdif]")

# ---- 上下文 ---------------------------------------------------------------------
# 一个 ContextVar 放全部请求级字段。async 下按任务隔离，worker 之间天然隔离。
# 不会自动流进 asyncio.to_thread 或自建线程：谁开线程谁用 copy_context() 包一层
# （方案 §3.3），这里不提前做框架。
request_context: ContextVar[Optional[dict]] = ContextVar("agent_request_context", default=None)

# 中间件注入、业务侧不重复传的字段。鉴权通过后往同一个 dict 里写。
CONTEXT_FIELDS = ("request_id", "method", "path", "route", "client_ip", "device_id", "org_id", "actor", "auth")

# ---- 版本 -----------------------------------------------------------------------
# 进程启动时读一次，一次发布内不变。读不到是正常状态（本地没有 .version），
# 记 unknown 而不是空串：空串和「没读到」分不清。
_version: Optional[str] = None


def current_version() -> str:
    """本进程的代码版本。显式 AGENT_VERSION 优先，否则 unknown。"""
    global _version
    if _version is None:
        raw = os.environ.get("AGENT_VERSION", "").strip()
        _version = raw or "unknown"
    return _version


def reset_version_cache() -> None:
    """测试用：换了 AGENT_VERSION 之后清掉进程内缓存。"""
    global _version
    _version = None


# ---- 上下文读写 -----------------------------------------------------------------


def bind_context(**fields: Any) -> None:
    """把请求级字段写进当前任务的上下文。只收白名单里的字段，其余拒绝。

    合并而不是替换：鉴权依赖在中间件之后运行，它只能往已有上下文里追加
    device_id / actor，不能把 request_id 覆盖掉。
    """
    unknown = set(fields) - set(CONTEXT_FIELDS)
    if unknown:
        raise TypeError(f"日志上下文字段不在白名单内: {sorted(unknown)}")
    current = request_context.get()
    merged = dict(current) if current else {}
    for key, value in fields.items():
        if value is None or value == "":
            # 空值等于没这个字段。JSON 里 null 与缺字段对 jq 是两回事，统一成缺。
            merged.pop(key, None)
        else:
            merged[key] = value
    request_context.set(merged)


def clear_context() -> None:
    """请求结束时清掉。不在 finally 里清，下一个请求会继承上一个的 device_id。"""
    request_context.set(None)


def context_value(key: str) -> Any:
    """读一个上下文字段。没有上下文、或该字段没写过，都返回 None。"""
    current = request_context.get()
    if not current:
        return None
    return current.get(key)


# ---- 记录器 ---------------------------------------------------------------------


class AgentLogger:
    """白名单记录器。业务代码只拿得到它，拿不到标准库 Logger。

    多余字段直接 TypeError：静默丢弃会让人以为记上了，静默放行会让
    password 进日志。两种都比在开发期炸一下更贵。
    """

    def __init__(self, name: str):
        self.name = name
        self._logger = logging.getLogger(name)

    def debug(self, msg: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._emit(logging.INFO, msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._emit(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._emit(logging.ERROR, msg, fields)

    def exception(self, msg: str, exc: BaseException, **fields: Any) -> None:
        """记一条带栈的 error。

        不接受 exc_info 这种标准库参数：栈从显式传入的异常取，调用点
        看得到记的是哪一个。数据库异常先按 §4.5 裁剪再进来，本方法不裁剪。
        """
        self._emit(logging.ERROR, msg, fields, exc=exc)

    def _emit(
        self,
        level: int,
        msg: str,
        fields: dict,
        exc: Optional[BaseException] = None,
    ) -> None:
        if _MSG_PLACEHOLDER.search(msg):
            raise TypeError(
                f"日志 msg 禁止拼变量（{msg!r}）。检索信息放 event / reason 字段，msg 用固定短句"
            )
        denied = set(fields) & DENIED_FIELDS
        if denied:
            raise TypeError(f"日志字段被拒绝: {sorted(denied)}")
        framework = set(fields) & FRAMEWORK_FIELDS
        if framework:
            raise TypeError(
                f"框架字段由日志内核填写，调用点不能传: {sorted(framework)}"
            )
        unknown = set(fields) - BUSINESS_FIELDS
        if unknown:
            raise TypeError(f"日志字段不在白名单内: {sorted(unknown)}")
        # 字段在记录创建时就冻住。pytest 的 caplog 与我们自己的 handler 拿到的是
        # 同一条记录，而上下文过滤器只挂在自己的 handler 上，caplog 那边读不到。
        #
        # 级别在这里判，不调用 isEnabledFor。那个方法会把「全局禁用阈值」的结论按级别
        # 缓存，而 pytest 的 caplog 靠 logging.disable() 升降这个阈值（捕获窗口按
        # setup / call / teardown 三段开关）。缓存在阈值恢复之后仍是旧结论：整组跑时
        # 记录被静默丢掉，单独跑一条又正常。manager.disable 每次读取都是当前值。
        if self._logger.disabled or level < self._logger.getEffectiveLevel():
            return
        if self._logger.manager.disable >= level:
            return
        exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
        record = self._logger.makeRecord(
            self.name, level, "(agent)", 0, msg, (), exc_info
        )
        record.agent_fields = fields
        _freeze(record)
        self._logger.handle(record)


def db_error_fields(exc: BaseException) -> tuple[dict, BaseException]:
    """数据库异常进日志之前的裁剪（方案 §4.5）。

    ``DBAPIError`` 的字符串包含语句和绑定参数。它一旦进 ``exc`` 字段，
    字段白名单就全部失效——白名单管的是字段名，管不到栈的内容。

    返回 (字段, 记栈用的异常)。是数据库异常时：``sqlstate`` 取得到才带，
    栈换成一句固定文本；不是时原样返回，调用点不用自己判断。
    """
    from sqlalchemy.exc import DBAPIError

    fields: dict[str, Any] = {"exc_type": type(exc).__name__}
    if isinstance(exc, DBAPIError):
        orig = getattr(exc, "orig", None)
        sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        if sqlstate:
            fields["sqlstate"] = str(sqlstate)
        # 类名写进文本：exc 字段里的栈不再指向原始异常，只靠字段会在
        # 文本格式里看丢类型。语句和参数仍然不在。
        return fields, RuntimeError(f"db error, statement omitted ({type(exc).__name__})")
    return fields, exc


def get_logger(name: str) -> AgentLogger:
    """取一个业务记录器。只接受 ``agent`` 或以 ``agent.`` 开头的名字。"""
    if name != LOGGER_ROOT and not name.startswith(LOGGER_ROOT + "."):
        raise ValueError(
            f"logger 名必须是 {LOGGER_ROOT!r} 或以 {LOGGER_ROOT + '.'!r} 开头，收到 {name!r}"
        )
    return AgentLogger(name)


# ---- 格式 -----------------------------------------------------------------------


def _freeze(record: logging.LogRecord) -> None:
    """把框架字段与请求上下文写进记录。必须在请求结束、上下文清除之前调用。"""
    record.agent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record.agent_version = current_version()
    record.agent_pid = os.getpid()
    record.agent_context = dict(request_context.get() or {})
    # 调用点没传的字段保持「没有」。formatter 据此省略，不写 null。
    record.agent_fields = getattr(record, "agent_fields", {}) or {}


class _ContextFilter(logging.Filter):
    """挂在我们自己的 handler 上。uvicorn 的 handler 不挂它，启动横幅不受影响。

    记录在创建时已经冻过一次；这里再写一次是为了经标准库 logger 直接打出的
    记录（当前没有这种调用）也能带上字段。两次结果一致。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        _freeze(record)
        return True


def _merged_fields(record: logging.LogRecord) -> dict:
    """上下文在前、调用点在后：调用点显式传的值覆盖中间件注入的同名字段。"""
    merged = dict(getattr(record, "agent_context", {}) or {})
    merged.update(getattr(record, "agent_fields", {}) or {})
    return merged


class JsonFormatter(logging.Formatter):
    """一行一个 JSON 对象。栈进 ``exc`` 字段，不让每一行栈变成一条日志。"""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload: dict[str, Any] = {
            "ts": record.agent_ts,
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "pid": record.agent_pid,
            "version": record.agent_version,
        }
        ctx = getattr(record, "agent_context", {}) or {}
        if ctx.get("request_id"):
            payload["request_id"] = ctx["request_id"]
        payload.update(_merged_fields(record))
        # request_id 已在上面按「有才写」处理过；合并时可能再写一次，无害。
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
            payload.setdefault("exc_type", record.exc_info[0].__name__ if record.exc_info[0] else "")
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """本地开发用。同一批字段，人眼能读。"""

    def format(self, record: logging.LogRecord) -> str:
        head = (
            f"{record.agent_ts} {record.levelname} {record.name} {record.getMessage()}"
        )
        parts = []
        ctx = getattr(record, "agent_context", {}) or {}
        if ctx.get("request_id"):
            parts.append(f"request_id={ctx['request_id']}")
        parts.append(f"version={record.agent_version}")
        for key in sorted(_merged_fields(record)):
            if key == "request_id":
                continue
            parts.append(f"{key}={_merged_fields(record)[key]}")
        line = head + (" " + " ".join(parts) if parts else "")
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _resolve_format() -> str:
    """显式 LOG_FORMAT 优先；否则 sqlite → text，其余 → json。"""
    explicit = os.environ.get("LOG_FORMAT", "").strip().lower()
    if explicit:
        return explicit
    if settings.is_sqlite:
        return "text"
    return "json"


def _resolve_level() -> str:
    return os.environ.get("LOG_LEVEL", "info").strip().lower() or "info"


_CONFIGURED = False


def configure_logging() -> None:
    """配置进程的日志。必须在任何业务模块 import logger 之前调用一次。

    重复调用是替换而不是叠加：测试会在不同格式之间切换，留着旧 handler
    会让一条日志打两遍。
    """
    global _CONFIGURED

    fmt = _resolve_format()
    if fmt not in ("text", "json"):
        raise SystemExit(f"LOG_FORMAT 只接受 text 或 json，收到 {fmt!r}")
    level_name = _resolve_level()
    level = logging.getLevelName(level_name.upper())
    if not isinstance(level, int):
        raise SystemExit(f"LOG_LEVEL 无法识别: {level_name!r}")

    formatter: logging.Formatter
    if fmt == "json":
        formatter = JsonFormatter()
    else:
        formatter = TextFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())

    root = logging.getLogger(LOGGER_ROOT)
    root.setLevel(level)
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    # 不向 root 传播。否则 caplog（挂在 root 上）与我们的 handler 各打一遍，
    # 线上则会再被 uvicorn 的 root handler 打一遍纯文本。
    root.propagate = False

    # 第三方 logger 的级别在同一处钉死。不设 sqlalchemy echo（方案 §1.7）：
    # echo=True 会把 SQL 与绑定参数打进日志，白名单管不到那里。
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    for name in ("oss2", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True


def logging_configured() -> bool:
    return _CONFIGURED
