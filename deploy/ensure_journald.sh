#!/usr/bin/env bash
# ensure_journald.sh — 把应用日志的保留期装到这台机器上。
#
# 在服务器上跑，一次即可，重复跑是空操作：
#   bash deploy/ensure_journald.sh
#
# 为什么不进 release.sh：发版的硬约束是不改机器拓扑。journald 是整机配置，
# 一次装错会删掉别的服务正在用的日志，所以单独走这一步，由人显式执行。
# 保留期取 30 天（方案上限，也满足 14 天下限），外加 1G 的体积兜底。
# 只写 drop-in，不改 /etc/systemd/journald.conf 本体。
set -euo pipefail

log() { echo "[journald $(date +%Y-%m-%dT%H:%M:%S%z)] $*"; }
die() { log "错误: $*" >&2; exit 1; }

SELF="$(cd "$(dirname "$0")" && pwd)"
SRC="$SELF/journald-agent-backend.conf"
DEST_DIR="/etc/systemd/journald.conf.d"
DEST="$DEST_DIR/agent-backend.conf"

[[ -f "$SRC" ]] || die "缺 $SRC"
[[ "$(id -u)" == "0" ]] || die "需要 root（要写 $DEST 并重启 systemd-journald）"

mkdir -p "$DEST_DIR"
if [[ -f "$DEST" ]] && cmp -s "$SRC" "$DEST"; then
  log "$DEST 已是预期内容，跳过"
  exit 0
fi

if [[ -f "$DEST" ]]; then
  cp -a "$DEST" "${DEST}.bak-$(date +%Y%m%d-%H%M%S)"
fi
# 先落到临时文件再移动，避免写到一半被 journald 读走。
tmp="$(mktemp)"
cp "$SRC" "$tmp"
install -m 644 "$tmp" "$DEST"
rm -f "$tmp"

systemctl restart systemd-journald || die "systemd-journald 重启失败（drop-in 已写入 ${DEST}）"
systemctl is-active --quiet systemd-journald || die "systemd-journald 没有回到 active"

# 现有归档不会在重启时被删。按上限收一次，把已经超过 30 天的清掉。
journalctl --vacuum-time=30d >/dev/null

log "已安装 $DEST 并按 30 天收过一次"
systemd-analyze cat-config systemd/journald.conf | grep -E '^(SystemMaxRetentionSec|SystemMaxUse)='
