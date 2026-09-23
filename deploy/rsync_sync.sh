#!/usr/bin/env bash
# deploy/rsync_sync.sh — ⚠️ 已废弃（本地存储时代的批量补传：rsync + reindex）
#
# 现行补传：claude-trace 侧的 uploader（自动）与 sync.py（批量），走 HTTP
# POST /api/v1/upload/traj，见 claude-trace/docs/upload-protocol.md。
# 本脚本在 STORAGE_BACKEND=oss 下是**静默空转**，比报错更坏：
#   - rsync 到 data/traj_files/claude-code/ —— OSS 模式下没有任何代码读这个目录；
#   - 随后调的 POST /upload/reindex 在 is_oss 时直接返回「不需要 reindex」，
#     一条都不会入库。于是文件躺在服务器磁盘上，管理台却永远看不到，
#     而脚本最后照样打印「完成」。
# 保留文件仅为历史参考；要删请单独开 PR，不要在别的改动里顺手删。
set -euo pipefail

echo "rsync_sync.sh 已废弃：批量补传请用 claude-trace 的 sync.py（HTTP 上传协议）" >&2
echo "（本脚本 rsync 到 data/traj_files/ 并调 reindex，OSS 模式下两步都是空转）" >&2
exit 1

# ---- 以下为历史实现，不再执行 ----
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
