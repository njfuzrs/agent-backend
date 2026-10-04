"""身份落账（P3）：events / usage_ledger 加 user_ref

Revision ID: 0016
Revises: 0015

user_ref = 上报时设备凭据所绑定的人（devices.user_ref 的快照），入库时从
DeviceContext 取，不信任 body。未登录设备（注册码）为 NULL。

不建外键：与 device_id 同款理由 —— 用户行删了事件和账本仍要在，审计才讲得清。
旧行不回填：上报那一刻设备归谁，事后无从得知；用今天的 devices.user_ref 去补
会把换过人的设备的历史记到新主人头上。

全是新增可空列，旧代码不可见。生产不跑 downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("user_ref", sa.Integer(), nullable=True))
    op.create_index("idx_events_user_ref", "events", ["user_ref"])
    op.add_column("usage_ledger", sa.Column("user_ref", sa.Integer(), nullable=True))
    op.create_index("idx_usage_ledger_user_ref", "usage_ledger", ["user_ref"])


def downgrade() -> None:
    op.drop_index("idx_usage_ledger_user_ref", table_name="usage_ledger")
    op.drop_column("usage_ledger", "user_ref")
    op.drop_index("idx_events_user_ref", table_name="events")
    op.drop_column("events", "user_ref")
