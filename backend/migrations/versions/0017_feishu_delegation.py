"""feishu：委托授权（P4）—— 新表 feishu_tokens / feishu_call_audit

Revision ID: 0017
Revises: 0016

- feishu_tokens：一人一行，access / refresh 都是 Fernet 密文（密钥 TOKEN_ENC_KEY 只在 .env）。
  外键 CASCADE 到 users：用户行删了 token 跟着删。
- feishu_call_audit：远程 MCP 每次工具调用一行，只追加。只记文档 token，不记内容。
  user_id / device_id 不建外键：人和设备删了审计仍要在。

全是新表，旧代码不可见。生产不跑 downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feishu_tokens",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("access_enc", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.Text(), nullable=False),
        sa.Column("refresh_enc", sa.Text(), nullable=True),
        sa.Column("refresh_expires_at", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_feishu_tokens_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "feishu_call_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("target_token", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_feishu_call_audit_created_at", "feishu_call_audit", ["created_at"])
    op.create_index("idx_feishu_call_audit_user_id", "feishu_call_audit", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_feishu_call_audit_user_id", table_name="feishu_call_audit")
    op.drop_index("idx_feishu_call_audit_created_at", table_name="feishu_call_audit")
    op.drop_table("feishu_call_audit")
    op.drop_table("feishu_tokens")
