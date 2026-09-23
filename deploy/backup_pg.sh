#!/usr/bin/env bash
# backup_pg.sh — PostgreSQL 备份到 OSS，并在本地留一份
#
# cron: 0 3 * * * $ROOT/deploy/backup_pg.sh >> /var/log/traj-backup.log 2>&1
# 切流前 $ROOT=/opt/trajectory-platform；切流后 cron 可继续走旧绝对路径（symlink）。
#
# 用法：
#   backup_pg.sh                          # 日备：trajdb_YYYYMMDD.sql.gz
#   backup_pg.sh --name <filename.sql.gz> # 迁库前：trajdb_pre_<sha12>_<戳>.sql.gz
#
# 密码只从 $ROOT/.env 的 DATABASE_URL 解析（没有再读 backend/.env）。
# 无密码、sqlite、或 oss 上传失败 → 非 0。不 echo 密码，不 set -x。
#
# 2026-09-23：resolve_root / load_pg_from_env 原本是本文件私有的，audit.sh 与
# cleanup_deleted.sh 却各自写死 `PG_USER=trajuser` 且不带密码（撞 peer auth 每天必失败）。
# 两个函数已抽到 deploy/pg_env.sh，三份脚本共用一份解析，避免再次分叉。
set -euo pipefail
# dump 含全库，权限收紧
umask 077

log() { echo "[backup_pg $(date +%Y-%m-%dT%H:%M:%S%z)] $*"; }

die() { log "错误: $*" >&2; exit 1; }

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/pg_env.sh
. "$SELF_DIR/pg_env.sh"

NAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      [[ $# -ge 2 ]] || die "--name 需要文件名"
      NAME="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      die "未知参数: $1"
      ;;
  esac
done

ROOT="$(resolve_root)"
ENVFILE="$ROOT/.env"
OSS_BUCKET="oss://traj-data"
BACKUP_DIR="$ROOT/data/backups"

if [[ -z "$NAME" ]]; then
  NAME="trajdb_$(date +%Y%m%d).sql.gz"
fi
NAME="$(basename "$NAME")"
[[ "$NAME" == *.sql.gz ]] || NAME="${NAME}.sql.gz"

# 先解析凭据：缺密码 / sqlite 应立刻失败，不要先报找不到 ossutil。
# pg_env.sh 的版本返回非 0 而不是自己 die（对账脚本要能降级），这里保持原有的立即失败语义。
load_pg_from_env "$ENVFILE" || die "无法从 $ENVFILE 取得 PG 凭据（见上一行）"

if command -v ossutil64 >/dev/null 2>&1; then
  OSSUTIL=ossutil64
elif command -v ossutil >/dev/null 2>&1; then
  OSSUTIL=ossutil
else
  die "找不到 ossutil64 / ossutil"
fi
command -v pg_dump >/dev/null 2>&1 || die "找不到 pg_dump"

mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/$NAME"

log "开始 PostgreSQL 备份  root=$ROOT  db=$PGDATABASE  file=$NAME  oss=$OSSUTIL"

# 走 TCP（与 DATABASE_URL 的 host 一致），不依赖 peer auth。
# pg_dump 自身失败由 pipefail 捕获（set -euo pipefail）。
pg_dump -U "$PGUSER" -h "$PGHOST" -p "$PGPORT" -d "$PGDATABASE" | gzip > "$DUMP"
# 密码只给 pg_dump 用
unset PGPASSWORD

# 校验产物真的是一份完整备份，而不是「文件存在」就算数。
#
# 2026-09-23：原来只有 `[[ -s "$DUMP" ]]`，但 gzip 对空输入也会写出 20 字节的
# header —— 即 pg_dump 退出码为 0 却没吐出内容时，检查照样通过，脚本打印
# 「备份完成」并把空档上传，覆盖 OSS 上当天的同名对象。备份的失败必须响，
# 不能等到要恢复的那天才发现。
gzip -t "$DUMP" 2>/dev/null || die "dump 不是有效的 gzip（很可能写入中断）: $DUMP"

# 一次解压同时拿到「解压字节数」与「有没有结束标记」。
# 标记不在最后一行（PG 15.15 实测其后还有一行 \unrestrict <token>），所以全量
# 扫描而不是 tail -N —— 尾部结构随 PG 版本变，位置断言会在某次升级后静默失效。
read -r raw_bytes has_marker < <(
  gzip -dc "$DUMP" | awk '
    /PostgreSQL database dump complete/ { m = 1 }
    { n += length($0) + 1 }
    END { printf "%d %d\n", n, m }
  '
) || die "解压校验失败: $DUMP"

[[ "$raw_bytes" -gt 0 ]] || die "dump 解压后为空（pg_dump 未输出内容）: $DUMP"
[[ "$has_marker" == "1" ]] \
  || die "dump 缺少 pg_dump 结束标记，可能被截断（解压 ${raw_bytes} 字节）: $DUMP"

dump_size="$(du -h "$DUMP" | cut -f1)"
log "本地: $DUMP ($dump_size, 解压 ${raw_bytes} 字节)"

"$OSSUTIL" cp "$DUMP" "${OSS_BUCKET}/backups/db/${NAME}"
log "已上传: ${OSS_BUCKET}/backups/db/${NAME}"
log "PostgreSQL 备份完成"
