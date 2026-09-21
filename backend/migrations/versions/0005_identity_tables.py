"""identity: 组织 / 团队 / 设备 / 凭据 / 一次性注册码

规划 PR-1.3 四张业务表，外加 enroll_codes 承载「管理员预置、单次使用」的注册码。
凭据与注册码都只存 sha256，不存原文。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_organizations_org_id"), ["org_id"], unique=True)

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "team_id", name="uq_teams_org_slug"),
    )
    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.create_index("idx_teams_organization_id", ["organization_id"], unique=False)

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("ver", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_devices_device_id"), ["device_id"], unique=True)
        batch_op.create_index("idx_devices_organization_id", ["organization_id"], unique=False)
        batch_op.create_index("idx_devices_last_seen_at", ["last_seen_at"], unique=False)
        batch_op.create_index("idx_devices_user_id", ["user_id"], unique=False)

    op.create_table(
        "device_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("device_credentials", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_device_credentials_token_hash"), ["token_hash"], unique=True)
        batch_op.create_index("idx_device_credentials_device_id", ["device_id"], unique=False)
        batch_op.create_index("idx_device_credentials_expires_at", ["expires_at"], unique=False)

    op.create_table(
        "enroll_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("used_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("enroll_codes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_enroll_codes_code_hash"), ["code_hash"], unique=True)
        batch_op.create_index("idx_enroll_codes_organization_id", ["organization_id"], unique=False)
        batch_op.create_index("idx_enroll_codes_used_at", ["used_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("enroll_codes", schema=None) as batch_op:
        batch_op.drop_index("idx_enroll_codes_used_at")
        batch_op.drop_index("idx_enroll_codes_organization_id")
        batch_op.drop_index(batch_op.f("ix_enroll_codes_code_hash"))
    op.drop_table("enroll_codes")

    with op.batch_alter_table("device_credentials", schema=None) as batch_op:
        batch_op.drop_index("idx_device_credentials_expires_at")
        batch_op.drop_index("idx_device_credentials_device_id")
        batch_op.drop_index(batch_op.f("ix_device_credentials_token_hash"))
    op.drop_table("device_credentials")

    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index("idx_devices_user_id")
        batch_op.drop_index("idx_devices_last_seen_at")
        batch_op.drop_index("idx_devices_organization_id")
        batch_op.drop_index(batch_op.f("ix_devices_device_id"))
    op.drop_table("devices")

    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.drop_index("idx_teams_organization_id")
    op.drop_table("teams")

    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_organizations_org_id"))
    op.drop_table("organizations")
