"""三张审计表加可空 request_id

日志方案 §3.9，本方案里唯一的 schema 变更。审计行要能跳回同一次请求的日志，
两边靠这一个值对上。

列可空、无默认值、不建外键、不建索引：

- 旧行不回填。不为历史行编造 request_id。
- 脚本直接改库没有请求，写入时取不到就留空。
- 审计表按时间查，request_id 是跳转用的，不是检索用的。等真有慢查询再加索引。

event_rejects 不加。它是聚合计数，不是一次操作一行，没有「这一次」可以挂。

生产不跑 downgrade（退代码不退 DDL）。可空列对旧代码无影响，回滚时保留不删。
downgrade 留着只为迁移链可逆、本地能测。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 只这三张。event_rejects 是聚合计数，不在其中。
_TABLES = ("feature_flag_audit", "policy_audit", "budget_audit")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("request_id", sa.Text(), nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "request_id")
