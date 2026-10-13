"""marketplace：CI 上传凭据（P2′）—— 新表 market_upload_tokens / market_token_audit

Revision ID: 0019
Revises: 0018

- market_upload_tokens：机器凭据，只能上传 draft。只存 sha256，带组织范围与过期时间。
- market_token_audit：签发 / 吊销 / 越权尝试，只追加。token_id SET NULL，名字存文本副本。

全是新表，旧代码不可见。生产不跑 downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_upload_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_table(
        "market_token_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_id", sa.Integer(), nullable=True),
        sa.Column("token_name", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["token_id"], ["market_upload_tokens.id"], name="fk_market_token_audit_token_id", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_market_token_audit_created_at", "market_token_audit", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_market_token_audit_created_at", table_name="market_token_audit")
    op.drop_table("market_token_audit")
    op.drop_table("market_upload_tokens")
