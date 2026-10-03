"""auth：CLI 飞书登录（P2）—— auth_states 加 CLI 列、新表 login_codes、devices.user_ref

Revision ID: 0015
Revises: 0014

- auth_states 加四个可空列：kind=cli 时记下 CLI 本地回调端口、CLI 的 PKCE challenge、
  CLI 自己的 state、device_id。kind=web 的行全为空。
- login_codes：callback 签发的一次性登录码，60 秒有效，只存 hash。
- devices.user_ref：设备归属的人（users.id），只由服务端在登录兑换时写入。
  旧的自报 user_id 列保留但不再信任。用户行删了设备不跟着删，置空即可。

全是新增，旧代码不可见。生产不跑 downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("auth_states", sa.Column("cli_port", sa.Integer(), nullable=True))
    op.add_column("auth_states", sa.Column("cli_challenge", sa.Text(), nullable=True))
    op.add_column("auth_states", sa.Column("cli_state", sa.Text(), nullable=True))
    op.add_column("auth_states", sa.Column("device_id", sa.Text(), nullable=True))

    op.create_table(
        "login_codes",
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("cli_challenge", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("used_at", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_login_codes_user_id", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("code_hash"),
    )
    op.create_index("idx_login_codes_expires_at", "login_codes", ["expires_at"])

    # SQLite 加带外键的列要走 batch（重建表）；PG 下就是一条 ALTER TABLE。
    with op.batch_alter_table("devices") as batch_op:
        batch_op.add_column(sa.Column("user_ref", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_devices_user_ref_users", "users", ["user_ref"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index("idx_devices_user_ref", ["user_ref"])


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_index("idx_devices_user_ref")
        batch_op.drop_constraint("fk_devices_user_ref_users", type_="foreignkey")
        batch_op.drop_column("user_ref")
    op.drop_index("idx_login_codes_expires_at", table_name="login_codes")
    op.drop_table("login_codes")
    with op.batch_alter_table("auth_states") as batch_op:
        batch_op.drop_column("device_id")
        batch_op.drop_column("cli_state")
        batch_op.drop_column("cli_challenge")
        batch_op.drop_column("cli_port")
