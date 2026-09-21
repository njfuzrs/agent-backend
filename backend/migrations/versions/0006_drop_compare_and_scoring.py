"""drop compare 表与 AI 评分列 —— 管理台只做收集与简单统计

对比功能与 AI 评分已从前端/API 拆除。本迁移把对应 schema 收敛到模型：

- drop compare_group_items / compare_groups
- drop trajectories 上的 AI 评分列与索引

幂等：表/列/索引不存在则跳过。全新库由 0001 建出后再跑到这里，
生产 PG 上这些对象都在，会真正 drop。

SQLite 不支持 DROP COLUMN，走 batch_alter_table（env.py 的 render_as_batch
在 SQLite 下为 True；PG 下 batch 无副作用）。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "trajectories"

SCORE_INDEXES = [
    "idx_traj_ai_quality_status",
    "idx_traj_ai_grade",
    "idx_traj_score_version",
    "ix_trajectories_ai_score",
]

SCORE_COLUMNS = [
    "ai_score",
    "ai_quality_status",
    "ai_grade",
    "rule_score",
    "rule_details",
    "rule_flags",
    "heuristic_score",
    "heuristic_details",
    "heuristic_patterns",
    "llm_score",
    "llm_details",
    "llm_reasoning",
    "llm_suggested_task_type",
    "llm_eval_model",
    "scored_at",
    "score_version",
]


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "compare_group_items" in tables:
        op.drop_table("compare_group_items")
    if "compare_groups" in tables:
        op.drop_table("compare_groups")

    if TABLE not in tables:
        return

    indexes = {ix["name"] for ix in inspector.get_indexes(TABLE)}
    columns = {c["name"] for c in inspector.get_columns(TABLE)}
    drop_indexes = [name for name in SCORE_INDEXES if name in indexes]
    drop_columns = [col for col in SCORE_COLUMNS if col in columns]
    if not drop_indexes and not drop_columns:
        return

    with op.batch_alter_table(TABLE) as batch_op:
        for name in drop_indexes:
            batch_op.drop_index(name)
        for col in drop_columns:
            batch_op.drop_column(col)


def downgrade() -> None:
    """空实现。评分列与对比表已从产品里拿掉，不恢复。"""
    pass
