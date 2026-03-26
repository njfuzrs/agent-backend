#!/bin/bash
# migrate_to_oss.sh — 将本地 session 文件迁移到 OSS
# 在服务器上执行，需要先安装并配置 ossutil
set -e

SESSIONS_DIR="/opt/trajectory-platform/data/sessions"
OSS_BUCKET="oss://traj-data"
PG_USER="trajuser"
PG_DB="trajdb"

echo "=== 迁移 session 文件到 OSS ==="
echo "源目录: $SESSIONS_DIR"
echo "目标: $OSS_BUCKET/sessions/"

if [ ! -d "$SESSIONS_DIR" ]; then
    echo "错误: 目录不存在 $SESSIONS_DIR"
    exit 1
fi

local_count=$(find "$SESSIONS_DIR" -name "session.traj" 2>/dev/null | wc -l)
echo "本地 session.traj 文件数: $local_count"

if [ "$local_count" -eq 0 ]; then
    echo "没有文件需要迁移"
    exit 0
fi

# 上传到 OSS（增量模式，跳过已存在的文件）
ossutil cp -r "$SESSIONS_DIR/" "${OSS_BUCKET}/sessions/" \
    --jobs 10 --parallel 5 --update

echo ""
echo "=== 验证文件数量 ==="
oss_count=$(ossutil ls "${OSS_BUCKET}/sessions/" -s 2>/dev/null | grep "session.traj" | wc -l)
echo "本地 session.traj: $local_count"
echo "OSS session.traj:  $oss_count"

if [ "$local_count" -eq "$oss_count" ]; then
    echo "数量一致 ✓"
else
    echo "警告: 数量不一致！请检查上传日志"
fi

echo ""
echo "=== 更新 PG 记录的 oss_key 字段 ==="
psql -U "$PG_USER" -d "$PG_DB" -c "
    UPDATE trajectories
    SET oss_key = 'sessions/' || session_id || '/session.traj'
    WHERE oss_key IS NULL;
"

updated=$(psql -U "$PG_USER" -d "$PG_DB" -t -c "
    SELECT COUNT(*) FROM trajectories WHERE oss_key IS NOT NULL;
")
echo "已更新 oss_key 的记录数: $updated"

echo ""
echo "=== 迁移完成 ==="
echo "注意: 本地文件暂时保留，确认 OSS 数据完整后可手动清理"
