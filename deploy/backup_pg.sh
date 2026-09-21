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
set -euo pipefail
# dump 含全库，权限收紧
umask 077

log() { echo "[backup_pg $(date +%Y-%m-%dT%H:%M:%S%z)] $*"; }

die() { log "错误: $*" >&2; exit 1; }

# 与 release.sh 同一套根目录规则。未设 AGENT_BACKEND_ROOT 时：
# /opt/agent-backend 存在就用它，否则 /opt/trajectory-platform。
resolve_root() {
  if [[ -n "${AGENT_BACKEND_ROOT:-}" ]]; then
    printf '%s\n' "$AGENT_BACKEND_ROOT"
    return
  fi
  if [[ -n "${TRAJ_REMOTE_BASE_DIR:-}" ]]; then
    printf '%s\n' "$TRAJ_REMOTE_BASE_DIR"
    return
  fi
  if [[ -d /opt/agent-backend ]]; then
    printf '%s\n' /opt/agent-backend
  else
    printf '%s\n' /opt/trajectory-platform
  fi
}

load_pg_from_env() {
  local envfile="$1"
  [[ -f "$envfile" ]] || die "找不到 $envfile"
  local parsed
  parsed="$(python3 - "$envfile" <<'PY'
import shlex
import sys
from urllib.parse import unquote, urlparse

path = sys.argv[1]
url = None
with open(path, encoding="utf-8") as f:
    for raw in f:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != "DATABASE_URL":
            continue
        url = v.strip().strip("'").strip('"')
        break
if not url:
    print("解析失败: 没有 DATABASE_URL", file=sys.stderr)
    sys.exit(2)
if url.startswith("sqlite"):
    print("解析失败: DATABASE_URL 是 sqlite，backup_pg.sh 只支持 PostgreSQL", file=sys.stderr)
    sys.exit(2)
for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgres+asyncpg://"):
    if url.startswith(prefix):
        url = "postgresql://" + url[len(prefix):]
        break
parsed = urlparse(url)
if parsed.scheme not in ("postgresql", "postgres"):
    print(f"解析失败: 不支持的协议 {parsed.scheme!r}", file=sys.stderr)
    sys.exit(2)
user = unquote(parsed.username or "")
password = unquote(parsed.password or "")
host = parsed.hostname or "localhost"
port = parsed.port or 5432
db = (parsed.path or "").lstrip("/") or "trajdb"
if not user:
    print("解析失败: DATABASE_URL 没有用户名", file=sys.stderr)
    sys.exit(2)
if not password:
    print("解析失败: DATABASE_URL 没有密码", file=sys.stderr)
    sys.exit(2)
# 逐行 export，shlex.quote 保证密码里的 $ ` " 不会被二次展开
print(f"export PGUSER={shlex.quote(user)}")
print(f"export PGPASSWORD={shlex.quote(password)}")
print(f"export PGHOST={shlex.quote(host)}")
print(f"export PGPORT={shlex.quote(str(port))}")
print(f"export PGDATABASE={shlex.quote(db)}")
PY
)" || die "无法从 $envfile 解析 DATABASE_URL"
  eval "$parsed"
  [[ -n "${PGPASSWORD:-}" ]] || die "DATABASE_URL 没有密码"
}

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
load_pg_from_env "$ENVFILE"

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

# 走 TCP（与 DATABASE_URL 的 host 一致），不依赖 peer auth
pg_dump -U "$PGUSER" -h "$PGHOST" -p "$PGPORT" -d "$PGDATABASE" | gzip > "$DUMP"
# 密码只给 pg_dump 用
unset PGPASSWORD
[[ -s "$DUMP" ]] || die "dump 文件为空: $DUMP"
dump_size="$(du -h "$DUMP" | cut -f1)"
log "本地: $DUMP ($dump_size)"

"$OSSUTIL" cp "$DUMP" "${OSS_BUCKET}/backups/db/${NAME}"
log "已上传: ${OSS_BUCKET}/backups/db/${NAME}"
log "PostgreSQL 备份完成"
