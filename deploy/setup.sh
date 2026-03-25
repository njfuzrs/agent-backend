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
echo "  1. 编辑 $PROJECT_DIR/.env 设置认证密码和 upload token"
echo "  2. 将 deploy/nginx.conf 中的 location 块追加到 Nginx 配置"
echo "  3. systemctl restart trajectory-platform"
echo "  4. nginx -t && systemctl reload nginx"
echo "  5. 访问 http://127.0.0.1/traj/"
