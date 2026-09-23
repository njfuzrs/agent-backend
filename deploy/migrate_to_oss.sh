#!/usr/bin/env bash
# migrate_to_oss.sh — 将本地 session 文件迁移到 OSS
#
# ⚠️ 一次性脚本，2026-03 已在生产执行完毕（现行 STORAGE_BACKEND=oss，数据在上传时
#    直接入 OSS）。保留仅为历史参考与灾难重建，**日常不要跑**。重跑会把 oss_key
#    为 NULL 的行按约定路径回填，对已经正常的库是无操作，但没有必要。
#
# 2026-09-23 一并修掉三处会让它「跑一半」的问题（当时没炸是因为它再没被跑过）：
#   1) psql 写死 `-U trajuser` 且不带密码 → 撞 pg_hba 的 `local all all peer` 必失败；
#      改为与 backup_pg / audit / cleanup 同一套 deploy/pg_env.sh 凭据解析，走 TCP。
#   2) 目录写死 /opt/trajectory-platform（2026-09-23 已切流）→ 改为 $ROOT 推导。
#   3) 调的是 `ossutil`，但机器上的二进制是 `ossutil64` → 改为自动探测。
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/pg_env.sh
. "$SELF_DIR/pg_env.sh"

die() { echo "错误: $*" >&2; exit 1; }

ROOT="$(resolve_root)"
SESSIONS_DIR="$ROOT/data/sessions"
OSS_BUCKET="oss://traj-data"

if command -v ossutil64 >/dev/null 2>&1; then
  OSSUTIL=ossutil64
elif command -v ossutil >/dev/null 2>&1; then
  OSSUTIL=ossutil
else
  die "找不到 ossutil64 / ossutil"
fi

load_pg_from_env "$ROOT/.env" || die "无法取得 PG 凭据（见上一行）"
trap 'unset PGPASSWORD' EXIT

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
"$OSSUTIL" cp -r "$SESSIONS_DIR/" "${OSS_BUCKET}/sessions/" \
    --jobs 10 --parallel 5 --update

echo ""
echo "=== 验证文件数量 ==="
oss_count=$("$OSSUTIL" ls "${OSS_BUCKET}/sessions/" -s 2>/dev/null | grep -c "session.traj" || true)
echo "本地 session.traj: $local_count"
echo "OSS session.traj:  $oss_count"

if [ "$local_count" -eq "$oss_count" ]; then
    echo "数量一致 ✓"
else
    echo "警告: 数量不一致！请检查上传日志"
fi

echo ""
echo "=== 更新 PG 记录的 oss_key 字段 ==="
psql_q "UPDATE trajectories
        SET oss_key = 'sessions/' || session_id || '/session.traj'
        WHERE oss_key IS NULL;" || die "回填 oss_key 失败"

updated=$(psql_q "SELECT COUNT(*) FROM trajectories WHERE oss_key IS NOT NULL;") \
  || die "查询 oss_key 计数失败"
echo "已更新 oss_key 的记录数: $updated"

echo ""
echo "=== 迁移完成 ==="
echo "注意: 本地文件暂时保留，确认 OSS 数据完整后可手动清理"
