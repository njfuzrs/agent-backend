# 手动验收脚本

`test_boundaries.py` 与 `test_identity.py` 进 CI。本目录其余脚本对着**活服务端**跑，不进 CI。

未设 `TRAJ_PLATFORM_URL` 时，四个联网脚本默认打 `http://127.0.0.1:8900`（仅回环）。打生产或经 nginx 的 `/traj` 前缀时，显式传 `--url` 或环境变量。

## 脚本清单

| 脚本 | 用途 | 依赖 |
|------|------|------|
| `test_boundaries.py` | 双平面鉴权 / 冻结区 URL / 运行时不得建表 | 无活服务端（import app） |
| `test_identity.py` | 注册码二次使用 / 吊销立即失效 / 明文不入库 / `/ctl/` 401 | 无活服务端（独立 SQLite） |
| `test_e2e.py` | 端到端：采集→上传→存储→查询全链路 | 服务端运行 |
| `test_concurrent.py` | 并发上传，验证无阻塞 | 服务端运行 |
| `test_fault.py` | 故障：SDK 内部逻辑（压缩、队列、重试） | 无需本仓服务端 |
| `test_frontend_api.py` | 前端 API 回归 | 服务端运行 |
| `test_audit.py` | 对账：DB vs 存储一致性 + 迁移验证 | 服务端运行 |

## 环境准备

### 1. 环境变量

```bash
# 服务端地址。本地直连后端用回环；经 nginx 时带 /traj 前缀。
export TRAJ_PLATFORM_URL="http://127.0.0.1:8900"
# export TRAJ_PLATFORM_URL="http://<your-host>/traj"

# 上传 token（与服务端 .env 中的 UPLOAD_TOKEN 一致，无内置默认值）
export TRAJ_UPLOAD_TOKEN="your-upload-token"

# 认证信息（与服务端 .env 中的 AUTH_USERNAME / AUTH_PASSWORD 一致）
export TRAJ_AUTH_USER="admin"
export TRAJ_AUTH_PASS="your-password"
```

### 2. 确认服务端运行

```bash
curl -s "${TRAJ_PLATFORM_URL:-http://127.0.0.1:8900}/api/v1/health" | python3 -m json.tool
# 应返回: {"status": "ok", "version": "1.0.0"}
```

### 3. 依赖

联网脚本只用 Python 标准库。`test_fault.py` 需要导入采集端 `uploader.py`，在 claude-trace 仓目录下跑或设置 `PYTHONPATH`。

---

## 执行步骤

以下命令均在仓库根执行，或 `cd tests` 后用相对路径。不要写本机绝对路径。

### Step 1：故障测试（本地，无需本仓服务端）

```bash
cd tests

python3 test_fault.py
python3 test_fault.py --test compress   # 压缩与 hash
python3 test_fault.py --test queue      # 队列持久化
python3 test_fault.py --test health     # 健康检查
python3 test_fault.py --test enqueue    # 服务端不可达时入队
python3 test_fault.py --test cleanup    # .uploaded 标记与清理
python3 test_fault.py --test retry      # 指数退避重试
python3 test_fault.py --test atomic     # 原子写入
```

**预期：** 全部通过（7 项）。

---

### Step 2：端到端测试

```bash
cd tests
python3 test_e2e.py
# 或
python3 test_e2e.py --url http://<your-host>/traj
```

**覆盖：** 健康检查 → gzip 上传 traj/raw/events → SHA256 → 幂等 409 → 列表/详情 → 标注 → 软删除。

**失败时：**

- 服务端日志：生产机 `tail -50 /opt/trajectory-platform/backend/app.log`（路径与开源仓名分叉是有意的）
- 存储配置：`STORAGE_BACKEND` 等，见 `backend/.env`（不要把真值贴进文档）
- DB：`psql -U trajuser -d trajdb -c "SELECT COUNT(*) FROM trajectories;"`

---

### Step 3：并发测试

```bash
cd tests
python3 test_concurrent.py
python3 test_concurrent.py --concurrency 100 --sessions 200
python3 test_concurrent.py --url http://<your-host>/traj
```

**预期：** 成功率 100%、DB 入库 100%。吞吐量取决于机器。

**失败时：** PostgreSQL `max_connections`、脚本 timeout、服务端存储写入。

---

### Step 4：前端 API 回归

```bash
cd tests
python3 test_frontend_api.py
```

**失败时：** 浏览器打开 `http://127.0.0.1:5173`（开发）或 `http://<your-host>/traj`（生产）；核对 CORS 与 nginx `/traj/`。

---

### Step 5：对账验证

```bash
cd tests
python3 test_audit.py
python3 test_audit.py --sample-size 50
python3 test_audit.py --test migration --sqlite /path/to/old/trajectories.db
python3 test_audit.py --test db-storage
python3 test_audit.py --test fields
python3 test_audit.py --test stats
```

**失败时：** DB 多于存储 → 查采集端上传队列；存储多于 DB → 查 traj 解析日志；迁移不一致 → 查迁移脚本。

---

## 服务端对账 cron

`deploy/audit.sh` 建议每天跑一次：

```bash
# 在服务器上（先用你自己的 SSH 登录，不要把公网 IP 写进默认命令）
crontab -e
# 0 5 * * * /opt/trajectory-platform/deploy/audit.sh >> /var/log/traj-audit.log 2>&1
```

---

## 故障排查

### 上传 401 / 403

token 不匹配或采集端未启动。核对 `TRAJ_UPLOAD_TOKEN` 与服务端 `UPLOAD_TOKEN`。采集端状态在 claude-trace 仓：`./install-daemon.sh status`。

### SHA256 不匹配

```bash
gzip -c test.traj | sha256sum
```

### 并发大量超时

```bash
psql -U trajuser -d trajdb -c "SELECT COUNT(*) FROM pg_stat_activity;"
```

### 队列持续增长

采集端 `~/.claude-trace/.upload_queue.jsonl`。本仓不持有这份文件。

---

## 灰度建议

1. 采集端 `TRAJ_CLEANUP_AFTER_UPLOAD=false`，先留本地副本
2. 盯上传队列，确认无持续失败
3. 每天跑 `test_audit.py`
4. 前端抽查最新轨迹详情
5. 稳定后再开自动清理

---

## 一键（需要活服务端 + 上面的环境变量）

```bash
cd tests

echo "=== 1. 故障测试 ==="
python3 test_fault.py || exit 1

echo -e "\n=== 2. 端到端测试 ==="
python3 test_e2e.py || exit 1

echo -e "\n=== 3. 并发测试（轻量）==="
python3 test_concurrent.py --concurrency 20 --sessions 20 || exit 1

echo -e "\n=== 4. 前端 API 回归 ==="
python3 test_frontend_api.py || exit 1

echo -e "\n=== 5. 对账验证 ==="
python3 test_audit.py --sample-size 10 || exit 1

echo -e "\n全部测试通过"
```

---

## 测试数据清理

脚本会软删除自己创建的 session。手动清：

```bash
curl -s "${TRAJ_PLATFORM_URL:-http://127.0.0.1:8900}/api/v1/trajectories?search=test-" \
  -u admin:your-password | python3 -m json.tool
```

`admin:your-password` 是占位符，换成你的 `TRAJ_AUTH_USER` / `TRAJ_AUTH_PASS`。

---

## 反馈时请带

1. 脚本完整输出
2. 服务端日志（生产机 `/opt/trajectory-platform/backend/app.log`）
3. 采集端日志（claude-trace 侧）
4. Python / OS 版本，不要贴密钥
