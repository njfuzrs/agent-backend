"""flag: feature_flags + feature_flag_audit

规划 §4 M2 PR-2.2 点名的两张表。

值用 Text 存 JSON 文本（不用 sa.JSON）：与本仓既有列类型一致，PG / SQLite 行为无差异，
下发时由 service 层 json.loads 还原成客户端 FlagValue 的原生类型。

审计表的 flag_id 走 SET NULL 而不是 CASCADE —— 真删一条 flag 时审计必须留下来，
否则「谁删的」跟着被删的行一起消失。key 另存文本副本兜底。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("disabled_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("feature_flags", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_feature_flags_key"), ["key"], unique=True)
        batch_op.create_index("idx_feature_flags_disabled_at", ["disabled_at"], unique=False)

    op.create_table(
        "feature_flag_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("flag_id", sa.Integer(), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("old_value_json", sa.Text(), nullable=True),
        sa.Column("new_value_json", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["flag_id"], ["feature_flags.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("feature_flag_audit", schema=None) as batch_op:
        batch_op.create_index("idx_feature_flag_audit_key", ["key"], unique=False)
        batch_op.create_index("idx_feature_flag_audit_created_at", ["created_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("feature_flag_audit", schema=None) as batch_op:
        batch_op.drop_index("idx_feature_flag_audit_created_at")
        batch_op.drop_index("idx_feature_flag_audit_key")
    op.drop_table("feature_flag_audit")

    with op.batch_alter_table("feature_flags", schema=None) as batch_op:
        batch_op.drop_index("idx_feature_flags_disabled_at")
        batch_op.drop_index(batch_op.f("ix_feature_flags_key"))
    op.drop_table("feature_flags")
