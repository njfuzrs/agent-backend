"""ISO8601 UTC 时间。与轨迹模块一样用 Text 存，不用 DateTime 列。"""

from datetime import datetime, timedelta, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def iso_after(*, days: int = 0, hours: int = 0) -> str:
    return (utc_now() + timedelta(days=days, hours=hours)).isoformat()


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_expired(iso_ts: str, now: datetime | None = None) -> bool:
    return parse_iso(iso_ts) <= (now or utc_now())
