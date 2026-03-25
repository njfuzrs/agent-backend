#!/bin/bash
# deploy/rsync_sync.sh — 批量同步 .traj 文件到云端

REMOTE_USER="${TRAJ_REMOTE_USER:-root}"
REMOTE_HOST="${TRAJ_REMOTE_HOST:-127.0.0.1}"
REMOTE_DIR="/opt/trajectory-platform/data/traj_files/claude-code/"
LOCAL_DIR="${TRAJ_LOCAL_DIR:-$HOME/Code/person/claude-trace/trajectories/traj/}"
SSH_KEY="${TRAJ_SSH_KEY:-$HOME/.ssh/id_rsa}"
UPLOAD_TOKEN="${TRAJ_UPLOAD_TOKEN:-<REDACTED_TOKEN>}"

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
