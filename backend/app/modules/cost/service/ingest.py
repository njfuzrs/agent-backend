"""账本入库：一条语句 upsert，latest-wins 整行覆盖。

## 为什么是 200 不是 202

账本一行 upsert 是毫秒级，没有「入队再写」的必要。202 会让人以为有队列。
events 用 202 是因为一批 ≤500 条；这里永远是 1 行。

## 禁止

- append 一行 —— 30 轮会话成本 ×30。规划原文点名要防的。
- 先 SELECT 再决定 INSERT/UPDATE —— 两 worker 并发双插，唯一索引 500。
- `cost_usd = usage_ledger.cost_usd + EXCLUDED.cost_usd` —— 把累计值又加一次。
- 按 `session_id` 单独唯一（不带 device）—— 两台设备理论上能撞 sessionId。
- 在写入路径上算 used_usd / 触发告警 —— 告警是客户端拿下发结果自己判。
  写入路径做聚合会让 upsert 的 p95 绑在全 org 求和上。

`device_id` / `org_id` / `team_id` 由调用方从 `DeviceContext` 传入，
**绝不从 body 取**。
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutil import utc_now_iso
from app.modules.cost.schemas import UsageIngestResponse

# 业务列（不含主键 id）。ON CONFLICT DO UPDATE 全部覆盖，不要漏。
# 禁止 cost_usd = usage_ledger.cost_usd + EXCLUDED.cost_usd。
_COLS = (
    "device_id",
    "org_id",
    "team_id",
    "session_id",
    "ts",
    "received_at",
    "model",
    "provider",
    "prompt_total",
    "cache_hit",
    "cache_write",
    "uncached_input",
    "output",
    "cost_usd",
    "savings_usd",
    "duration_ms",
    "side_input_tokens",
    "side_output_tokens",
    "side_cost_usd",
    "endpoint_host",
    "app_version",
    "peak_ratio",
)

_UPDATE_COLS = tuple(c for c in _COLS if c not in {"device_id", "session_id"})

_SET_CLAUSE = ",\n  ".join(f"{c} = EXCLUDED.{c}" for c in _UPDATE_COLS)

# PG 与 SQLite 这条语法相同。用 text() 一行，不要按 dialect 分派写路径。
# PG 用 xmax=0 判定新行（xmax=0 是新行）；SQLite 没有 xmax，RETURNING 仍给 id，
# 文案靠 upsert 前的 SELECT —— **只用于响应文案**，不要按它决定写不写。
_UPSERT_SQL = text(
    f"""
    INSERT INTO usage_ledger ({", ".join(_COLS)})
    VALUES ({", ".join(f":{c}" for c in _COLS)})
    ON CONFLICT (device_id, session_id) DO UPDATE SET
      {_SET_CLAUSE}
    RETURNING id
    """
)

_UPSERT_SQL_PG = text(
    f"""
    INSERT INTO usage_ledger ({", ".join(_COLS)})
    VALUES ({", ".join(f":{c}" for c in _COLS)})
    ON CONFLICT (device_id, session_id) DO UPDATE SET
      {_SET_CLAUSE}
    RETURNING (xmax = 0) AS inserted
    """
)

_EXISTS_SQL = text(
    """
    SELECT 1 FROM usage_ledger
    WHERE device_id = :device_id AND session_id = :session_id
    """
)


async def upsert_ledger(
    db: AsyncSession,
    parsed: dict[str, Any],
    *,
    device_id: str,
    org_id: str,
    team_id: str,
) -> UsageIngestResponse:
    """一条语句 upsert。身份从凭据，业务列从 parsed。

    latest-wins = 整行覆盖，不是字段级 merge。客户端每次发的是「本会话累计到
    现在」的完整快照；服务端若 merge，旧字段会残留。
    """
    received_at = utc_now_iso()
    params = {
        **parsed,
        "device_id": device_id,
        "org_id": org_id,
        "team_id": team_id or "",
        "received_at": received_at,
    }

    existed = False
    if settings.is_sqlite:
        # 只用于响应文案。两个 worker 并发时可能都说 inserted，库里仍 1 行。
        # 单测认库行数，不认并发下的文案。
        probe = await db.execute(
            _EXISTS_SQL,
            {"device_id": device_id, "session_id": parsed["session_id"]},
        )
        existed = probe.scalar() is not None
        await db.execute(_UPSERT_SQL, params)
        inserted: Literal["inserted", "updated"] = "updated" if existed else "inserted"
    else:
        result = await db.execute(_UPSERT_SQL_PG, params)
        row = result.first()
        inserted = "inserted" if (row is not None and bool(row[0])) else "updated"

    await db.commit()
    return UsageIngestResponse(upserted=inserted)
