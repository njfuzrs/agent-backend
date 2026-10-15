"""identity：新表 identity_audit（组织 / 团队 / 设备归属变更审计）

Revision ID: 0021
Revises: 0020

D1：管理台可以新建 / 改名组织与团队、调整设备的组织与团队。
这些操作决定策略与预算命中哪一层，必须有不可变记录。只追加，不建 FK（对象删了审计仍在）。

新表，旧代码不可见。生产不跑 downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "identity_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_identity_audit_created_at", "identity_audit", ["created_at"])
    op.create_index("idx_identity_audit_target", "identity_audit", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("idx_identity_audit_target", table_name="identity_audit")
    op.drop_index("idx_identity_audit_created_at", table_name="identity_audit")
    op.drop_table("identity_audit")
