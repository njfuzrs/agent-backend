#!/usr/bin/env bash
# pg_env.sh — 被 source 的公共库：解析 $ROOT 与 PG 连接凭据。
#
# 为什么存在：backup_pg.sh 早先自带一份 DATABASE_URL 解析，audit.sh /
# cleanup_deleted.sh 却写死 `PG_USER=trajuser` 且不带密码，于是它们的 psql
# 走 unix socket 撞 pg_hba 的 `local all all peer`，每天必然 FATAL。
# 三份脚本各写一遍凭据解析 = 三个月后又分叉，所以抽到这里，谁都 source 它。
#
# 用法（在 deploy/ 下的脚本里）：
#   SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   . "$SELF/pg_env.sh"
#   ROOT="$(resolve_root)"
#   load_pg_from_env "$ROOT/.env"     # 导出 PGUSER/PGPASSWORD/PGHOST/PGPORT/PGDATABASE
#   psql_q "select 1"                 # 走 TCP，不依赖 peer auth
#   unset PGPASSWORD                  # 用完即清
#
# 约定：
#   - 不 echo 密码，不 set -x。
#   - 只读 $ROOT/.env（systemd EnvironmentFile 那一份），不读 backend/.env。
#     两处键名相同，根级是进程实际生效的那份（OS env 优先于 pydantic env_file）。
#   - 连接一律走 DATABASE_URL 里的 host（localhost → TCP 127.0.0.1，命中
#     pg_hba 的 `host all all 127.0.0.1/32 scram-sha-256`）。

# 与 release.sh / rollback.sh / backup_pg.sh 同一套根目录规则。
# 未设 AGENT_BACKEND_ROOT 时：/opt/agent-backend 存在就用它，否则 /opt/trajectory-platform。
# （2026-09-23 切流后前者是真实目录，后者是指向它的 symlink。）
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

# 从 .env 的 DATABASE_URL 解析并 export PG* 环境变量。
# 缺文件 / 缺 DATABASE_URL / sqlite / 缺密码 → 返回非 0（调用方自己决定 die 还是降级）。
load_pg_from_env() {
  local envfile="$1"
  if [[ ! -f "$envfile" ]]; then
    echo "pg_env: 找不到 $envfile" >&2
    return 1
  fi
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
    print("解析失败: DATABASE_URL 是 sqlite，这些脚本只支持 PostgreSQL", file=sys.stderr)
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
)" || {
    echo "pg_env: 无法从 $envfile 解析 DATABASE_URL" >&2
    return 1
  }
  eval "$parsed"
  if [[ -z "${PGPASSWORD:-}" ]]; then
    echo "pg_env: DATABASE_URL 没有密码" >&2
    return 1
  fi
  return 0
}

# 单值查询（-tA：无表头、无对齐）。显式带 -h/-p/-U 走 TCP，绕开 peer auth。
# 失败时返回非 0 并把 psql 的 stderr 透出来——调用方不要把它塞进管道，
# 否则 $? 变成管道末端的退出码，失败会被吞掉（这正是 audit.sh 原来的 bug）。
psql_q() {
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tAc "$1"
}

# 带命名参数的查询/写入：值通过 psql 变量传入，用 :'name' 引用。
# psql 会正确加引号转义，避免把 session_id 之类的外部输入拼进 SQL。
#   psql_v "delete from t where id = :'sid'" sid "$s"
psql_v() {
  local sql="$1"; shift
  local -a args=()
  while [[ $# -ge 2 ]]; do
    args+=(-v "$1=$2")
    shift 2
  done
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -tA "${args[@]}" -c "$sql"
}
