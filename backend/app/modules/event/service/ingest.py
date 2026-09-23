"""事件入库：指纹 + 白名单 + 单条批量 INSERT。

## 为什么 202 是「诚实的」而不是假的

主规划要求「5s 内必须返回（写入队列即 202）」。**第一版故意不引队列，同步批量插入。**

理由：一批 ≤500 行的 `INSERT ... ON CONFLICT DO NOTHING` 在 PG 上是毫秒级；引入内存
队列会带来「进程重启丢队列」这个新失败模式，而它比它要解决的问题更严重（生产是
`--workers 2`，内存队列还不共享）。

为了让 202 不是谎话，这里有两条硬纪律：
  1. 单次请求**一条** INSERT（多 VALUES），不是循环 500 次 add()；
  2. 请求处理里**不做**任何 join / 聚合 / 统计更新。计数只用 RETURNING 的行数。

**什么时候才引真队列**：当 `POST /events` 的 p95 超过 1s。届时的正确方案是写入中间表
或 broker，**不是**内存队列。写在这里是因为「202 但其实同步」看起来像个漏洞，下一个
人会顺手加内存队列 —— 那是退步，不是修复。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import utc_now_iso
from app.modules.event.schemas import EventIngestResponse
from app.modules.event.service.guard import (
    CTX_SESSION_ID,
    REASON_BAD_SHAPE,
    RejectedEvent,
    canonical_json,
    fingerprint,
    validate_client_ts,
    validate_event_name,
    validate_metadata,
)

logger = logging.getLogger("uvicorn.error")

# 列顺序与 INSERT 对齐。用 text() 而不是 dialects.{postgresql,sqlite}.insert：
# 那两个 on_conflict_do_nothing 是**两个不同的函数**，按 dialect 分派要写一个 if；
# 而 `ON CONFLICT DO NOTHING` 的语法在 PG 与 SQLite 里完全相同。
#
# **禁止**改成「先 SELECT fingerprint IN (...) 再插未命中的」：两个 worker 并发时两次
# SELECT 都说「不存在」，然后都插，靠唯一索引报错 —— 于是要么 500，要么退化成逐条
# try/except。DB 约束是唯一可靠的并发去重点。
#
# 不用 executemany：aiosqlite 的 rowcount 对 ON CONFLICT DO NOTHING 不可靠（常是 -1
# 或等于 values 条数）。一条多 VALUES + RETURNING，accepted = 返回行数。SQLite 3.35+
# 与 PG 都支持 RETURNING；本仓 SQLite 3.53 / 生产 PG 14 都够。
_COLS = (
    "fingerprint",
    "event_name",
    "device_id",
    "org_id",
    "team_id",
    "session_id",
    "client_ts",
    "received_at",
    "metadata_json",
)


def _insert_sql(n: int):
    values = ", ".join(
        "(" + ", ".join(f":{col}_{i}" for col in _COLS) + ")" for i in range(n)
    )
    return text(
        f"""
        INSERT INTO events ({", ".join(_COLS)})
        VALUES {values}
        ON CONFLICT (fingerprint) DO NOTHING
        RETURNING fingerprint
        """
    )


def _session_id_of(metadata: dict[str, Any]) -> str | None:
    """取 join key。缺失或非字符串 → NULL，事件仍入库（契约 §1）。"""
    value = metadata.get(CTX_SESSION_ID)
    return value if isinstance(value, str) and value else None


async def _bump_rejects(db: AsyncSession, counts: dict[tuple[str, str], int], now: str) -> None:
    """把被拒的 (event_name, reason) 计数 upsert 进 event_rejects。

    为什么要存而不是只打日志：rejected 是每次请求的瞬时返回值，客户端拿到就丢了。
    「客户端加了新事件名、服务端白名单没同步 → 事件被静默丢」这个已知风险如果只能
    靠翻服务器日志发现，就等于没有出口（契约 §2 / 管理台 §3）。

    这是本函数唯一违反「请求里不做额外写」的地方，代价可控：被拒的名字是极小闭集，
    正常情况下这个 dict 是空的，一条语句都不会发。
    """
    for (event_name, reason), delta in counts.items():
        # 每个占位符只用一次：sqlite3 不允许同一 named 参数出现两次。
        # 冲突后用 excluded.* 取 INSERT 行，避免再绑一遍 :delta / :reason。
        await db.execute(
            text(
                """
                INSERT INTO event_rejects (event_name, reason, count, first_seen_at, last_seen_at)
                VALUES (:name, :reason, :delta, :first_seen, :last_seen)
                ON CONFLICT (event_name) DO UPDATE SET
                    count = event_rejects.count + excluded.count,
                    reason = excluded.reason,
                    last_seen_at = excluded.last_seen_at
                """
            ),
            {
                "name": event_name[:200],
                "reason": reason,
                "delta": delta,
                "first_seen": now,
                "last_seen": now,
            },
        )


async def ingest_events(
    db: AsyncSession,
    raw_events: list[dict[str, Any]],
    *,
    device_id: str,
    org_id: str,
    team_id: str,
) -> EventIngestResponse:
    """校验 + 批量入库。返回 accepted / deduped / rejected。

    `device_id` / `org_id` / `team_id` 由调用方从 `DeviceContext` 传入，
    **绝不从 body 取** —— body 里的同名字段是攻击面（契约 §1）。这里不接受
    「body 有就用 body 的」这种回退：那等于让上报方自己声明归属。
    """
    received_at = utc_now_iso()
    rows: list[dict[str, Any]] = []
    rejected = 0
    reject_counts: dict[tuple[str, str], int] = {}
    # 同一批里指纹重复的先在内存去重：ON CONFLICT 对「同一 INSERT 内的重复行」
    # 在 SQLite 上不生效，会整句失败。
    seen: set[str] = set()
    in_batch_dupes = 0

    for raw in raw_events:
        try:
            if not isinstance(raw, dict):
                raise RejectedEvent(REASON_BAD_SHAPE, "")
            event_name = validate_event_name(raw.get("eventName"))
            client_ts = validate_client_ts(raw.get("timestamp"), event_name)
            metadata = validate_metadata(raw.get("metadata"), event_name)
        except RejectedEvent as exc:
            rejected += 1
            key = (exc.event_name or "(unnamed)", exc.reason)
            reject_counts[key] = reject_counts.get(key, 0) + 1
            continue

        fp = fingerprint(device_id, event_name, client_ts, metadata)
        if fp in seen:
            in_batch_dupes += 1
            continue
        seen.add(fp)
        rows.append(
            {
                "fingerprint": fp,
                "event_name": event_name,
                "device_id": device_id,
                "org_id": org_id,
                "team_id": team_id,
                "session_id": _session_id_of(metadata),
                "client_ts": client_ts,
                "received_at": received_at,
                "metadata_json": canonical_json(metadata),
            }
        )

    accepted = 0
    if rows:
        params: dict[str, Any] = {}
        for i, row in enumerate(rows):
            for col in _COLS:
                params[f"{col}_{i}"] = row[col]
        result = await db.execute(_insert_sql(len(rows)), params)
        accepted = len(result.fetchall())

    deduped = len(rows) - accepted + in_batch_dupes
    if reject_counts:
        await _bump_rejects(db, reject_counts, received_at)
    await db.commit()

    if rejected:
        logger.warning(
            "events 上报含被拒事件: device=%s rejected=%d 明细=%s",
            device_id,
            rejected,
            {f"{name}:{reason}": n for (name, reason), n in reject_counts.items()},
        )
    return EventIngestResponse(accepted=accepted, deduped=max(deduped, 0), rejected=rejected)
