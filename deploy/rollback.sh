#!/usr/bin/env bash
# rollback.sh — 把代码退回 $ROOT/releases/<sha> 的某一份快照（在 ECS 上执行，PR-CD-2）
#
# 用法：
#   rollback.sh                 # 退到 .deploy-sha.prev
#   rollback.sh <sha>           # 退到指定快照（release.sh 发版时留下的）
#   rollback.sh <sha> --force   # schema 已经前进也强行退代码（危险，见下）
#   rollback.sh --list          # 列出服务器上还留着哪些快照
#
# 退出码：0 成功；非 0 失败（调用方 GitHub job 失败）
#
# 硬约束（合入 main 自动部署设计 §6.1 / §8 / 生产目录文 §9）：
#   - 只换 backend/app/ 与 frontend/dist/。不动 migrations/、不动 requirements.txt、不 pip
#   - **绝不** alembic downgrade。0006 的 downgrade() 是空的；破坏性迁移的回滚手段是 pg_dump 恢复
#   - 快照的 alembic-rev 与线上不一致 → 默认拒绝，提示用迁库前那份 trajdb_pre_*.sql.gz 恢复
#   - 不 mv /opt、不改 nginx
#   - unit 只按白名单收敛日志配置（见 ensure_unit），其余一个字节不动
#   - 不读、不传、不改业务凭据；.env 必须已经在位，否则拒绝动
set -euo pipefail

log() { echo "[rollback $(date +%Y-%m-%dT%H:%M:%S%z)] $*"; }
die() { log "错误: $*" >&2; exit 1; }

usage() {
  sed -n '3,10p' "$0" >&2
  exit 2
}

# 与 release.sh / backup_pg.sh 同一套根目录规则。
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

resolve_unit() {
  if systemctl cat agent-backend.service >/dev/null 2>&1; then
    printf '%s\n' agent-backend
  else
    printf '%s\n' trajectory-platform
  fi
}

# 与 release.sh 的 ensure_unit 是同一份逻辑。回滚也要收敛 unit：
# 退回 PR-L1 之前的代码时，--no-access-log 会把旧版 uvicorn 的访问日志一起关掉，
# 那是旧代码唯一的访问记录，所以旧快照必须把这一行拿掉。
UNIT_REQUIRED_LINES=(
  'EnvironmentFile=-/opt/agent-backend/.version'
  'SyslogIdentifier=agent-backend'
  # --no-access-log 追加在 ExecStart 行尾，不是独立一行，所以这里只写标记本身。
  '--no-access-log'
)

ensure_unit() {
  local new_code="$1"
  local unit_path
  unit_path="$(systemctl show -P FragmentPath "$UNIT")"
  [[ -n "$unit_path" && -f "$unit_path" ]] || die "找不到 $UNIT 的 unit 文件"

  local has_logging=0
  grep -qxF 'agent.access' "$ROOT/backend/app/core/logging.py" 2>/dev/null && has_logging=1

  if [[ "$has_logging" == "1" ]]; then
    local missing=() line
    for line in "${UNIT_REQUIRED_LINES[@]}"; do
      awk -v s="$line" 'BEGIN{found=0} index($0,s) {found=1} END{exit !found}' "$unit_path"         || missing+=("$line")
    done
    [[ ${#missing[@]} -eq 0 ]] && return 0
    [[ "$new_code" == "1" ]] || die "线上 unit 缺日志配置，而这次回滚换上去的是旧代码，补了也没有进程读它"
    # 与 release.sh 同一份白名单：只对已知原文打补丁。
    # 模板随发版同步进 $ROOT/deploy，回滚不改 deploy/，所以读的是线上这份。
    [[ -f "$ROOT/deploy/agent-backend.service.template" ]]       || die "缺 $ROOT/deploy/agent-backend.service.template（无法核对 unit 原文）"
    local expected expected_hash actual_hash
    expected="$(sed "s#__ROOT__#${ROOT}#g" "$ROOT/deploy/agent-backend.service.template")"
    expected_hash="$(printf '%s\n' "$expected" | sha256sum | awk '{print $1}')"
    actual_hash="$(sha256sum "$unit_path" | awk '{print $1}')"
    [[ "$actual_hash" == "$expected_hash" ]]       || die "$unit_path 既缺日志配置，又不是已知的原文（sha256=${actual_hash}）。拒绝自动改，请人工核对"
    log "unit 缺日志配置，按白名单补上"
  else
    awk 'BEGIN{found=0} index($0,"--no-access-log") {found=1} END{exit !found}' "$unit_path" || return 0
    log "回滚目标没有日志内核，去掉 --no-access-log（旧代码只靠 uvicorn 访问日志）"
  fi

  local tmp
  tmp="$(mktemp)"
  if [[ "$has_logging" == "1" ]]; then
    awk '
      /^EnvironmentFile=/ && !done_env { print; print "EnvironmentFile=-/opt/agent-backend/.version"; done_env=1; next }
      /^ExecStart=/ { print $0 " --no-access-log"; next }
      /^Restart=always$/ { print; print "SyslogIdentifier=agent-backend"; next }
      { print }
    ' "$unit_path" > "$tmp"
  else
    grep -vxF -- '--no-access-log' "$unit_path" \
      | sed 's/ --no-access-log$//' > "$tmp"
  fi

  if [[ "$has_logging" == "1" ]]; then
    local l
    for l in "${UNIT_REQUIRED_LINES[@]}"; do
      awk -v s="$l" 'BEGIN{found=0} index($0,s) {found=1} END{exit !found}' "$tmp"         || die "补丁没有写入 ${l}（回滚中止，未改 unit）"
    done
    local delta
    delta="$(diff "$unit_path" "$tmp" | grep -c '^>' || true)"
    [[ "$delta" == "3" ]] || die "补丁改动了预期之外的行（$delta 行，应为 3 行）"
  else
    grep -q -- '--no-access-log' "$tmp" && die "补丁没有去掉 --no-access-log"
  fi

  cp -a "$unit_path" "${unit_path}.bak-$(date +%Y%m%d-%H%M%S)"
  cat "$tmp" > "$unit_path"
  rm -f "$tmp"
  systemctl daemon-reload || die "daemon-reload 失败（unit 已改、备份在 ${unit_path}.bak-*，回滚中止）"
  systemctl cat "$UNIT" >/dev/null 2>&1 || die "daemon-reload 之后 unit 解析失败"
  log "unit 已收敛并 daemon-reload"
}

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

live_alembic_current() {
  local backend="$1"
  (cd "$backend" && alembic current 2>&1) \
    | awk '/^[0-9a-fA-F]/ {print $1}' \
    | tail -1
}

TARGET=""
FORCE=0
LIST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    --list)
      LIST=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    -*)
      die "未知参数: $1"
      ;;
    *)
      [[ -z "$TARGET" ]] || die "只能指定一个 SHA（已有 ${TARGET}）"
      TARGET="$1"
      shift
      ;;
  esac
