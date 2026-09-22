"""policy: policies + policy_audit

规划 §4 M3。settings 用 Text 存 JSON 文本（不含 source）：与本仓既有列类型一致，
PG / SQLite 行为无差异，下发时由 service 层 json.loads 还原，并写死 source=remote。

不建 FK 到 devices / teams / organizations：设备删了策略行仍在，审计才讲得清。
org_id 是文本副本，求值收窄用。

部分唯一索引 (scope_type, scope_id) WHERE disabled_at IS NULL：同层只能一份生效。
SQLite 3.8+ / PG 都支持；本地 SQLite 必须能 upgrade。

审计表的 policy_id 走 SET NULL 而不是 CASCADE —— 真删一条策略时审计必须留下来。
scope_type / scope_id 另存文本副本兜底。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("settings_json", sa.Text(), nullable=False),
        sa.Column("disabled_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_policies_scope", "policies", ["scope_type", "scope_id"], unique=False)
    op.create_index("idx_policies_org_id", "policies", ["org_id"], unique=False)
    op.create_index("idx_policies_disabled_at", "policies", ["disabled_at"], unique=False)
    op.create_index(
        "uq_policies_scope_enabled",
        "policies",
        ["scope_type", "scope_id", "org_id"],
        unique=True,
        sqlite_where=sa.text("disabled_at IS NULL"),
        postgresql_where=sa.text("disabled_at IS NULL"),
    )

    op.create_table(
        "policy_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("old_settings_json", sa.Text(), nullable=True),
        sa.Column("new_settings_json", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["policies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_policy_audit_scope", "policy_audit", ["scope_type", "scope_id"], unique=False)
    op.create_index("idx_policy_audit_created_at", "policy_audit", ["created_at"], unique=False)
    op.create_index("idx_policy_audit_policy_id", "policy_audit", ["policy_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_policy_audit_policy_id", table_name="policy_audit")
    op.drop_index("idx_policy_audit_created_at", table_name="policy_audit")
    op.drop_index("idx_policy_audit_scope", table_name="policy_audit")
    op.drop_table("policy_audit")

    op.drop_index("uq_policies_scope_enabled", table_name="policies")
    op.drop_index("idx_policies_disabled_at", table_name="policies")
    op.drop_index("idx_policies_org_id", table_name="policies")
    op.drop_index("idx_policies_scope", table_name="policies")
    op.drop_table("policies")
