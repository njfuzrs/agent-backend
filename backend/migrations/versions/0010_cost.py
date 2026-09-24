"""cost: usage_ledger + budgets + budget_audit

规划 §4 M5 / 服务端设计 §2。金额用 REAL（轨迹 total_cost_usd 同款）。ts 用 Integer
存 Unix 秒（~1.7e9，PG int4 上限内）；received_at 用 Text ISO。两列故意不同类型，
见 model.py 注释。side_* 可空：缺 = 无影子，存 NULL 不存 0。

不建 FK 到 devices / trajectories：设备删了账本仍在；会话进行中就 upsert，轨迹
还没到。

部分唯一索引 (scope_type, scope_id, org_id, period) WHERE disabled_at IS NULL：
同层同周期只能一份生效。SQLite 3.8+ / PG 都支持；本地 SQLite 必须能 upgrade。

审计表的 budget_id 走 SET NULL 而不是 CASCADE —— 真删一条预算时审计必须留下来。
scope_type / scope_id 另存文本副本兜底。

used_usd 不存表，每次 GET 从 usage_ledger 现算。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("ts", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("prompt_total", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Integer(), nullable=False),
        sa.Column("cache_write", sa.Integer(), nullable=False),
        sa.Column("uncached_input", sa.Integer(), nullable=False),
        sa.Column("output", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.REAL(), nullable=False),
        sa.Column("savings_usd", sa.REAL(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("side_input_tokens", sa.Integer(), nullable=True),
        sa.Column("side_output_tokens", sa.Integer(), nullable=True),
        sa.Column("side_cost_usd", sa.REAL(), nullable=True),
        sa.Column("endpoint_host", sa.Text(), nullable=True),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column("peak_ratio", sa.REAL(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_usage_ledger_device_session",
        "usage_ledger",
        ["device_id", "session_id"],
        unique=True,
    )
    op.create_index("idx_usage_ledger_org_id", "usage_ledger", ["org_id"], unique=False)
    op.create_index("idx_usage_ledger_session_id", "usage_ledger", ["session_id"], unique=False)
    op.create_index("idx_usage_ledger_received_at", "usage_ledger", ["received_at"], unique=False)
    op.create_index(
        "idx_usage_ledger_org_received",
        "usage_ledger",
        ["org_id", "received_at"],
        unique=False,
    )

    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("limit_usd", sa.REAL(), nullable=False),
        sa.Column("enforcement", sa.Text(), server_default="alert", nullable=False),
        sa.Column("disabled_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_budgets_scope", "budgets", ["scope_type", "scope_id"], unique=False)
    op.create_index("idx_budgets_org_id", "budgets", ["org_id"], unique=False)
    op.create_index(
        "uq_budgets_scope_period_enabled",
        "budgets",
        ["scope_type", "scope_id", "org_id", "period"],
        unique=True,
        sqlite_where=sa.text("disabled_at IS NULL"),
        postgresql_where=sa.text("disabled_at IS NULL"),
    )

    op.create_table(
        "budget_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("budget_id", sa.Integer(), nullable=True),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("old_json", sa.Text(), nullable=True),
        sa.Column("new_json", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["budget_id"], ["budgets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_budget_audit_scope", "budget_audit", ["scope_type", "scope_id"], unique=False)
    op.create_index("idx_budget_audit_created_at", "budget_audit", ["created_at"], unique=False)
    op.create_index("idx_budget_audit_budget_id", "budget_audit", ["budget_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_budget_audit_budget_id", table_name="budget_audit")
    op.drop_index("idx_budget_audit_created_at", table_name="budget_audit")
    op.drop_index("idx_budget_audit_scope", table_name="budget_audit")
    op.drop_table("budget_audit")

    op.drop_index("uq_budgets_scope_period_enabled", table_name="budgets")
    op.drop_index("idx_budgets_org_id", table_name="budgets")
    op.drop_index("idx_budgets_scope", table_name="budgets")
    op.drop_table("budgets")

    op.drop_index("idx_usage_ledger_org_received", table_name="usage_ledger")
    op.drop_index("idx_usage_ledger_received_at", table_name="usage_ledger")
    op.drop_index("idx_usage_ledger_session_id", table_name="usage_ledger")
    op.drop_index("idx_usage_ledger_org_id", table_name="usage_ledger")
    op.drop_index("uq_usage_ledger_device_session", table_name="usage_ledger")
    op.drop_table("usage_ledger")