done

ROOT="$(resolve_root)"
RELEASES="$ROOT/releases"

if [[ "$LIST" == "1" ]]; then
  [[ -d "$RELEASES" ]] || die "没有 ${RELEASES}（还没发过版，或这台机器是切流前后的另一套路径）"
  log "可回滚快照（${RELEASES}）："
  while IFS= read -r d; do
    [[ -n "$d" ]] || continue
    rev="$(tr -d ' \n\r' < "$d/alembic-rev" 2>/dev/null || echo '?')"
    at="$(tr -d ' \n\r' < "$d/taken-at" 2>/dev/null || echo '?')"
    ok="完整"
    [[ -d "$d/backend-app" && -d "$d/dist" ]] || ok="不完整"
    printf '  %s  schema=%-6s  %s  %s\n' "$(basename "$d")" "${rev:-?}" "${at:-?}" "$ok"
  done < <(list_snapshots "$RELEASES")
  exit 0
fi

# 不给 SHA 就退到上一份。
if [[ -z "$TARGET" ]]; then
  [[ -f "$ROOT/.deploy-sha.prev" ]] || die "没有 $ROOT/.deploy-sha.prev，请显式给 SHA（--list 看有哪些）"
  TARGET="$(tr -d ' \n\r' < "$ROOT/.deploy-sha.prev")"
  [[ -n "$TARGET" ]] || die "$ROOT/.deploy-sha.prev 是空的"
  log "未指定 SHA，用 .deploy-sha.prev = $TARGET"
fi

UNIT="$(resolve_unit)"
SNAP="$RELEASES/$TARGET"
CURRENT=""
[[ -f "$ROOT/.deploy-sha" ]] && CURRENT="$(tr -d ' \n\r' < "$ROOT/.deploy-sha")"

log "TARGET=$TARGET"
log "CURRENT=${CURRENT:-未知}"
log "ROOT=$ROOT"
log "UNIT=$UNIT"

[[ -d "$SNAP" ]] || die "没有快照 ${SNAP}（--list 看有哪些；只有 release.sh 发过的 SHA 才有）"
[[ -f "$SNAP/backend-app/main.py" ]] || die "快照不完整：缺 $SNAP/backend-app/main.py"
[[ -f "$SNAP/dist/index.html" ]] || die "快照不完整：缺 $SNAP/dist/index.html"
grep -q '/traj/' "$SNAP/dist/index.html" \
  || die "快照的 dist/index.html 不含 /traj/（拒绝回滚）"
[[ -f "$ROOT/.env" ]] || die "缺 $ROOT/.env（回滚不写凭据，拒绝继续）"
[[ -f "$ROOT/backend/.env" ]] || die "缺 $ROOT/backend/.env（回滚不写凭据，拒绝继续）"

