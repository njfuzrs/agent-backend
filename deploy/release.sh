#!/usr/bin/env bash
# release.sh — 生产切换的唯一入口（在 ECS 上执行）
#
# 用法：release.sh <sha> [--source /tmp/agent-backend-release-<sha>]
# 退出码：0 成功；非 0 失败（调用方 GitHub job / push_code.sh 失败）
#
# 硬约束（合入 main 自动部署设计 §6.1 / 生产目录文 §9）：
#   - 不 mv /opt、不改 nginx、不覆盖 /etc/systemd/system/*.service
#   - 不读、不传、不改业务凭据；rsync 排除 .env
#   - $ROOT 读 AGENT_BACKEND_ROOT（未设：/opt/agent-backend 存在则用之，否则 /opt/trajectory-platform）
#   - systemctl 优先 agent-backend，不存在再 trajectory-platform
set -euo pipefail

log() { echo "[release $(date +%Y-%m-%dT%H:%M:%S%z)] $*"; }
die() { log "错误: $*" >&2; exit 1; }

usage() {
  echo "用法: release.sh <sha> [--source /tmp/agent-backend-release-<sha>]" >&2
  exit 2
}

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

# 优先新 unit；切流前线上只有 trajectory-platform。
resolve_unit() {
  if systemctl cat agent-backend.service >/dev/null 2>&1; then
    printf '%s\n' agent-backend
  else
    printf '%s\n' trajectory-platform
  fi
}

# 不 import 应用：SOURCE 没有 .env，env.py 会因为缺 AUTH_PASSWORD 直接 SystemExit。
source_alembic_heads() {
  local backend="$1"
  python3 - "$backend" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
versions = root / "migrations" / "versions"
if not versions.is_dir():
    sys.exit("没有 migrations/versions")
# 同时吃 revision = "0006" 和 revision: str = "0006"
rev_re = re.compile(r"^revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)", re.M)
down_re = re.compile(r"^down_revision(?:\s*:\s*[^=]+)?\s*=\s*(.+)$", re.M)
revs = []
downs = set()
for p in versions.glob("*.py"):
    text = p.read_text(encoding="utf-8")
    m = rev_re.search(text)
    if not m:
        continue
    rev = m.group(1)
    revs.append(rev)
    d = down_re.search(text)
    if not d:
        continue
    val = d.group(1).strip()
    if val in ("None", "none"):
        continue
    mm = re.search(r"['\"]([^'\"]+)['\"]", val)
    if mm:
        downs.add(mm.group(1))
heads = [r for r in revs if r not in downs]
if not heads:
    sys.exit("解析不到 alembic head")
print("\n".join(heads))
PY
}

live_alembic_current() {
  local backend="$1"
  # INFO 在 stderr/stdout 混排；revision 行形如「0006 (head)」
  (cd "$backend" && alembic current 2>&1) \
    | awk '/^[0-9a-fA-F]/ {print $1}' \
    | tail -1
}

