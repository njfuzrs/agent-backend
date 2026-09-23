#!/usr/bin/env bash
# audit.sh — 每日 DB vs OSS 对账（只读，不写 DB、不删 OSS）
#
# cron: 0 5 * * * $ROOT/deploy/audit.sh >> /var/log/traj-audit.log 2>&1
# 切流后 cron 可继续走旧绝对路径 /opt/trajectory-platform/deploy/audit.sh（symlink）。
#
# 2026-09-23 修：
#   1) 原来 `psql -U trajuser` 不带密码走 unix socket，撞 pg_hba 的
#      `local all all peer`，每天必然 FATAL；且 psql 被塞进 `| tr` 管道，
#      $? 取的是 tr 的 0，失败被吞掉 → 打印空数字并继续，最后报出
#      `差异: -9574` 这种假警报。现在凭据从 $ROOT/.env 的 DATABASE_URL 解析、
#      走 TCP，且查询失败立即非 0 退出。
#   2) 口径修正：原来拿「未软删的行数」去比「全部 OSS 文件数」。软删记录的
#      OSS 文件要等 cleanup_deleted.sh 满 30 天才删，在窗口内必然存在，
#      两个数天然对不上。现在用 DB 全部行数（含软删）对 OSS 去重 session 数，
#      并把孤儿/缺失分别列出来——这才是对账要回答的问题。
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SELF/pg_env.sh"

OSS_BUCKET="oss://traj-data"

log() { echo "  $*"; }
die() { echo "  [错误] $*" >&2; exit 1; }

ROOT="$(resolve_root)"
load_pg_from_env "$ROOT/.env" || die "无法取得 PG 凭据（见上一行）"

if command -v ossutil64 >/dev/null 2>&1; then
  OSSUTIL=ossutil64
elif command -v ossutil >/dev/null 2>&1; then
  OSSUTIL=ossutil
else
  die "找不到 ossutil64 / ossutil"
fi

echo "[对账 $(date +%Y-%m-%dT%H:%M:%S%z)] root=$ROOT db=$PGDATABASE oss=$OSSUTIL"

# --- DB 侧 ---
db_live="$(psql_q 'select count(*) from trajectories where deleted_at is null;')" \
  || die "查询 DB 失败（未删除计数）"
db_deleted="$(psql_q 'select count(*) from trajectories where deleted_at is not null;')" \
  || die "查询 DB 失败（软删计数）"
db_total="$(psql_q 'select count(*) from trajectories;')" \
  || die "查询 DB 失败（总计）"

# --- OSS 侧 ---
# 一次列举，之后都在本地算，避免对 OSS 多次全量扫描。
OSS_LIST="$(mktemp)"; DB_SIDS="$(mktemp)"; OSS_SIDS="$(mktemp)"
trap 'rm -f "$OSS_LIST" "$DB_SIDS" "$OSS_SIDS"; unset PGPASSWORD' EXIT

"$OSSUTIL" ls "${OSS_BUCKET}/sessions/" -s > "$OSS_LIST" 2>/dev/null \
  || die "ossutil 列举失败"

oss_plain="$(grep -cE 'sessions/[^/]+/session\.traj$'    "$OSS_LIST" || true)"
oss_gz="$(  grep -cE 'sessions/[^/]+/session\.traj\.gz$' "$OSS_LIST" || true)"

# 同一 session 可能同时有 .traj 与 .traj.gz（迁移遗留），按 session 去重才可比。
grep -oE 'sessions/[^/]+/session\.traj(\.gz)?$' "$OSS_LIST" \
  | sed -E 's#sessions/([^/]+)/.*#\1#' | sort -u > "$OSS_SIDS"
oss_sessions="$(wc -l < "$OSS_SIDS" | tr -d ' ')"

psql_q 'select session_id from trajectories;' | sort -u > "$DB_SIDS" \
  || die "查询 DB 失败（session_id 列表）"

orphan_count="$(comm -23 "$OSS_SIDS" "$DB_SIDS" | wc -l | tr -d ' ')"   # OSS 有、DB 无
missing_count="$(comm -13 "$OSS_SIDS" "$DB_SIDS" | wc -l | tr -d ' ')"  # DB 有、OSS 无

log "DB 记录:            总计 ${db_total}（有效 $db_live / 软删待清 ${db_deleted}）"
log "OSS session:        ${oss_sessions}（文件 未压缩 $oss_plain / 压缩 ${oss_gz}）"

# 判据：DB 全部行（含软删，其文件此时仍在 OSS）应与 OSS 去重 session 一一对应。
if [[ "$db_total" == "$oss_sessions" && "$orphan_count" == "0" && "$missing_count" == "0" ]]; then
  log "数量一致 ✓"
else
  log "[警告] 对账不一致：DB 总计 $db_total vs OSS session ${oss_sessions}（差 $((db_total - oss_sessions))）"
  if [[ "$orphan_count" != "0" ]]; then
    log "  仅在 OSS（$orphan_count 个，DB 无行；多为上传解析失败或历史测试残留，可人工核后删）："
    comm -23 "$OSS_SIDS" "$DB_SIDS" | head -20 | sed 's/^/      /'
    [[ "$orphan_count" -gt 20 ]] && log "      …… 其余 $((orphan_count - 20)) 个略"
  fi
  if [[ "$missing_count" != "0" ]]; then
    log "  仅在 DB（$missing_count 个，OSS 无文件；这类更要紧，详情页会取不到内容）："
    comm -13 "$OSS_SIDS" "$DB_SIDS" | head -20 | sed 's/^/      /'
    [[ "$missing_count" -gt 20 ]] && log "      …… 其余 $((missing_count - 20)) 个略"
  fi
fi

# 满 30 天、下次 cleanup_deleted.sh 会真删的条数（时间戳比较，不是文本比较）
due="$(psql_q "select count(*) from trajectories
               where deleted_at is not null
                 and deleted_at::timestamptz < NOW() - INTERVAL '30 days';")" \
  || die "查询 DB 失败（待清理计数）"
log "软删待清理:         ${db_deleted}（其中已满 30 天、下次 cleanup 会真删: ${due}）"
