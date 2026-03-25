#!/bin/bash
# deploy/backup.sh — 数据备份脚本

set -e

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
