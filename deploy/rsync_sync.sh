#!/bin/bash
# deploy/rsync_sync.sh — 批量同步 .traj 文件到云端

REMOTE_USER="${TRAJ_REMOTE_USER:-root}"
# TRAJ_SSH_KEY 是密钥文件，不能代替 host。未设目标主机则退出，避免把生产公网 IP 写进默认值。
if [[ -z "${TRAJ_REMOTE_HOST:-}" ]]; then
  echo "未设置 TRAJ_REMOTE_HOST（目标主机）。请 export TRAJ_REMOTE_HOST=<host> 后再跑。" >&2
  echo "TRAJ_SSH_KEY 只用于指定密钥文件，不能代替 host。" >&2
  exit 1
fi
REMOTE_HOST="$TRAJ_REMOTE_HOST"
# 生产路径与开源仓名分叉是有意的：GitHub 仓是 agent-backend，线上目录仍是 /opt/trajectory-platform。
REMOTE_DIR="/opt/trajectory-platform/data/traj_files/claude-code/"
LOCAL_DIR="${TRAJ_LOCAL_DIR:-$HOME/Code/person/claude-trace/trajectories/traj/}"
SSH_KEY="${TRAJ_SSH_KEY:-$HOME/.ssh/id_rsa}"
# 不再内置真实 token 做默认值（规划 §PR-0.5）—— 缺失即退出
UPLOAD_TOKEN="${TRAJ_UPLOAD_TOKEN:?需要设置 TRAJ_UPLOAD_TOKEN}"

echo "同步 $LOCAL_DIR → ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"

# rsync 增量同步
rsync -avz --progress \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new" \
    --include='*.traj' \
    --exclude='*' \
    "$LOCAL_DIR" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"

echo ""
echo "同步完成，执行服务端索引重建..."

# 触发服务端重新扫描并索引
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new \
    "${REMOTE_USER}@${REMOTE_HOST}" \
    "curl -s -X POST http://localhost:8900/api/v1/upload/reindex \
     -H 'X-Upload-Token: ${UPLOAD_TOKEN}'"

echo ""
echo "完成"
