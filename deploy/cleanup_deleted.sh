#!/usr/bin/env bash
# cleanup_deleted.sh — 真删软删除满 30 天的记录与其 OSS 文件
#
# cron: 0 6 * * * $ROOT/deploy/cleanup_deleted.sh >> /var/log/traj-cleanup.log 2>&1
# 切流后 cron 可继续走旧绝对路径 /opt/trajectory-platform/deploy/cleanup_deleted.sh（symlink）。
#
# 用法：
#   cleanup_deleted.sh              # 真删
#   cleanup_deleted.sh --dry-run    # 只列出会删什么，不动 DB / OSS
#   cleanup_deleted.sh --days 60    # 改保留期（默认 30）
#
# 2026-09-23 修（三个缺陷，此脚本此前从未成功跑完过一次）：
#   1) `psql -U trajuser` 不带密码走 unix socket，撞 pg_hba 的 `local all all peer`
#      必然 FATAL；加上 `set -e` 且该 psql 不在管道里，脚本在第一条查询就中止。
#      → 凭据改从 $ROOT/.env 的 DATABASE_URL 解析并走 TCP。
#   2) deleted_at 是 TEXT 存 ISO8601（形如 2026-08-24T02:00:00+00:00），原来与
#      `(NOW() - INTERVAL '30 days')::text`（形如 2026-08-24 10:31:25+08）做
#      **文本**比较：ISO 的 'T'(0x54) > 空格(0x20)，所以「满 30 天那一天」的记录
#      文本上永远更大 → 永不被选中；跨时区渲染也对不上。
#      → 改为 deleted_at::timestamptz < NOW() - INTERVAL 'N days' 的时间戳比较。
#   3) session_id 来自上传端（外部输入），原来直接字符串拼进 DELETE 语句。
#      → 改用 psql 变量 :'sid'，由 psql 负责加引号转义。
#
# 安全约定：先删 OSS 文件再删 DB 行。反过来的话 DB 行没了就再也找不到该删哪些
# 对象，只能留成永久孤儿。OSS 删失败则跳过该条、保留 DB 行，下次再试。
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SELF/pg_env.sh"

OSS_BUCKET="oss://traj-data"
RETAIN_DAYS=30
DRY_RUN=0

log() { echo "  $*"; }
die() { echo "  [错误] $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --days)
      [[ $# -ge 2 ]] || die "--days 需要天数"
      [[ "$2" =~ ^[0-9]+$ ]] || die "--days 必须是整数: $2"
      RETAIN_DAYS="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

ROOT="$(resolve_root)"
load_pg_from_env "$ROOT/.env" || die "无法取得 PG 凭据（见上一行）"
trap 'unset PGPASSWORD' EXIT

if command -v ossutil64 >/dev/null 2>&1; then
  OSSUTIL=ossutil64
elif command -v ossutil >/dev/null 2>&1; then
  OSSUTIL=ossutil
else
  die "找不到 ossutil64 / ossutil"
fi

echo "[清理 $(date +%Y-%m-%dT%H:%M:%S%z)] root=$ROOT db=$PGDATABASE 保留=${RETAIN_DAYS}天 dry_run=$DRY_RUN"

# 时间戳比较（不是文本比较）。-tA 输出裸值，一行一个 session_id。
SESSIONS="$(psql_q "select session_id from trajectories
                    where deleted_at is not null
                      and deleted_at::timestamptz < NOW() - INTERVAL '${RETAIN_DAYS} days'
                    order by deleted_at;")" \
  || die "查询待清理列表失败"

if [[ -z "${SESSIONS//[$'\n\r\t ']/}" ]]; then
  log "没有需要清理的记录（软删满 ${RETAIN_DAYS} 天的为 0）"
  exit 0
fi

total="$(printf '%s\n' "$SESSIONS" | grep -c . || true)"
log "待清理 $total 条："

ok=0; skipped=0
while IFS= read -r sid; do
  [[ -n "$sid" ]] || continue

  if [[ "$DRY_RUN" == "1" ]]; then
    log "  [dry-run] 会删 OSS ${OSS_BUCKET}/sessions/${sid}/ 与 DB 行 $sid"
    ok=$((ok + 1))
    continue
  fi

  # 先 OSS。删失败就保留 DB 行，下次再试，不让对象变成找不回来的孤儿。
  if ! "$OSSUTIL" rm "${OSS_BUCKET}/sessions/${sid}/" -r -f >/dev/null 2>&1; then
    log "  [跳过] OSS 删除失败，保留 DB 行待下次重试: $sid"
    skipped=$((skipped + 1))
    continue
  fi

  # session_id 走 psql 变量，不拼字符串
  if ! psql_v "delete from trajectories where session_id = :'sid';" sid "$sid" >/dev/null; then
    log "  [跳过] DB 删除失败（OSS 文件已删，下次将作为孤儿被 audit 报出）: $sid"
    skipped=$((skipped + 1))
    continue
  fi

  log "  已清理: $sid"
  ok=$((ok + 1))
done <<< "$SESSIONS"

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run 结束：会清理 $ok 条，未改动任何数据"
else
  log "共清理 $ok 条，跳过 $skipped 条"
fi
[[ "$skipped" == "0" ]] || exit 1
