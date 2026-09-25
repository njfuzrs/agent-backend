#!/usr/bin/env bash
# release.sh — 生产切换的唯一入口（在 ECS 上执行）
#
# 用法：release.sh <sha> [--source /tmp/agent-backend-release-<sha>]
# 退出码：0 成功；非 0 失败（调用方 GitHub job / push_code.sh 失败）
#
# 硬约束（合入 main 自动部署设计 §6.1 / 生产目录文 §9）：
#   - 不 mv /opt、不改 nginx
#   - unit 只按白名单补三处日志配置（见 ensure_unit），其余一个字节不动：
#     路径、ExecStart 解释器、凭据文件都不属于发版脚本
#   - 不读、不传、不改业务凭据；rsync 排除 .env
#   - $ROOT 读 AGENT_BACKEND_ROOT（未设：/opt/agent-backend 存在则用之，否则 /opt/trajectory-platform）
#   - systemctl 优先 agent-backend，不存在再 trajectory-platform
#   - 成功后把 backend/app 与 frontend/dist 快照到 $ROOT/releases/<sha>/，只留最近 KEEP_RELEASES 份（PR-CD-2）
set -euo pipefail

# 保留几份代码快照。只存 app/ 与 dist/（各 ~1–2M），不存 data/、不存 .env。
KEEP_RELEASES="${KEEP_RELEASES:-5}"

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
#
# 必须解析出真正的 unit 名。trajectory-platform.service 在切流后只是
# agent-backend.service 的 Alias，systemctl cat 对别名也返回 0，但
# journalctl -u <别名> 是空的——日志都记在主名下。拿别名去排查等于没有日志。
resolve_unit() {
  local fragment
  fragment="$(systemctl show -P FragmentPath agent-backend.service 2>/dev/null || true)"
  if [[ -n "$fragment" && -f "$fragment" ]]; then
    printf '%s\n' agent-backend
    return
  fi
  fragment="$(systemctl show -P FragmentPath trajectory-platform.service 2>/dev/null || true)"
  if [[ -n "$fragment" && -f "$fragment" ]]; then
    # 别名的 FragmentPath 指向主 unit。用它的文件名，而不是调用时的那个名字。
    local base
    base="$(basename "$fragment")"
    printf '%s\n' "${base%.service}"
    return
  fi
  printf '%s\n' trajectory-platform
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
RELEASES="$ROOT/releases"

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

# 按 mtime 新到旧列出 releases/ 下的快照目录。
# 不用 find -printf：那是 GNU 扩展，BSD find（开发机）不认，pipefail 下会整条失败。
list_snapshots() {
  local releases="$1"
  [[ -d "$releases" ]] || return 0
  python3 - "$releases" <<'PYEOF'
import os
import sys

root = sys.argv[1]
try:
    entries = [
        e for e in os.scandir(root)
        if e.is_dir(follow_symlinks=False)
    ]
except OSError:
    sys.exit(0)
for e in sorted(entries, key=lambda e: e.stat().st_mtime, reverse=True):
    print(e.path)
PYEOF
}

# 把当时的 live 树快照成 releases/<sha>/{backend-app,dist}，并记下当时的 schema。
# 回滚只换代码，不回 DDL —— alembic-rev 就是 rollback.sh 判断「能不能直接退」的依据。
snapshot_tree() {
  local sha="$1" rev="$2"
  [[ -n "$sha" ]] || return 0
  local dest="$RELEASES/$sha"
  [[ -d "$ROOT/backend/app" && -d "$ROOT/frontend/dist" ]] || return 0
  mkdir -p "$dest" || return 1
  # --checksum：重发同一个 SHA 时 dest 已存在，而 -a 保留 mtime，
  # 同名不同内容可能被「size+mtime 相同」判成一样而跳过。树只有 1–2M，逐字节更安全。
  # 每步显式 || return 1：这个函数既被 if 调用（set -e 在条件里不生效），
  # 也被直接调用，不写返回码两处行为会不一样。
  rsync -a --checksum --delete \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$ROOT/backend/app/" "$dest/backend-app/" || return 1
  rsync -a --checksum --delete "$ROOT/frontend/dist/" "$dest/dist/" || return 1
  printf '%s\n' "$rev" > "$dest/alembic-rev" || return 1
  date -Iseconds > "$dest/taken-at" || return 1
  log "已快照 ${dest}（schema ${rev}）"
  return 0
}

# 按 taken-at 的新旧留最近 KEEP_RELEASES 份。只删 releases/ 下的目录，不碰 $ROOT 其它东西。
prune_releases() {
  [[ -d "$RELEASES" ]] || return 0
  local keep="$KEEP_RELEASES"
  [[ "$keep" =~ ^[0-9]+$ && "$keep" -ge 1 ]] || keep=5
  local n=0 d
  while IFS= read -r d; do
    [[ -n "$d" ]] || continue
    n=$((n + 1))
    if [[ "$n" -gt "$keep" ]]; then
      log "清理旧快照 $(basename "$d")"
      rm -rf "$d"
    fi
  done < <(list_snapshots "$RELEASES")
}

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
  for f in requirements.txt requirements.lock alembic.ini pyproject.toml; do
    if [[ -f "$SOURCE/backend/$f" ]]; then
      cp -a "$SOURCE/backend/$f" "$ROOT/backend/$f"
    fi
  done
  # deploy/ 整目录同步，但不 --delete。unit 的收敛不靠整文件覆盖，见 ensure_unit。
  mkdir -p "$ROOT/deploy"
  rsync -a "$SOURCE/deploy/" "$ROOT/deploy/"
}

