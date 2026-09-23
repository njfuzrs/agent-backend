#!/usr/bin/env bash
# deploy/setup.sh — ⚠️ 已废弃（SQLite + 本地 traj_files 时代的 ECS 一键部署脚本）
#
# 现行入口：首次装机 deploy/remote_setup.sh，日常发版 deploy/push_code.sh → release.sh。
# 本脚本的假设现在条条不成立，跑它会把一台已正常的机器改坏：
#   - 建 data/traj_files/{claude-code,codex,...}：STORAGE_BACKEND=oss 下没有这些目录，
#     建出来只会让人误以为轨迹还在本地（这正是 .env 里 TRAJ_FILES_DIR 那个死键的来源）；
#   - apt install sqlite3 + 建 backend/venv：生产是 PostgreSQL 且无 venv（系统 python3）；
#   - 往 crontab 写 `0 3 * * * deploy/backup.sh`：backup.sh 已废弃并会 exit 1，
#     真正的日备是 backup_pg.sh，装上这条 cron 等于每天 03:00 静默失败；
#   - 覆写 /etc/systemd/system/trajectory-platform.service：2026-09-23 切流后
#     真实 unit 是 agent-backend.service（trajectory-platform 只是 Alias）。
# 保留文件仅为历史参考；要删请单独开 PR，不要在别的改动里顺手删。
set -euo pipefail

echo "setup.sh 已废弃：首次装机请用 deploy/remote_setup.sh，发版请用 deploy/push_code.sh" >&2
echo "（本脚本面向 SQLite + 本地 traj_files，且会给已废弃的 backup.sh 装 cron）" >&2
exit 1

# ---- 以下为历史实现，不再执行 ----
PROJECT_DIR="/opt/trajectory-platform"

echo "=== 1. 创建目录结构 ==="
mkdir -p $PROJECT_DIR/{backend,frontend,data/traj_files/{claude-code,codex,gemini-cli,sid-code},deploy,backups}

echo "=== 2. 安装系统依赖 ==="
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip nginx sqlite3

echo "=== 3. 部署后端 ==="
cd $PROJECT_DIR/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "=== 3.5 初始化数据库 schema ==="
# schema 由 Alembic 接管（M0/PR-0.1）。原来靠 init_db() 的 create_all 自动建表，
# 那条路已删除 —— 它不改已有表的列，是「生产无加列路径」那个 bug 的成因。
#
# ⚠️ 全新库用 upgrade；如果这台机器上已有带数据的库，改用：
#      deploy/migrate.sh stamp
cd $PROJECT_DIR/backend
if [ -f "$PROJECT_DIR/backend/.env" ]; then
  alembic upgrade head
  alembic current
else
  echo "!! 未找到 backend/.env —— 跳过迁移。"
  echo "   请先创建 .env（至少含 AUTH_PASSWORD / UPLOAD_TOKEN），再执行："
  echo "     cd $PROJECT_DIR/backend && alembic upgrade head"
fi

echo "=== 4. 部署前端 ==="
cd $PROJECT_DIR/frontend
# 确保 node/pnpm 已安装
command -v pnpm >/dev/null 2>&1 || npm install -g pnpm
pnpm install
pnpm run build

echo "=== 5. 配置 systemd ==="
cp $PROJECT_DIR/deploy/trajectory-platform.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable trajectory-platform
systemctl start trajectory-platform

echo "=== 6. 等待后端启动 ==="
sleep 3
curl -sf http://127.0.0.1:8900/api/v1/health || { echo "后端启动失败"; exit 1; }

echo "=== 7. 配置 Nginx ==="
# 将 nginx.conf 中的 location 块追加到已有 server 配置
echo "请手动将 deploy/nginx.conf 中的 location 块追加到 /etc/nginx/sites-available/ 中已有的 server 块"
echo "然后执行: nginx -t && systemctl reload nginx"

echo "=== 8. 配置备份 cron ==="
chmod +x $PROJECT_DIR/deploy/backup.sh
(crontab -l 2>/dev/null; echo "0 3 * * * $PROJECT_DIR/deploy/backup.sh >> /var/log/traj-backup.log 2>&1") | sort -u | crontab -

echo ""
echo "=== 部署完成 ==="
echo "后端 API: http://127.0.0.1:8900/api/v1/health"
echo "前端构建: $PROJECT_DIR/frontend/dist/"
echo ""
echo "下一步："
echo "  1. 编辑 $PROJECT_DIR/backend/.env 设置 AUTH_PASSWORD 与 UPLOAD_TOKEN"
echo "  2. 将 deploy/nginx.conf 中的 location 块追加到 Nginx 配置"
echo "  3. systemctl restart trajectory-platform"
echo "  4. nginx -t && systemctl reload nginx"
echo "  5. 访问 http://<your-host>/traj/（见你的 nginx 反代）"
