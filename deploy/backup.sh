#!/usr/bin/env bash
# deploy/backup.sh — ⚠️ 已废弃（SQLite + 本地 traj_files 时代的备份脚本）
#
# 现行备份是 deploy/backup_pg.sh（PostgreSQL → OSS，cron 0 3 * * *）。
# 本脚本假设的两样东西现在都不成立：
#   - data/trajectories.db 只是 2026-03 迁库前的历史文件，不是运行时数据源；
#   - data/traj_files/ 在 STORAGE_BACKEND=oss 下根本不存在（轨迹在 OSS）。
# 跑它只会产出一份「看起来有备份」的无效产物，比不备份更危险，所以直接拦住。
# 保留文件仅为历史参考；要删请单独开 PR，不要在别的改动里顺手删。
set -euo pipefail

echo "backup.sh 已废弃：现行 PostgreSQL 备份请用 deploy/backup_pg.sh" >&2
echo "（本脚本面向 SQLite + 本地 traj_files，当前 STORAGE_BACKEND=oss 下产物无效）" >&2
exit 1

# ---- 以下为历史实现，不再执行 ----
BACKUP_DIR="/opt/trajectory-platform/backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# 备份 SQLite（在线安全备份）
echo "备份数据库..."
sqlite3 /opt/trajectory-platform/data/trajectories.db ".backup '$BACKUP_DIR/trajectories_$DATE.db'"

# 增量备份 .traj 文件
LAST_BACKUP_MARKER="$BACKUP_DIR/.last_traj_backup"
if [ -f "$LAST_BACKUP_MARKER" ]; then
    INCR_FILES=$(find /opt/trajectory-platform/data/traj_files/ -name '*.traj' -newer "$LAST_BACKUP_MARKER" -print)
    if [ -n "$INCR_FILES" ]; then
        echo "$INCR_FILES" | tar czf "$BACKUP_DIR/traj_files_incr_$DATE.tar.gz" -T -
        echo "增量备份完成"
    else
        echo "无新增文件，跳过增量备份"
    fi
else
    echo "首次全量备份..."
    tar czf "$BACKUP_DIR/traj_files_full_$DATE.tar.gz" \
        -C /opt/trajectory-platform/data traj_files/
fi
touch "$LAST_BACKUP_MARKER"

# 保留最近 7 天的备份
find "$BACKUP_DIR" -name "*.db" -mtime +7 -delete
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +7 -delete

echo "备份完成: $DATE"