if [[ "$TARGET" == "$CURRENT" ]]; then
  log "线上已经是 ${TARGET}，无需回滚"
  exit 0
fi

# schema 门：快照那会儿的 head 与现在不一致，说明中间跑过迁移。
# 退代码不退 DDL → 旧 ORM 可能读到不认识的表/列，或少了列直接 crash loop。
# 正确手段是恢复 trajdb_pre_<sha>_*.sql.gz，不是 alembic downgrade。
SNAP_REV="$(tr -d ' \n\r' < "$SNAP/alembic-rev" 2>/dev/null || true)"
LIVE_REV="$(live_alembic_current "$ROOT/backend" || true)"
log "schema 快照=${SNAP_REV:-未知}  线上=${LIVE_REV:-未知}"
if [[ -z "$SNAP_REV" || -z "$LIVE_REV" ]]; then
  if [[ "$FORCE" != "1" ]]; then
    die "读不到 schema 版本（快照=${SNAP_REV:-空} 线上=${LIVE_REV:-空}）。确认无破坏性迁移后加 --force"
  fi
  log "警告: schema 版本读不全，--force 继续"
elif [[ "$SNAP_REV" != "$LIVE_REV" ]]; then
  if [[ "$FORCE" != "1" ]]; then
    log "线上 schema 是 ${LIVE_REV}，快照是 $SNAP_REV —— 中间跑过迁移。" >&2
    log "不要 alembic downgrade。两条正路：" >&2
    log "  A) forward fix：修好再发一次新 SHA" >&2
    log "  B) 停服务 → 恢复 $ROOT/data/backups/trajdb_pre_${TARGET:0:12}_*.sql.gz → 再跑本脚本" >&2
    log "  只在确认这次迁移是纯加表/加列（旧代码不碰）时才 --force" >&2
    die "schema 不一致，拒绝回滚"
  fi
  log "警告: schema 不一致（$LIVE_REV vs ${SNAP_REV}），--force 继续"
fi

# 失败时把服务拉起来，不要停在 stop 与 start 之间。
trap 'rc=$?; if [[ $rc -ne 0 ]]; then
  echo "[rollback] 失败 (exit $rc)，systemctl start '"$UNIT"'" >&2
  systemctl start '"$UNIT"' || true
fi' EXIT

# --checksum：rsync 默认按「大小 + mtime」快速判断，而 -a 会把 mtime 一并保留，
# 于是同名不同版本的文件可能被判成「一样」而跳过（本地演练真踩到：退回去的
# main.py 还是新版内容）。回滚必须逐字节确定，树才 1–2M，校验和的代价可以忽略。
log "同步快照 → ${ROOT}（只换 app/ 与 dist/，按校验和比对）"
rsync -a --checksum --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude '.env.bak-*' \
  "$SNAP/backend-app/" "$ROOT/backend/app/"
rsync -a --checksum --delete "$SNAP/dist/" "$ROOT/frontend/dist/"

log "清理 __pycache__"
find "$ROOT/backend" -type d -name '__pycache__' -prune -exec rm -rf {} +

# 换上去的代码已在磁盘上，按它收敛 unit。旧快照没有日志内核时，
# 这里会把 --no-access-log 拿掉，否则旧代码连 uvicorn 访问日志都没有。
ensure_unit 0

# 与 release.sh 同一份文件、同一条时序：重启前写，写失败中止。
# 退回去的进程记的是目标 SHA，不是退之前的那一版。
printf 'AGENT_VERSION=%s\n' "$TARGET" > "$ROOT/.version" \
  || die "写 $ROOT/.version 失败（回滚中止：进程会以 version=unknown 启动）"
log "已写 $ROOT/.version = $TARGET （重启前）"

log "systemctl restart $UNIT"
systemctl restart "$UNIT"

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
[[ "$ok" == "1" ]] || die "http://127.0.0.1:8900/api/v1/health 连续失败（旧代码也起不来，看 journalctl -u ${UNIT}）"

# 2026-09-22 起无 Host 的 :80 是整块 410，反代要问 HTTPS 域名（与 release.sh 同）。
log "health nginx /traj/api（HTTPS 本机 Host）"
curl -fsS --resolve www.sid-code.cc:443:127.0.0.1 \
  https://www.sid-code.cc/traj/api/v1/health >/dev/null \
  || die "https://www.sid-code.cc/traj/api/v1/health 本机反代失败"

# 记下「从哪退回来的」，这样 rollback 之后还能再 rollback 回去。
if [[ -n "$CURRENT" ]]; then
  printf '%s\n' "$CURRENT" > "$ROOT/.deploy-sha.prev"
fi
printf '%s\n' "$TARGET" > "$ROOT/.deploy-sha"
date -Iseconds > "$ROOT/.deploy-time"
log "已写 $ROOT/.deploy-sha = $TARGET"
log "完成（schema 未动：仍是 ${LIVE_REV:-未知}）"
