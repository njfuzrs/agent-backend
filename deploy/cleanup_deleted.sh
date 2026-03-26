#!/bin/bash
# cleanup_deleted.sh — 清理软删除超过 30 天的记录和 OSS 文件
# cron: 0 6 * * * /opt/trajectory-platform/deploy/cleanup_deleted.sh >> /var/log/traj-cleanup.log 2>&1
set -e

OSS_BUCKET="oss://traj-data"
PG_USER="trajuser"
PG_DB="trajdb"

echo "[清理 $(date +%Y-%m-%d)]"

# 查询软删除超过 30 天的 session_id
SESSIONS=$(psql -U "$PG_USER" -d "$PG_DB" -t -A -c "
    SELECT session_id FROM trajectories
    WHERE deleted_at IS NOT NULL
    AND deleted_at < (NOW() - INTERVAL '30 days')::text;
")

if [ -z "$SESSIONS" ]; then
    echo "  没有需要清理的记录"
    exit 0
fi

count=0
for sid in $SESSIONS; do
    echo "  清理: $sid"

    # 删除 OSS 文件（整个 session 目录）
    ossutil rm "${OSS_BUCKET}/sessions/${sid}/" -r -f 2>/dev/null || true

    # 删除 DB 记录
    psql -U "$PG_USER" -d "$PG_DB" -c \
        "DELETE FROM trajectories WHERE session_id = '${sid}';" 2>/dev/null || true

    count=$((count + 1))
done

echo "  共清理 $count 条记录"
