#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_USER="${TRAJ_REMOTE_USER:-root}"
REMOTE_HOST="${TRAJ_REMOTE_HOST:-127.0.0.1}"
REMOTE_BASE_DIR="${TRAJ_REMOTE_BASE_DIR:-/opt/trajectory-platform}"
SSH_KEY="${TRAJ_SSH_KEY:-}"
SSH_OPTS="-o StrictHostKeyChecking=no"

build_frontend() {
  echo "==> 构建前端"
  (cd "$ROOT_DIR/frontend" && pnpm build)
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

sync_frontend() {
  echo "==> 同步前端 dist"
  rsync_cmd --delete \
    "$ROOT_DIR/frontend/dist/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_BASE_DIR}/frontend/dist/"
}

sync_backend() {
  echo "==> 同步后端代码"
  rsync_cmd \
    --exclude 'venv' \
    --exclude '__pycache__' \
    --exclude '.env' \
    --exclude '*.pyc' \
    "$ROOT_DIR/backend/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_BASE_DIR}/backend/"
}

sync_deploy() {
  echo "==> 同步 deploy 目录"
  rsync_cmd \
    "$ROOT_DIR/deploy/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_BASE_DIR}/deploy/"
}

restart_backend() {
  echo "==> 远端安装依赖并重启服务"
  ssh_cmd "pip3 install -r ${REMOTE_BASE_DIR}/backend/requirements.txt && systemctl restart trajectory-platform && systemctl status trajectory-platform --no-pager"
}

health_check() {
  echo "==> 健康检查"
  ssh_cmd "for i in 1 2 3 4 5 6 7 8 9 10; do curl -fsS http://127.0.0.1:8900/api/v1/health && exit 0; sleep 2; done; exit 1"
}

build_frontend
sync_frontend
sync_backend
sync_deploy
restart_backend
health_check

echo "==> 完成"
