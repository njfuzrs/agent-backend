#!/bin/bash
# audit.sh — 每日 DB vs OSS 对账
# cron: 0 5 * * * /opt/trajectory-platform/deploy/audit.sh >> /var/log/traj-audit.log 2>&1
set -e

OSS_BUCKET="oss://traj-data"
PG_USER="trajuser"
PG_DB="trajdb"

echo "[对账 $(date +%Y-%m-%d)]"

# DB 中未删除的记录数
db_count=$(psql -U "$PG_USER" -d "$PG_DB" -t -c \
    "SELECT COUNT(*) FROM trajectories WHERE deleted_at IS NULL;" | tr -d ' ')

# OSS 中的 session.traj 文件数
oss_count=$(ossutil ls "${OSS_BUCKET}/sessions/" -s 2>/dev/null \
    | grep "session.traj" | grep -v ".gz" -c 2>/dev/null || echo "0")
oss_gz_count=$(ossutil ls "${OSS_BUCKET}/sessions/" -s 2>/dev/null \
    | grep "session.traj.gz" -c 2>/dev/null || echo "0")
oss_total=$((oss_count + oss_gz_count))

echo "  DB 有效记录:        $db_count"
echo "  OSS traj 文件:      $oss_total (未压缩: $oss_count, 压缩: $oss_gz_count)"

if [ "$db_count" != "$oss_total" ]; then
    echo "  [警告] 数量不一致！差异: $((db_count - oss_total))"
else
    echo "  数量一致 ✓"
fi

# 软删除待清理数
pending_cleanup=$(psql -U "$PG_USER" -d "$PG_DB" -t -c \
    "SELECT COUNT(*) FROM trajectories WHERE deleted_at IS NOT NULL;" | tr -d ' ')
echo "  软删除待清理:       $pending_cleanup"
