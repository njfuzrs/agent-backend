#!/usr/bin/env bash
# push_code.sh — 本机发版入口。构建前端，把产物 rsync 到服务器暂存目录，
# 再 ssh 调远端唯一切换脚本 deploy/release.sh。
#
# 停服务 / 备份 / 迁库 / chmod / 启动 / health 都在 release.sh 里，
# 不要在本文件再复制一份，否则三个月后又分叉。
#
# 用法：
#   export TRAJ_REMOTE_HOST=<host>
#   export TRAJ_SSH_KEY=~/.ssh/id_ed25519    # 或设置 SSHPASS
#   bash deploy/push_code.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_USER="${TRAJ_REMOTE_USER:-root}"
# TRAJ_SSH_KEY 是密钥文件，不能代替 host。未设目标主机则退出，避免把生产公网 IP 写进默认值。
if [[ -z "${TRAJ_REMOTE_HOST:-}" ]]; then
  echo "未设置 TRAJ_REMOTE_HOST（目标主机）。请 export TRAJ_REMOTE_HOST=<host> 后再跑。" >&2
  echo "TRAJ_SSH_KEY 只用于指定密钥文件，不能代替 host。" >&2
  exit 1
fi
REMOTE_HOST="$TRAJ_REMOTE_HOST"
# 切流前默认仍是 /opt/trajectory-platform（生产目录文：切流前不要改仓库默认值）。
# 传给远端的 AGENT_BACKEND_ROOT；release.sh 未收到时会自己探测。
REMOTE_BASE_DIR="${TRAJ_REMOTE_BASE_DIR:-${AGENT_BACKEND_ROOT:-/opt/trajectory-platform}}"
SSH_KEY="${TRAJ_SSH_KEY:-}"
SSH_OPTS="-o StrictHostKeyChecking=no"

if ! git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "不在 git 仓库内，无法取 SHA" >&2
  exit 1
fi
SHA="$(git -C "$ROOT_DIR" rev-parse HEAD)"
STAGE="/tmp/agent-backend-release-${SHA}"

echo "==> SHA=$SHA"
echo "==> 远端暂存 $REMOTE_USER@$REMOTE_HOST:$STAGE"
echo "==> AGENT_BACKEND_ROOT=$REMOTE_BASE_DIR"

build_frontend() {
  echo "==> 构建前端"
  (cd "$ROOT_DIR/frontend" && pnpm build)
  [[ -f "$ROOT_DIR/frontend/dist/index.html" ]] || {
    echo "前端构建后没有 dist/index.html" >&2
    exit 1
  }
}

rsync_cmd() {
  if [[ -n "$SSH_KEY" ]]; then
    rsync -avz "$@" -e "ssh -i $SSH_KEY $SSH_OPTS"
  else
    if [[ -z "${SSHPASS:-}" ]]; then
      echo "缺少登录凭据：请设置 SSHPASS 或 TRAJ_SSH_KEY" >&2
      exit 1
    fi
    sshpass -e rsync -avz "$@" -e "ssh $SSH_OPTS"
  fi
}

ssh_cmd() {
  if [[ -n "$SSH_KEY" ]]; then
    ssh -i "$SSH_KEY" $SSH_OPTS "${REMOTE_USER}@${REMOTE_HOST}" "$1"
  else
    if [[ -z "${SSHPASS:-}" ]]; then
      echo "缺少登录凭据：请设置 SSHPASS 或 TRAJ_SSH_KEY" >&2
      exit 1
    fi
    sshpass -e ssh $SSH_OPTS "${REMOTE_USER}@${REMOTE_HOST}" "$1"
  fi
}

stage_remote() {
  echo "==> 暂存到 ${STAGE} （不直接写 ${REMOTE_BASE_DIR}）"
  ssh_cmd "mkdir -p $(printf '%q' "$STAGE")/frontend/dist $(printf '%q' "$STAGE")/backend $(printf '%q' "$STAGE")/deploy"
  rsync_cmd --delete \
    "$ROOT_DIR/frontend/dist/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${STAGE}/frontend/dist/"
  rsync_cmd --delete \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '.env' \
    --exclude '.env.bak-*' \
    --exclude '*.pyc' \
    --exclude '.ruff_cache' \
    "$ROOT_DIR/backend/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${STAGE}/backend/"
  rsync_cmd --delete \
    "$ROOT_DIR/deploy/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${STAGE}/deploy/"
}

run_release() {
  echo "==> 远端 release.sh"
  # 把本机选中的根目录传过去，避免脚本在服务器上探测到另一套路径
  ssh_cmd "AGENT_BACKEND_ROOT=$(printf '%q' "$REMOTE_BASE_DIR") bash $(printf '%q' "$STAGE/deploy/release.sh") $(printf '%q' "$SHA") --source $(printf '%q' "$STAGE")"
}

build_frontend
stage_remote
run_release

echo "==> 完成  SHA=$SHA"