# 线上 unit 必须具备的三处日志配置（日志方案 §3.13）。
# 缺任何一处，journalctl -o cat | jq 都过不了：没有 EnvironmentFile=-$ROOT/.version
# 则 version 恒为 unknown；没有 --no-access-log 则 uvicorn 文本行把 jq 噎住；
# 没有 SyslogIdentifier 则与机器上其他服务混在一起。
#
# 只补这三处，不整文件覆盖。unit 里还有路径、解释器、凭据文件，那些是机器
# 自己的状态（切流后是 /opt/agent-backend + /usr/bin/python3，仓里那份参考 unit
# 写的是 /opt/trajectory-platform + venv），整份拷过去会把一台正常的机器改坏。
# 三处都在就直接返回，所以重复发版是空操作，不会每版都 daemon-reload。
# --no-access-log 追加在 ExecStart 行尾，检测按子串而不是整行。
UNIT_REQUIRED_LINES=(
  'EnvironmentFile=-/opt/agent-backend/.version'
  'SyslogIdentifier=agent-backend'
  # --no-access-log 追加在 ExecStart 行尾，不是独立一行，所以这里只写标记本身。
  '--no-access-log'
)

ensure_unit() {
  local unit_path
  unit_path="$(systemctl show -P FragmentPath "$UNIT")"
  [[ -n "$unit_path" && -f "$unit_path" ]] || die "找不到 $UNIT 的 unit 文件"

  # grep -F 在 macOS 上会把以 - 开头的行当成选项，所以精确行匹配走 awk。
  local missing=() line
  for line in "${UNIT_REQUIRED_LINES[@]}"; do
    awk -v s="$line" 'BEGIN{found=0} index($0,s) {found=1} END{exit !found}' "$unit_path"       || missing+=("$line")
  done
  [[ ${#missing[@]} -eq 0 ]] && return 0

  # 白名单比对的是仓里的模板按 $ROOT 渲染出来的原文（2026-09-25 从生产抄下）。
  # 模板本身不含注释：它要与 /etc 里的 unit 逐字节一致，注释写在这里。
  # 对不上说明 unit 被人改过，拒绝猜测，交人工处理，
  # 不要在发版中途改一份看不懂的 unit。
  local expected expected_hash actual_hash
  [[ -f "$SOURCE/deploy/agent-backend.service.template" ]]     || die "缺 $SOURCE/deploy/agent-backend.service.template（无法核对 unit 原文）"
  expected="$(sed "s#__ROOT__#${ROOT}#g" "$SOURCE/deploy/agent-backend.service.template")"
  expected_hash="$(printf '%s\n' "$expected" | sha256sum | awk '{print $1}')"
  actual_hash="$(sha256sum "$unit_path" | awk '{print $1}')"
  [[ "$actual_hash" == "$expected_hash" ]]     || die "$unit_path 既缺日志配置，又不是已知的原文（sha256=${actual_hash}）。拒绝自动改，请人工核对"

  local tmp
  tmp="$(mktemp)"
  awk '
    /^EnvironmentFile=/ && !done_env { print; print "EnvironmentFile=-/opt/agent-backend/.version"; done_env=1; next }
    /^ExecStart=/ { print $0 " --no-access-log"; next }
    /^Restart=always$/ { print; print "SyslogIdentifier=agent-backend"; next }
    { print }
  ' "$unit_path" > "$tmp"

  local l
  for l in "${UNIT_REQUIRED_LINES[@]}"; do
    awk -v s="$l" 'BEGIN{found=0} index($0,s) {found=1} END{exit !found}' "$tmp"       || die "补丁没有写入 ${l}（发版中止，未改 unit）"
  done
  # 只许多出这三行。多改了别的就说明 awk 规则写错了。
  local delta
  delta="$(diff "$unit_path" "$tmp" | grep -c '^>' || true)"
  [[ "$delta" == "3" ]] || die "补丁改动了预期之外的行（$delta 行，应为 3 行）"

  cp -a "$unit_path" "${unit_path}.bak-$(date +%Y%m%d-%H%M%S)"
  cat "$tmp" > "$unit_path"
  rm -f "$tmp"
  systemctl daemon-reload     || die "daemon-reload 失败（unit 已改、备份在 ${unit_path}.bak-*，发版中止）"
  # 重新解析后三行必须还在：daemon-reload 报错不一定非 0，但 unit 坏了后续 restart 会起不来。
  systemctl cat "$UNIT" >/dev/null 2>&1 || die "daemon-reload 之后 unit 解析失败"
  log "已为 $UNIT 补上 .version / SyslogIdentifier / --no-access-log，并 daemon-reload"
}

# 先快照即将被覆盖的这一份：没有它，第一次回滚无处可退。
PREV_SHA=""
if [[ -f "$ROOT/.deploy-sha" ]]; then
  PREV_SHA="$(tr -d ' \n\r' < "$ROOT/.deploy-sha")"
fi
if [[ -n "$PREV_SHA" && ! -d "$RELEASES/$PREV_SHA/backend-app" ]]; then
  log "快照当前 live（${PREV_SHA}）"
  snapshot_tree "$PREV_SHA" "$LIVE_REV" \
    || log "快照 $PREV_SHA 失败（继续发版）：回滚将没有这一份"
fi

# 依赖必须在改任何代码之前装。2026-09-24 的发版在 rsync 之后才 pip，
# mako==1.4.3 在镜像上不存在，结果磁盘已是新代码、进程还是旧的，
# 而 Restart=always 会让一次崩溃把没走完的发版静默切成线上版本。
# 装的是暂存目录里的锁：装失败时 $ROOT 一个字节都没动。
[[ -f "$SOURCE/backend/requirements.lock" ]] || die "缺 $SOURCE/backend/requirements.lock"
log "pip3 install -r requirements.lock（改代码之前）"
pip3 install -q -r "$SOURCE/backend/requirements.lock"   || die "依赖安装失败（发版中止：代码未改动）"

# unit 也在改代码之前收敛：补丁被白名单拒绝时，$ROOT 同样一个字节都没动。
ensure_unit

log "同步代码 → $ROOT"
rsync_app

log "清理 __pycache__"
find "$ROOT/backend" -type d -name '__pycache__' -prune -exec rm -rf {} +

log "chmod +x deploy 脚本"
# pg_env.sh 是被 source 的公共库，不需要 +x，但也不能漏掉它的存在性检查：
# audit.sh / cleanup_deleted.sh / backup_pg.sh 少了它会在第一行 source 就失败。
for s in release backup_pg audit cleanup_deleted migrate push_code rollback; do
  f="$ROOT/deploy/${s}.sh"
  if [[ -f "$f" ]]; then
    chmod +x "$f"
  fi
done
if [[ ! -f "$ROOT/deploy/pg_env.sh" ]]; then
  log "警告: 缺 $ROOT/deploy/pg_env.sh，audit/cleanup/backup_pg 将无法运行"
fi

if [[ "$NEED_MIGRATE" == "1" ]]; then
  log "alembic upgrade head（cwd=$ROOT/backend）"
  (
    cd "$ROOT/backend"
    alembic current
    alembic upgrade head
    alembic current
  )
  log "启动 $UNIT"
else
  log "无迁移：systemctl restart $UNIT"
fi

# 版本表达的是「这个进程用哪份代码启动」，所以写在重启之前，
# 与 .deploy-sha 的「health 通过之后才落盘」相反（方案 §3.2）。
# 不写进 .env：那是人手工维护的配置，也和回滚打架。
# 写失败必须中止发版——进程带 unknown 启动，这次发布就无法按版本排查。
printf 'AGENT_VERSION=%s\n' "$SHA" > "$ROOT/.version" \
  || die "写 $ROOT/.version 失败（发版中止：进程会以 version=unknown 启动）"
log "已写 $ROOT/.version = $SHA （重启前）"

if [[ "$NEED_MIGRATE" == "1" ]]; then
  systemctl start "$UNIT"
else
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

# 中继是另一个进程，发版换了 app/ 之后它还在跑旧字节。配对协议一变就全 4001。
# 主服务健康之后才重启它：中继起不来不该挡住主应用，没装过这个 unit 也不该挡住。
# M6 是按需的，unit 不存在就跳过。
BRIDGE_UNIT="agent-backend-bridge.service"
if systemctl cat "$BRIDGE_UNIT" >/dev/null 2>&1; then
  log "重启 $BRIDGE_UNIT"
  systemctl restart "$BRIDGE_UNIT"
  bridge_ok=0
  for i in 1 2 3 4 5; do
    if curl -fsS http://127.0.0.1:8901/health >/dev/null; then
      log "bridge health ok (attempt $i)"
      bridge_ok=1
      break
    fi
    sleep 1
  done
  if [[ "$bridge_ok" != "1" ]]; then
    log "警告: sidecar 重启后 health 失败，主应用已更新。看 journalctl -u $BRIDGE_UNIT"
  fi
else
  log "警告: 未安装 ${BRIDGE_UNIT}，跳过中继重启（M6 按需，不影响本次发版）"
fi

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

# 快照这一份（health 已过才做），再把历史剪到 KEEP_RELEASES。
# 快照失败不该让一个已经健康的发版判红。
if snapshot_tree "$SHA" "$(live_alembic_current "$ROOT/backend")"; then
  prune_releases || log "清理旧快照失败（不影响本次发版）"
else
  log "快照失败（不影响本次发版）：回滚将没有 $SHA 这一份"
fi
log "完成"
