#!/bin/bash
# deploy/migrate.sh — 生产库 schema 演进操作入口（M0/PR-0.1）
#
# 这是**唯一**会碰生产库元数据的脚本。规划要求：执行前先跑一次 deploy/backup_pg.sh。
#
# 用法（在服务器上 /opt/trajectory-platform/backend 执行）：
#   ../deploy/migrate.sh current     # 看当前版本（只读，随便跑）
#   ../deploy/migrate.sh stamp       # 【仅首次】把已有数据的库标记为基线 0001，不执行 DDL
#   ../deploy/migrate.sh plan        # 干跑：打印将要执行的 SQL，不改库
#   ../deploy/migrate.sh upgrade     # 真正执行迁移（会自动先备份）
#   ../deploy/migrate.sh check       # ORM 模型与库是否漂移
#
# 为什么 stamp 和 upgrade 分开、且 stamp 只该跑一次：
# 生产库已有 4 张表和真实数据，0001 里是完整的 create_table —— 直接 upgrade 会失败。
# stamp 的含义是「这个库已经处于基线状态」，只写 alembic_version 行，不动业务表。
#
# ⚠️ 生产 PG 的实际状态（代码盘点结论，尚未在生产核实）：
#    deploy/migrate_to_pg.py 的建表语句里**没有 16 个 AI 评分列**，而补列逻辑
#    `_migrate_sqlite_columns()` 被 `if settings.is_sqlite` 挡住 —— 也就是说生产
#    从来没有过添加这些列的路径。0002 会把缺的列与索引补上，并清掉旧
#    `ADD COLUMN ... DEFAULT` 留下的库侧默认值。
#    首次上线务必按序：stamp → check（看差多少）→ plan（看将执行的 SQL）→ upgrade。

set -euo pipefail

cd "$(dirname "$0")/../backend"
[ -d venv ] && . venv/bin/activate

ACTION="${1:-current}"

backup_first() {
  if [ -x ../deploy/backup_pg.sh ]; then
    echo "==> 执行迁移前备份"
    ../deploy/backup_pg.sh
  else
    echo "!! 找不到可执行的 backup_pg.sh —— 拒绝在无备份的情况下改生产库 schema" >&2
    exit 1
  fi
}

case "$ACTION" in
  current)
    alembic current --verbose
    ;;
  check)
    # 有漂移则非 0 退出。注意：`alembic check` 而不是 grep 生成的迁移文件 ——
    # SQLite 下 render_as_batch=True 会把操作渲染成 `batch_op.add_column`，
    # grep '^\s+op\.' 抓不到，那样写出来的是个假门禁（实测漏报过）。
    # `alembic check` 直接比对 metadata 与库，有差异即非 0 退出。
    alembic check
    ;;
  stamp)
    echo "将把当前库标记为基线 0001（不执行任何 DDL）。"
    alembic current
    read -r -p "确认？只有【首次接管已有数据的库】才该做这一步 [y/N] " ans
    [ "$ans" = "y" ] || { echo "已取消"; exit 1; }
    alembic stamp 0001
    alembic current
    ;;
  plan)
    # 干跑：--sql 让 alembic 只输出 SQL 不连库执行。上线前先看一眼将要做什么。
    echo "==> 待执行的 SQL（不改库）"
    alembic upgrade head --sql
    ;;
  upgrade)
    echo "==> 迁移前版本"; alembic current
    backup_first
    alembic upgrade head
    echo "==> 迁移后版本"; alembic current
    ;;
  *)
    echo "未知操作: ${ACTION}（可用：current / check / stamp / plan / upgrade）" >&2
    exit 1
    ;;
esac
