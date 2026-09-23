"""event: events + event_rejects

规划 §4 M4 / 服务端设计 §2。events 用 Text 存 metadata JSON、BigInteger 存 client_ts
（毫秒 epoch 超过 PG int4 上限 2.1e9，存 Text 会让排序变成字典序）、Text 存 received_at
（ISO，字典序=时序）。这两列故意不同类型，见 model.py 注释。

不建 FK 到 devices / trajectories：设备删了事件仍在；事件几乎总是先到，建 FK 会让
绝大多数插入失败。session_id 是值 join。

event_rejects 按事件名一行，只 upsert 计数，不存事件内容。被拒的名字是极小闭集，
这张表是「新事件被静默丢」这个已知风险的出口（契约 §2）。

fingerprint 唯一索引 + ON CONFLICT DO NOTHING 就是全部去重机制。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("client_ts", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_events_fingerprint", "events", ["fingerprint"], unique=True)
    op.create_index("idx_events_session_id", "events", ["session_id"], unique=False)
    op.create_index("idx_events_device_id", "events", ["device_id"], unique=False)
    op.create_index("idx_events_org_id", "events", ["org_id"], unique=False)
    op.create_index(
        "idx_events_name_received", "events", ["event_name", "received_at"], unique=False
    )

    op.create_table(
        "event_rejects",
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("event_name"),
    )


def downgrade() -> None:
    op.drop_table("event_rejects")
    op.drop_index("idx_events_name_received", table_name="events")
    op.drop_index("idx_events_org_id", table_name="events")
    op.drop_index("idx_events_device_id", table_name="events")
    op.drop_index("idx_events_session_id", table_name="events")
    op.drop_index("uq_events_fingerprint", table_name="events")
    op.drop_table("events")
