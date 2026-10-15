"""trajectory：加 end_source（会话结束方式）

Revision ID: 0020
Revises: 0019

sid-code 把「结果」与「结束方式」拆成两个维度：exit_status 表示结果，
metadata.end_source 表示怎么结束的（exit / abort / terminal_closed / error / clear）。
空闲时关终端的会话 exit_status 按末轮推断，end_source=terminal_closed。

可空新列，旧代码不可见。旧行不回填：历史轨迹的 end_source 要从对象存储里的
.traj 重新解析，不在迁移里做。生产不跑 downgrade。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trajectories", sa.Column("end_source", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trajectories", "end_source")
