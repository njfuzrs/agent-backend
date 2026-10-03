"""auth：users / auth_states / auth_audit（P1 管理台飞书登录）

Revision ID: 0014
Revises: 0013

三张新表，旧代码不可见，回滚代码时空置无害。生产不跑 downgrade。
auth_audit.user_id 不建外键：用户行删了，审计仍要能回答「谁登录过」。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("tenant_key", sa.Text(), nullable=False),
        sa.Column("union_id", sa.Text(), nullable=False),
        sa.Column("open_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "tenant_key", "union_id", name="uq_users_provider_identity"),
    )

    op.create_table(
        "auth_states",
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("nonce_hash", sa.Text(), nullable=False),
        sa.Column("redirect_to", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("used_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index("idx_auth_states_expires_at", "auth_states", ["expires_at"])

    op.create_table(
        "auth_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_auth_audit_created_at", "auth_audit", ["created_at"])
    op.create_index("idx_auth_audit_user_id", "auth_audit", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_auth_audit_user_id", table_name="auth_audit")
    op.drop_index("idx_auth_audit_created_at", table_name="auth_audit")
    op.drop_table("auth_audit")
    op.drop_index("idx_auth_states_expires_at", table_name="auth_states")
    op.drop_table("auth_states")
    op.drop_table("users")