[[ $# -ge 1 ]] || usage
SHA="$1"
shift
[[ -n "$SHA" && "$SHA" != -* ]] || usage

SOURCE="/tmp/agent-backend-release-${SHA}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      [[ $# -ge 2 ]] || die "--source 需要目录"
      SOURCE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      die "未知参数: $1"
      ;;
  esac
done

ROOT="$(resolve_root)"
UNIT="$(resolve_unit)"
SELF="$(cd "$(dirname "$0")" && pwd)"

log "SHA=$SHA"
log "SOURCE=$SOURCE"
log "ROOT=$ROOT"
log "UNIT=$UNIT"

[[ -f "$SOURCE/backend/app/main.py" ]] || die "缺 $SOURCE/backend/app/main.py"
[[ -f "$SOURCE/frontend/dist/index.html" ]] || die "缺 $SOURCE/frontend/dist/index.html"
[[ -d "$SOURCE/backend/migrations/versions" ]] || die "缺 $SOURCE/backend/migrations/versions"
grep -q '/traj/' "$SOURCE/frontend/dist/index.html" \
  || die "frontend/dist/index.html 不含 /traj/（Vite base 被改了？拒绝发布）"
[[ -f "$ROOT/.env" ]] || die "缺 $ROOT/.env（CD 不写凭据，拒绝继续）"
[[ -f "$ROOT/backend/.env" ]] || die "缺 $ROOT/backend/.env（CD 不写凭据，拒绝继续）"
[[ -d "$ROOT/backend" ]] || die "缺 $ROOT/backend"

# 失败时把当时那个 unit 拉起来，不要停在 stop 与 start 之间。
# start 对已在跑的服务是空操作。
trap 'rc=$?; if [[ $rc -ne 0 ]]; then
  echo "[release] 失败 (exit $rc)，systemctl start '"$UNIT"'" >&2
  systemctl start '"$UNIT"' || true
fi' EXIT

HEADS="$(source_alembic_heads "$SOURCE/backend")"
HEAD_COUNT="$(printf '%s\n' "$HEADS" | grep -c . || true)"
[[ "$HEAD_COUNT" == "1" ]] || die "SOURCE alembic 不是单一 head: $HEADS"
NEW_HEAD="$(printf '%s\n' "$HEADS" | head -1)"
LIVE_REV="$(live_alembic_current "$ROOT/backend")"
[[ -n "$LIVE_REV" ]] || die "读不到线上 alembic current"

NEED_MIGRATE=0
if [[ "$LIVE_REV" != "$NEW_HEAD" ]]; then
  NEED_MIGRATE=1
fi
log "alembic live=$LIVE_REV  new_head=$NEW_HEAD  migrate=$NEED_MIGRATE"

if [[ "$NEED_MIGRATE" == "1" ]]; then
  log "有迁移：先停服务再备份、再切代码"
  systemctl stop "$UNIT"
  SHA12="$(printf '%s' "$SHA" | cut -c1-12)"
  STAMP="$(date +%Y%m%d-%H%M%S)"
  # 调 SOURCE 里修好的备份脚本（$ROOT/deploy 可能还是旧的 ossutil 版）
  AGENT_BACKEND_ROOT="$ROOT" bash "$SELF/backup_pg.sh" \
    --name "trajdb_pre_${SHA12}_${STAMP}.sql.gz"
fi

rsync_app() {
  # --delete 只打白名单目录，避免扫掉 data/ 或 .env
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude '.env.bak-*' \
    --exclude 'venv' \
    --exclude '.ruff_cache' \
    "$SOURCE/backend/app/" "$ROOT/backend/app/"
  rsync -a --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$SOURCE/backend/migrations/" "$ROOT/backend/migrations/"
  rsync -a --delete \
    "$SOURCE/frontend/dist/" "$ROOT/frontend/dist/"
  # 单文件覆盖（不 --delete 整个 backend/）
  for f in requirements.txt alembic.ini pyproject.toml; do
    if [[ -f "$SOURCE/backend/$f" ]]; then
      cp -a "$SOURCE/backend/$f" "$ROOT/backend/$f"
    fi
  done
  # deploy/ 整目录同步，但不 --delete，也不把仓内 unit 拷到 /etc
  mkdir -p "$ROOT/deploy"
  # 仓内 unit 只当参考，rsync 进 $ROOT/deploy 无妨；禁止 copy 到 /etc。
  rsync -a "$SOURCE/deploy/" "$ROOT/deploy/"
}

log "同步代码 → $ROOT"
rsync_app

log "清理 __pycache__"
find "$ROOT/backend" -type d -name '__pycache__' -prune -exec rm -rf {} +

log "chmod +x deploy 脚本"
for s in release backup_pg audit cleanup_deleted migrate push_code rollback; do
  f="$ROOT/deploy/${s}.sh"
  if [[ -f "$f" ]]; then
    chmod +x "$f"
  fi
done

log "pip3 install -r requirements.txt"
pip3 install -q -r "$ROOT/backend/requirements.txt"

if [[ "$NEED_MIGRATE" == "1" ]]; then
  log "alembic upgrade head（cwd=$ROOT/backend）"
  (
    cd "$ROOT/backend"
    alembic current
    alembic upgrade head
    alembic current
  )
  log "启动 $UNIT"
  systemctl start "$UNIT"
else
  log "无迁移：systemctl restart $UNIT"
  systemctl restart "$UNIT"
fi

log "health 循环 10×2s → :8900"
ok=0
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8900/api/v1/health >/dev/null; then
    log "health 直连 ok (attempt $i)"
    ok=1
    break
  fi
  sleep 2
done
[[ "$ok" == "1" ]] || die "http://127.0.0.1:8900/api/v1/health 连续失败"

# 2026-09-22 起 IP/未知 Host 的 :80 是整块 410。本机 curl 不带 Host
# 会走 default_server，再打 http://127.0.0.1/traj/ 就是 410（#20 合入后
# 第一次 Deploy 在这里红）。反代是否还在，问 HTTPS 域名块。
log "health nginx /traj/api（HTTPS 本机 Host）"
curl -fsS --resolve www.sid-code.cc:443:127.0.0.1 \
  https://www.sid-code.cc/traj/api/v1/health >/dev/null \
  || die "https://www.sid-code.cc/traj/api/v1/health 本机反代失败"

if [[ -f "$ROOT/.deploy-sha" ]]; then
  cp -a "$ROOT/.deploy-sha" "$ROOT/.deploy-sha.prev"
fi
printf '%s\n' "$SHA" > "$ROOT/.deploy-sha"
date -Iseconds > "$ROOT/.deploy-time"
log "已写 $ROOT/.deploy-sha = $SHA"
log "完成"
