"""heal: 抹平旧加列路径留下的 schema 漂移（索引 + 遗留默认值）

**M0 第一个真实生效的迁移，也是「迁移链能改已有库」的自证。**

规划 §1.3 ① 那个 bug 在实测中留下三种痕迹，本迁移全部抹平：

1. **缺 7 个索引**。旧 `_migrate_sqlite_columns()` 只执行 `ALTER TABLE ADD COLUMN`，
   不建索引；`create_all` 只在**建表时**建索引。实测本地开发库 17 个索引里只有 10 个。

2. **遗留的 server default，且带一个引号 bug**。旧代码拼的是
   `ADD COLUMN ai_grade TEXT DEFAULT ''''`（默认值本身已带引号，又被当字符串拼一次），
   SQLite 把它解析成「两个撇号组成的字符串」而不是空串：

       ALTER TABLE t ADD COLUMN legacy TEXT DEFAULT "''";  -- 插入后得到 '' （2 字符）
       ALTER TABLE t ADD COLUMN ok     TEXT DEFAULT '';    -- 插入后得到 空串（0 字符）

   模型侧只有 Python 的 `default=`，从不声明 `server_default`。所以这些库侧默认值
   **既是漂移源，又埋着一个错值**。实测 63 行数据零污染（应用总是显式写值），
   所以直接删掉库侧默认值、以模型为唯一真相 —— 而不是把 bug 固化进模型。

3. **生产 PG 可能整批缺列**（最严重）。`deploy/migrate_to_pg.py` 的建表语句里
   **完全没有 16 个 AI 评分列**，而补列逻辑被 `if settings.is_sqlite` 挡住 ——
   生产从来没有过添加这些列的路径。本迁移会把缺的列补上。

**幂等设计**：三种库都要能跑通 —— 全新库（0001 已建全，什么都不做）、
本地漂移库（缺索引 + 有遗留默认值）、生产 PG（可能缺列）。
所以每一步都先查实际状态再决定动作，不做无条件 DDL。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "trajectories"

# 旧路径新增的列（列名, 类型）。生产 PG 若缺列，在此补齐。
LEGACY_COLUMNS = [
    ("oss_key", sa.Text()),
    ("sha256", sa.Text()),
    ("file_size", sa.Integer()),
    ("user_id", sa.Text()),
    ("device_id", sa.Text()),
    ("deleted_at", sa.Text()),
    ("ai_score", sa.Integer()),
    ("ai_quality_status", sa.Text()),
    ("ai_grade", sa.Text()),
    ("rule_score", sa.Integer()),
    ("rule_details", sa.Text()),
    ("rule_flags", sa.Text()),
    ("heuristic_score", sa.Integer()),
    ("heuristic_details", sa.Text()),
    ("heuristic_patterns", sa.Text()),
    ("llm_score", sa.Integer()),
    ("llm_details", sa.Text()),
    ("llm_reasoning", sa.Text()),
    ("llm_suggested_task_type", sa.Text()),
    ("llm_eval_model", sa.Text()),
    ("scored_at", sa.Text()),
    ("score_version", sa.Integer()),
]

# (索引名, 列名, 唯一)
INDEXES = [
    ("idx_traj_ai_quality_status", "ai_quality_status", False),
    ("idx_traj_ai_grade", "ai_grade", False),
    ("idx_traj_score_version", "score_version", False),
    ("idx_traj_deleted_at", "deleted_at", False),
    ("idx_traj_user_id", "user_id", False),
    ("idx_traj_device_id", "device_id", False),
    ("ix_trajectories_ai_score", "ai_score", False),
]

# 需要清掉库侧默认值的列（模型只声明 Python 侧 default）
DROP_SERVER_DEFAULT = [
    "ai_quality_status", "ai_grade",
    "rule_details", "rule_flags",
    "heuristic_details", "heuristic_patterns",
    "llm_details", "llm_reasoning",
    "llm_suggested_task_type", "llm_eval_model",
    "score_version",
]


def _inspector():
    return inspect(op.get_bind())


def _columns() -> dict:
    return {c["name"]: c for c in _inspector().get_columns(TABLE)}


def _indexes() -> set[str]:
    return {ix["name"] for ix in _inspector().get_indexes(TABLE)}


def upgrade() -> None:
    # ① 补缺列（生产 PG 很可能走到这里；本地库已有则跳过）
    existing_cols = _columns()
    for name, type_ in LEGACY_COLUMNS:
        if name not in existing_cols:
            op.add_column(TABLE, sa.Column(name, type_, nullable=True))

    # ② 补缺索引
    existing_idx = _indexes()
    for name, column, unique in INDEXES:
        if name not in existing_idx:
            op.create_index(name, TABLE, [column], unique=unique)

    # ③ 清掉旧 ADD COLUMN 留下的库侧默认值（含那个双引号错值）。
    #    模型不声明 server_default，清掉后 `alembic check` 才能干净。
    cols = _columns()
    targets = [
        c for c in DROP_SERVER_DEFAULT
        if c in cols and cols[c].get("default") is not None
    ]
    if targets:
        # SQLite 不支持 ALTER COLUMN，必须 batch（重建表）；PG 下 batch 会退化为原生 ALTER。
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            for c in targets:
                batch_op.alter_column(c, server_default=None)


def downgrade() -> None:
    """空实现 —— 本迁移是「向模型收敛」的愈合，不可逆，且不需要可逆。

    三件事逐个说明为什么不回滚：

    - **索引**：这 7 个索引在 0001 的表定义里就有（全新库由 0001 建出）。它们归 0001 所有，
      0002 只是补上漂移库缺的那部分。若在这里 drop，`downgrade base` 会走到 0001 的
      downgrade 时再 drop 一次 → `no such index: ix_trajectories_ai_score`（实测踩到过）。
    - **库侧默认值**：本身带引号 bug（`DEFAULT "''"` 在 SQLite 里是「两个撇号的字符串」
      而非空串），恢复它等于把 bug 放回去。
    - **列**：删列会丢数据。

    需要回到基线时用 `alembic downgrade base` —— 0001 的 downgrade 会 drop_table，
    整表连索引一起消失，不依赖本函数。
    """
    pass
