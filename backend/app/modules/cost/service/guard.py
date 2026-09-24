"""账本上报与预算写入的上限、字段校验。

上限值**写死在常量里，不做成环境变量**（服务端设计 §8）：
    能配 = 生产上可能被调成一个没人记得的值；
    这些数字的依据在契约 §7，改它们要一起改文档 —— 这是对的。

`enforcement` 默认 `"alert"` 写死在 model server_default，不要做成部署配置 ——
能配 = 生产上可能被改成 block 而管理台还显示「默认告警」。

不校验 `cost_usd` 与 token 的关系：口径陷阱就是它们不可除。
不按 `endpoint_host` 拒收：不可信渠道仍入库，聚合时排除。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException

from app.core.timeutil import utc_now

# --- 上限（契约 §7）。改这里要同步改 01-契约.md ---------------------------------
MAX_BODY_BYTES = 16 * 1024  # 16 KiB。一行账本远小于 2KiB
MAX_SESSION_ID_CHARS = 64  # 24 位正规形状，留余量
MAX_HOST_CHARS = 256  # host only
MAX_STRING_CHARS = 256  # model / provider / appVersion

REQUIRED_FIELDS = (
    "sessionId",
    "ts",
    "model",
    "provider",
    "promptTotal",
    "cacheHit",
    "cacheWrite",
    "uncachedInput",
    "output",
    "costUSD",
    "savingsUSD",
    "durationMs",
)

# body 里的 deviceId / orgId / teamId / userId 出现也不读。不 400（客户端今天会带，剥掉即可）。

VALID_SCOPE_TYPES = frozenset({"device", "team", "org"})
VALID_PERIODS = frozenset({"session", "daily", "weekly", "monthly"})
VALID_ENFORCEMENTS = frozenset({"alert", "block"})

# 单价禁令：by-scope 响应 schema / 示例不得出现这些字段。门禁扫这个集合。
BANNED_UNIT_PRICE_FIELDS = frozenset(
    {
        "unit_price",
        "unitPrice",
        "cost_per_token",
        "costPerToken",
        "cost_usd_per_prompt",
        "costUSD_per_promptTotal",
    }
)


class BadLedger(Exception):
    """上报 body 形状错。路由把它翻成 400。"""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def current_period_key(period: str, now: datetime | None = None) -> str:
    """服务端算周期键。客户端不要自己算再跟服务端对。"""
    now = now or utc_now()
    if period == "session":
        return "session"
    if period == "daily":
        return now.strftime("%Y-%m-%d")
    if period == "weekly":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "monthly":
        return now.strftime("%Y-%m")
    raise HTTPException(status_code=422, detail=f"未知 period {period!r}")


def period_bounds(period: str, period_key: str) -> tuple[int, int]:
    """周期键 → [start, end) Unix 秒（UTC）。session 没有时间窗，不要调这个。"""
    try:
        if period == "daily":
            start = datetime.strptime(period_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = start + timedelta(days=1)
        elif period == "weekly":
            year_s, week_s = period_key.split("-W")
            start = datetime.fromisocalendar(int(year_s), int(week_s), 1).replace(tzinfo=timezone.utc)
            end = start + timedelta(days=7)
        elif period == "monthly":
            start = datetime.strptime(period_key, "%Y-%m").replace(tzinfo=timezone.utc)
            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)
        else:
            raise ValueError(period)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"非法 period_key {period_key!r}（period={period}）",
        ) from exc
    return int(start.timestamp()), int(end.timestamp())


def _as_int(value: Any, field: str, *, default: int = 0, required: bool = False) -> int:
    if value is None:
        if required:
            raise BadLedger(f"missing {field}")
        return default
    # bool 是 int 的子类，必须先挡
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadLedger(f"{field} must be a number")
    return int(value)


def _as_float(value: Any, field: str, *, default: float = 0.0, required: bool = False) -> float:
    if value is None:
        if required:
            raise BadLedger(f"missing {field}")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadLedger(f"{field} must be a number")
    return float(value)


def _as_opt_int(value: Any, field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadLedger(f"{field} must be a number")
    return int(value)


def _as_opt_float(value: Any, field: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadLedger(f"{field} must be a number")
    return float(value)


def _as_opt_str(value: Any, field: str, max_len: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BadLedger(f"{field} must be a string")
    if len(value) > max_len:
        raise BadLedger(f"{field} too long")
    return value or None


def parse_ledger_body(body: dict[str, Any]) -> dict[str, Any]:
    """把客户端 UsageLedgerEntry 翻成入库列。身份字段不在这里取。

    缺必填 → BadLedger。可选 side_* 缺省保持 None（不要存 0）。
    """
    missing = [k for k in REQUIRED_FIELDS if k not in body]
    if missing:
        raise BadLedger(f"missing {missing[0]}")

    session_id = body.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        raise BadLedger("missing sessionId")
    if len(session_id) > MAX_SESSION_ID_CHARS:
        raise BadLedger("sessionId too long")

    model = body.get("model")
    provider = body.get("provider")
    if not isinstance(model, str) or not model:
        raise BadLedger("missing model")
    if not isinstance(provider, str) or not provider:
        raise BadLedger("missing provider")
    if len(model) > MAX_STRING_CHARS:
        raise BadLedger("model too long")
    if len(provider) > MAX_STRING_CHARS:
        raise BadLedger("provider too long")

    return {
        "session_id": session_id,
        "ts": _as_int(body.get("ts"), "ts", required=True),
        "model": model,
        "provider": provider,
        "prompt_total": _as_int(body.get("promptTotal"), "promptTotal"),
        "cache_hit": _as_int(body.get("cacheHit"), "cacheHit"),
        "cache_write": _as_int(body.get("cacheWrite"), "cacheWrite"),
        "uncached_input": _as_int(body.get("uncachedInput"), "uncachedInput"),
        "output": _as_int(body.get("output"), "output"),
        "cost_usd": _as_float(body.get("costUSD"), "costUSD", required=True),
        "savings_usd": _as_float(body.get("savingsUSD"), "savingsUSD"),
        "duration_ms": _as_int(body.get("durationMs"), "durationMs"),
        "side_input_tokens": _as_opt_int(body.get("sideInputTokens"), "sideInputTokens"),
        "side_output_tokens": _as_opt_int(body.get("sideOutputTokens"), "sideOutputTokens"),
        "side_cost_usd": _as_opt_float(body.get("sideCostUSD"), "sideCostUSD"),
        "endpoint_host": _as_opt_str(body.get("endpointHost"), "endpointHost", MAX_HOST_CHARS),
        "app_version": _as_opt_str(body.get("appVersion"), "appVersion", MAX_STRING_CHARS),
        "peak_ratio": _as_opt_float(body.get("peakRatio"), "peakRatio"),
    }


def validate_enforcement(value: Any) -> str:
    if value not in VALID_ENFORCEMENTS:
        raise HTTPException(
            status_code=422,
            detail="enforcement 只允许 'alert' | 'block'（没有 downgrade）",
        )
    return value


def validate_period(value: Any) -> str:
    if value not in VALID_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"period 只允许 {sorted(VALID_PERIODS)}",
        )
    return value


def validate_scope_type(value: Any) -> str:
    if value not in VALID_SCOPE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"scope_type 只允许 {sorted(VALID_SCOPE_TYPES)}",
        )
    return value
