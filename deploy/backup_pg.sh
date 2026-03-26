#!/bin/bash
# backup_pg.sh — 每日 PostgreSQL 备份到 OSS
# cron: 0 3 * * * /opt/trajectory-platform/deploy/backup_pg.sh >> /var/log/traj-backup.log 2>&1
set -e

DATE=$(date +%Y%m%d)
DUMP="/tmp/trajdb_${DATE}.sql.gz"
OSS_BUCKET="oss://traj-data"
PG_USER="trajuser"
PG_DB="trajdb"

echo "[$(date)] 开始 PostgreSQL 备份..."

# 导出并压缩
pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$DUMP"
dump_size=$(du -h "$DUMP" | cut -f1)
echo "  备份文件: $DUMP ($dump_size)"

# 上传到 OSS
ossutil cp "$DUMP" "${OSS_BUCKET}/backups/db/trajdb_${DATE}.sql.gz"
echo "  已上传到 OSS: backups/db/trajdb_${DATE}.sql.gz"

# 清理本地临时文件
rm -f "$DUMP"

echo "[$(date)] PostgreSQL 备份完成"
