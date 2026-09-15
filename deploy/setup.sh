#!/bin/bash
# deploy/setup.sh — ECS 一键部署脚本
set -e

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
echo "  5. 访问 http://127.0.0.1/traj/"
