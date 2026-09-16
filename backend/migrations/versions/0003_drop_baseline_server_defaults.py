"""heal: 清掉基线列的库侧默认值 —— 让 `alembic check` 真正能当门禁用

0002 只清了 16 个 AI 评分列的 server_default，于是迁移跑完 `alembic check` 仍然报
26 条 `modify_default`。这批漂移的来源不是评分列那条旧加列路径，而是
`deploy/migrate_to_pg.py` 的建表语句 —— 它照 SQLite 的写法给基线列都带上了
`DEFAULT ''` / `DEFAULT 0`，而 ORM 模型从头到尾只声明 Python 侧 `default=`，
不声明 `server_default`。两边不一致，`check` 就永远是红的。

**为什么必须清掉，而不是把 server_default 补进模型**

一个永远报红的 `check` 等于没有 check：下次真有人漏写迁移，这 26 条噪音会把那一条
盖掉。要让它能当门禁，就得让「模型 == 库」这个等式真正成立。

两个方向都能让等式成立，选清库侧而不是补模型侧，理由和 0002 步骤③ 一致 ——
**以模型为唯一真相**。库侧默认值是 migrate_to_pg.py 的实现细节漏进 schema 的产物，
不是设计意图；把它固化进模型等于承认「PG 建表脚本」也是 schema 的定义方之一，
以后每加一列都要在两个地方对齐。

**为什么清掉是安全的（逐条核过，2026-09-16）**

- 26 列在 `model.py` 里**全部**有等价的 Python `default=`（tool_source="claude-code"、
  tokens_sent=0、tags="[]" …），清掉库侧后新插入的行拿到的值逐字不变。
- 写入路径**全部**走 ORM：`grep -riE "insert into|update .* set"` 在 app/ 下无命中；
  `sync.py` / `pull.py` 只走 HTTP API，不直连库；crontab 里没有直写库的任务。
  所以不存在「绕过 ORM 的原生 INSERT 依赖库侧默认值」这种情况。
- 只改列的默认值元数据，**不动任何已有行**（`ALTER COLUMN ... DROP DEFAULT` 不重写数据）。

**锁**：`DROP DEFAULT` 要表级 ACCESS EXCLUSIVE 锁，但它只改 catalog、不扫表，
持锁时间与表大小无关（实测 49 万行的 tool_steps 亦然）。生产采集链路在实时写入，
部署时用 `PGOPTIONS='-c lock_timeout=15s'` 兜底：宁可迁移失败重试，也不卡住上传。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# {表: [列, ...]} —— 建表脚本留下库侧默认值、而模型只有 Python default 的列。
# 与 0002 的 DROP_SERVER_DEFAULT 不重叠：那批是评分列，这批是基线列。
TARGETS: dict[str, list[str]] = {
    "trajectories": [
        "tool_source", "model",
        "tokens_sent", "tokens_received",
        "cache_read_tokens", "cache_creation_tokens",
        "total_tokens", "total_cost_usd",
        "total_steps", "total_api_calls",
        "exit_status", "tools_used", "files_edited",
        "working_directory", "task_type", "project_name", "tags",
        "quality_status", "quality_notes",
        "has_thinking", "has_sub_agent",
        "first_prompt", "traj_file_size",
    ],
    "compare_groups": ["description", "task_prompt"],
    "compare_group_items": ["notes"],
}


def _columns(table: str) -> dict:
    return {c["name"]: c for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    """幂等：只清「确实还带着库侧默认值」的列。

    全新库由 0001 建出，本身不带 server_default → 这里全部跳过，什么都不做。
    生产 PG 带 26 条 → 逐列清掉。所以三种库跑完都收敛到同一状态。
    """
    inspector_tables = set(inspect(op.get_bind()).get_table_names())

    for table, columns in TARGETS.items():
        if table not in inspector_tables:
            continue
        cols = _columns(table)
        targets = [
            c for c in columns
            if c in cols and cols[c].get("default") is not None
        ]
        if not targets:
            continue
        # SQLite 不支持 ALTER COLUMN，必须 batch（重建表）；PG 下 batch 退化为原生 ALTER。
        with op.batch_alter_table(table, schema=None) as batch_op:
            for c in targets:
                batch_op.alter_column(c, server_default=None)


def downgrade() -> None:
    """空实现 —— 与 0002 同理，这是「向模型收敛」的愈合，不可逆且不需要可逆。

    恢复这些库侧默认值等于把 migrate_to_pg.py 的实现细节重新写回 schema，
    而模型侧的 Python default 已经覆盖了同样的语义 —— 回滚它没有任何收益。

    需要回到基线时用 `alembic downgrade base`：0001 的 downgrade 会 drop_table，
    整表消失，不依赖本函数。
    """
    pass
