"""marketplace：插件市场（P5）—— 新表 market_items / market_versions / market_downloads / market_audit

Revision ID: 0018
Revises: 0017

- market_items：一个插件一行，name 全局唯一。可见范围 org / team 存 slug 文本副本，不建 FK。
- market_versions：draft → published → yanked。制品存对象存储，库里只有 key 与 sha256。
  item 外键 RESTRICT：版本不删，条目也不删。
- market_downloads：设备拉制品一行，只追加。user_ref 从设备凭据取，不建 FK。
- market_audit：只追加。item_id SET NULL，name / version 存文本副本。

全是新表，旧代码不可见。生产不跑 downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("maintainer", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("idx_market_items_org_id", "market_items", ["org_id"])
    op.create_table(
        "market_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("components_json", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Text(), nullable=True),
        sa.Column("yanked_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["item_id"], ["market_items.id"], name="fk_market_versions_item_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "version", name="uq_market_versions_item_version"),
    )
    op.create_index("idx_market_versions_status", "market_versions", ["status"])
    op.create_table(
        "market_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["item_id"], ["market_items.id"], name="fk_market_audit_item_id", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_market_audit_created_at", "market_audit", ["created_at"])
    op.create_index("idx_market_audit_item_id", "market_audit", ["item_id"])
    op.create_table(
        "market_downloads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("item_name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("user_ref", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_market_downloads_created_at", "market_downloads", ["created_at"])
    op.create_index("idx_market_downloads_item_name", "market_downloads", ["item_name"])


def downgrade() -> None:
    op.drop_index("idx_market_downloads_item_name", table_name="market_downloads")
    op.drop_index("idx_market_downloads_created_at", table_name="market_downloads")
    op.drop_table("market_downloads")
    op.drop_index("idx_market_audit_item_id", table_name="market_audit")
    op.drop_index("idx_market_audit_created_at", table_name="market_audit")
    op.drop_table("market_audit")
    op.drop_index("idx_market_versions_status", table_name="market_versions")
    op.drop_table("market_versions")
    op.drop_index("idx_market_items_org_id", table_name="market_items")
    op.drop_table("market_items")
