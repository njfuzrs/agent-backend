"""heal: 收敛 session_id / ai_score 的索引命名 —— 0003 之后剩下的最后一批漂移

0003 清完 26 条默认值漂移后，`alembic check` 在**生产 PG 上**又暴露 4 条操作
（本地 SQLite 没有，因为本地库由 0001 直接建出，命名天生就对）：

    remove_index      idx_traj_ai_score
    remove_index      idx_traj_session_id
    remove_constraint UNIQUE (session_id)          ← trajectories_session_id_key
    add_index         ix_trajectories_session_id   (unique=True)

来源同 0003：`deploy/migrate_to_pg.py` 手写建表时用了自己的 `idx_traj_*` 命名，
而模型靠 `Column(..., unique=True, index=True)` 让 SQLAlchemy 生成 `ix_<表>_<列>`。

**这批是纯命名/实现层差异，不是缺约束**（部署前逐条核过）：

- `ai_score`：生产同时有 `idx_traj_ai_score`（建表脚本）和 `ix_trajectories_ai_score`
  （0002 补的）。**两个索引完全等价**，都是 btree(ai_score) 非唯一 —— 属重复索引，
  白付一份写入维护成本。删掉旧名那个。
- `session_id`：唯一性由 `trajectories_session_id_key`（UNIQUE **约束**）保证，
  模型期望 `ix_trajectories_session_id`（UNIQUE **索引**）。二者对上传去重的效果
  逐字相同，差别只在 PG 把它记在 pg_constraint 还是仅 pg_index。另外还有一个
  多余的非唯一 `idx_traj_session_id`，被上面那个唯一索引完全覆盖。

**session_id 的唯一性在迁移全程不中断** —— 这是本迁移最重要的不变量。
`/upload/session-file` 靠它做去重，一旦出现窗口期就可能插入重复会话。
所以顺序是**先建新的唯一索引，再删旧约束**，而不是反过来：

    ① CREATE UNIQUE INDEX ix_trajectories_session_id   ← 此刻两道保护并存
    ② DROP CONSTRAINT trajectories_session_id_key      ← 删掉时 ① 已在生效
    ③ DROP INDEX idx_traj_session_id（非唯一，多余）
    ④ DROP INDEX idx_traj_ai_score（与 ix_trajectories_ai_score 重复）

**锁**：`CREATE UNIQUE INDEX`（非 CONCURRENTLY）会持 SHARE 锁挡住写入，
9 千行的表上是毫秒级。不用 CONCURRENTLY 是因为它不能在事务里跑，
而 alembic 的迁移默认包在事务中 —— 换来的是「迁移失败能整体回滚」，
在这个表规模下这个取舍是对的。部署时同样带 `PGOPTIONS='-c lock_timeout=15s'`。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "trajectories"

TARGET_UNIQUE_INDEX = "ix_trajectories_session_id"
LEGACY_UNIQUE_CONSTRAINT = "trajectories_session_id_key"
REDUNDANT_INDEXES = [
    "idx_traj_session_id",   # 非唯一，被 ix_trajectories_session_id 完全覆盖
    "idx_traj_ai_score",     # 与 ix_trajectories_ai_score 重复
]


def _indexes() -> set[str]:
    return {ix["name"] for ix in inspect(op.get_bind()).get_indexes(TABLE)}


def _unique_constraints() -> set[str]:
    insp = inspect(op.get_bind())
    return {c["name"] for c in insp.get_unique_constraints(TABLE)}


def upgrade() -> None:
    """幂等：每步都先查实际状态。全新库（0001 建出）命名已正确 → 全部跳过。"""
    indexes = _indexes()

    # ① 先建目标唯一索引 —— 必须在删旧约束之前，保证 session_id 唯一性无窗口期
    if TARGET_UNIQUE_INDEX not in indexes:
        op.create_index(TARGET_UNIQUE_INDEX, TABLE, ["session_id"], unique=True)

    # ② 旧唯一约束此时已被 ① 覆盖，可以安全删除
    if LEGACY_UNIQUE_CONSTRAINT in _unique_constraints():
        op.drop_constraint(LEGACY_UNIQUE_CONSTRAINT, TABLE, type_="unique")

    # ③④ 删冗余索引（重新读一次：① 可能已改变索引集合）
    indexes = _indexes()
    for name in REDUNDANT_INDEXES:
        if name in indexes:
            op.drop_index(name, table_name=TABLE)


def downgrade() -> None:
    """空实现 —— 与 0002 / 0003 同理：向模型收敛的愈合，不可逆且不需要可逆。

    恢复 `idx_traj_*` 命名等于把 migrate_to_pg.py 的手写命名重新写回 schema。
    唯一性本身并没有丢（`ix_trajectories_session_id` 在，且它就是模型声明的那个），
    所以回滚没有任何收益，反而会重新引入重复索引。

    需要回到基线时用 `alembic downgrade base`：0001 的 downgrade 会 drop_table。
    """
    pass
