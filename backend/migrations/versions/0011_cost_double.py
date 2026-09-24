"""cost: 金额列 REAL(float4) → double precision(float8)

验收 F3：`0010` 的金额列用 `sa.REAL()`。SQLite 上 REAL 是 8 字节，看不出问题；
Postgres 上 REAL 是 `real`（float4，24 bit 尾数）。`0.01` 在 float8 里精确，
进 float4 变成 `0.009999999776482582`，下发后被客户端 `toFixed(2)` 吃成 `$0.00`。

`0010` 已上生产且有数据。按「回滚退代码不退 DDL」，这是一次新迁移，不改 `0010`。
`ALTER COLUMN … TYPE double precision` 是拓宽，float4 能表示的值 float8 都能表示，
不丢已有行。

不改 `trajectories.total_cost_usd`：它从 `0001` 起就是 REAL，不在本次验收范围。
不改用 Numeric：SQLite 本地开发会漂，见 model.py。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (表, 列, 可空)。nullable 必须与 0010 一致，否则 batch 重建时列约束会变。
_COLUMNS = (
    ("usage_ledger", "cost_usd", False),
    ("usage_ledger", "savings_usd", False),
    ("usage_ledger", "side_cost_usd", True),
    ("usage_ledger", "peak_ratio", True),
    ("budgets", "limit_usd", False),
)


def upgrade() -> None:
    # SQLite 不支持 ALTER COLUMN，batch 会重建表再拷数据；PG 下退化为原生 ALTER。
    # 一次 batch 改完一张表的所有列，避免 SQLite 按列重建多次。
    by_table: dict[str, list[tuple[str, bool]]] = {}
    for table, column, nullable in _COLUMNS:
        by_table.setdefault(table, []).append((column, nullable))
    for table, columns in by_table.items():
        with op.batch_alter_table(table) as batch_op:
            for column, nullable in columns:
                batch_op.alter_column(
                    column,
                    existing_type=sa.REAL(),
                    type_=sa.Double(),
                    existing_nullable=nullable,
                )


def downgrade() -> None:
    # 生产不跑 downgrade（退代码不退 DDL）。留着是为了迁移链可逆、本地能测。
    # float8 → float4 会把已经精确的小额再打回 float4，仅用于回退 schema。
    by_table: dict[str, list[tuple[str, bool]]] = {}
    for table, column, nullable in _COLUMNS:
        by_table.setdefault(table, []).append((column, nullable))
    for table, columns in by_table.items():
        with op.batch_alter_table(table) as batch_op:
            for column, nullable in columns:
                batch_op.alter_column(
                    column,
                    existing_type=sa.Double(),
                    type_=sa.REAL(),
                    existing_nullable=nullable,
                )
