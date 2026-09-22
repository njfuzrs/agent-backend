#!/bin/bash
# 远程部署脚本 — 在 ECS 上执行

set -e

echo "=== 1. 清理残留进程 ==="
systemctl stop trajectory-platform 2>/dev/null || true
for pid in $(pgrep -f 'uvicorn.*app.main' 2>/dev/null); do
    kill -9 $pid 2>/dev/null || true
done
sleep 1
echo "清理完成"

echo "=== 2. 配置 systemd 服务 ==="
cat > /etc/systemd/system/trajectory-platform.service << 'SVCEOF'
[Unit]
Description=Agent Backend
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/trajectory-platform/backend
EnvironmentFile=/opt/trajectory-platform/.env
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8900 --workers 2 --log-level info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable trajectory-platform
systemctl start trajectory-platform
sleep 3
echo "systemd 状态:"
systemctl is-active trajectory-platform

echo "=== 3. 健康检查 ==="
curl -sf http://127.0.0.1:8900/api/v1/health && echo ""

echo "=== 4. 安装 Nginx ==="
if ! command -v nginx &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq nginx
fi
nginx -v 2>&1

echo "=== 5. 配置 Nginx 反代 ==="
cat > /etc/nginx/sites-available/trajectory-platform << 'NGXEOF'
server {
    listen 80;
    server_name _;

    # 前端静态文件
    location /traj/ {
        alias /opt/trajectory-platform/frontend/dist/;
        try_files $uri $uri/ /traj/index.html;
    }

    # 后端 API 反代
    location /traj/api/ {
        proxy_pass http://127.0.0.1:8900/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        client_max_body_size 100M;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
NGXEOF

# 启用站点（如果已有 default 站点，合并到 default 中）
if [ -f /etc/nginx/sites-enabled/default ]; then
    # 检查 default 中是否已有 /traj/ 配置
    if ! grep -q '/traj/' /etc/nginx/sites-enabled/default 2>/dev/null; then
        # 在 default server 块的最后一个 } 之前插入 location 块
        sed -i '/^}$/i \
    # Agent Backend 前端\
    location /traj/ {\
        alias /opt/trajectory-platform/frontend/dist/;\
        try_files $uri $uri/ /traj/index.html;\
    }\
\
    # Agent Backend API\
    location /traj/api/ {\
        proxy_pass http://127.0.0.1:8900/api/;\
        proxy_set_header Host $host;\
        proxy_set_header X-Real-IP $remote_addr;\
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\
        client_max_body_size 100M;\
        proxy_read_timeout 120s;\
        proxy_send_timeout 120s;\
    }' /etc/nginx/sites-enabled/default
        echo "已追加到 default 站点"
    else
        echo "/traj/ 配置已存在于 default 站点"
    fi
else
    ln -sf /etc/nginx/sites-available/trajectory-platform /etc/nginx/sites-enabled/
    echo "已启用独立站点配置"
fi

nginx -t
systemctl reload nginx
echo "Nginx 配置完成"

echo "=== 6. 安装 pnpm ==="
if ! command -v pnpm &>/dev/null; then
    npm install -g pnpm
fi
pnpm --version

echo "=== 7. 构建前端 ==="
cd /opt/trajectory-platform/frontend
pnpm install --frozen-lockfile 2>&1 | tail -3
pnpm run build 2>&1 | tail -5

echo "=== 8. 防火墙 ==="
ufw allow 8900/tcp 2>/dev/null || true

echo "=== 9. 最终验证 ==="
echo "后端 API:"
curl -sf http://127.0.0.1:8900/api/v1/health && echo ""
echo "Nginx 反代（HTTPS 本机 Host；:80 无 Host 是 410）:"
curl -sf --resolve www.sid-code.cc:443:127.0.0.1 \
  https://www.sid-code.cc/traj/api/v1/health && echo ""
echo ""
echo "=== 部署完成 ==="
echo "外网访问: 见你的 nginx 反代（http://<your-host>/traj/）"
echo "API 地址: http://<your-host>/traj/api/v1/health"
