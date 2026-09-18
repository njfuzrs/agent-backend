# Phase 3：验证与切换 — 测试执行指南

## 测试脚本清单

本表覆盖 `tests/` 下全部脚本。

| 脚本 | 用途 | 依赖 |
|------|------|------|
| `test_e2e.py` | 端到端测试：采集→上传→存储→查询全链路 | 服务端运行 |
| `test_concurrent.py` | 并发测试：多用户同时上传，验证无阻塞 | 服务端运行 |
| `test_fault.py` | 故障测试：SDK 内部逻辑（压缩、队列、重试） | 无需服务端 |
| `test_frontend_api.py` | 前端 API 回归：所有查询端点正常工作 | 服务端运行 |
| `test_audit.py` | 对账验证：DB vs 存储一致性 + 迁移验证 | 服务端运行 |

## 环境准备

### 1. 设置环境变量

```bash
# 服务端地址
export TRAJ_PLATFORM_URL="http://127.0.0.1/traj"

# 上传 token（与服务端 .env 中的 UPLOAD_TOKEN 一致）
export TRAJ_UPLOAD_TOKEN="your-upload-token"

# 认证信息（与服务端 .env 中的 AUTH_USERNAME/AUTH_PASSWORD 一致）
export TRAJ_AUTH_USER="admin"
export TRAJ_AUTH_PASS="your-password"
```

### 2. 确认服务端运行

```bash
curl -s http://127.0.0.1/traj/api/v1/health | python3 -m json.tool
# 应返回: {"status": "ok", "version": "1.0.0"}
```

### 3. 安装测试依赖（可选）

所有测试脚本只使用 Python 标准库，无需额外安装依赖。

`test_fault.py` 需要导入 `uploader.py`，确保在 `claude-trace` 目录下运行或设置 `PYTHONPATH`。

---

## 测试执行步骤

### Step 1：故障测试（本地，无需服务端）

验证 SDK 内部逻辑的正确性。

```bash
cd /Users/dev/Code/person/trajectory-platform/tests

# 运行全部故障测试
python3 test_fault.py

# 或单独运行某个测试
python3 test_fault.py --test compress   # 压缩与 hash
python3 test_fault.py --test queue      # 队列持久化
python3 test_fault.py --test health     # 健康检查
python3 test_fault.py --test enqueue    # 服务端不可达时入队
python3 test_fault.py --test cleanup    # .uploaded 标记与清理
python3 test_fault.py --test retry      # 指数退避重试
python3 test_fault.py --test atomic     # 原子写入
```

**预期结果：** 全部通过（7 项测试）

---

### Step 2：端到端测试

验证完整的数据流：采集 → 压缩上传 → 存储落盘 → DB 入库 → 前端查询。

```bash
cd /Users/dev/Code/person/trajectory-platform/tests

python3 test_e2e.py
```

**测试内容：**
1. 健康检查
2. 生成测试数据 + gzip 压缩上传（traj/raw/events 三个文件）
3. SHA256 校验（客户端 vs 服务端）
4. 幂等性测试（重复上传返回 409）
5. 列表查询（确认新 session 出现）
6. 详情查询（trajectory/history/info/raw-data/events 全部端点）
7. 标注更新
8. 软删除 + 验证不可见

**预期结果：** 全部通过（约 30+ 项检查）

**如果失败：**
- 检查服务端日志：`tail -50 /opt/trajectory-platform/backend/app.log`
- 检查存储后端配置：`cat /opt/trajectory-platform/backend/.env | grep STORAGE`
- 检查 DB 连接：`psql -U trajuser -d trajdb -c "SELECT COUNT(*) FROM trajectories;"`

---

### Step 3：并发测试

验证多用户同时上传时无阻塞、无数据丢失。

```bash
cd /Users/dev/Code/person/trajectory-platform/tests

# 默认 50 并发，50 个 session
python3 test_concurrent.py

# 压力测试：100 并发，200 个 session
python3 test_concurrent.py --concurrency 100 --sessions 200

# 指定服务端地址
python3 test_concurrent.py --url http://your-server/traj
```

**测试内容：**
1. 生成 N 个模拟 session
2. M 个线程并发上传（每个 session 3 个文件）
3. 统计成功率、耗时、吞吐量
4. 验证所有 session 都入库
5. 清理测试数据

**预期结果：**
- 成功率 100%（所有 session 上传成功）
- DB 入库 100%（所有 session 在 DB 中可查）
- 吞吐量 > 10 sessions/s（取决于服务器性能）

**如果失败：**
- PostgreSQL 连接数不足：修改 `postgresql.conf` 的 `max_connections`
- 超时：增加 `test_concurrent.py` 中的 `timeout` 参数
- 部分失败：检查服务端日志，可能是存储写入失败

---

### Step 4：前端 API 回归测试

验证所有前端依赖的 API 端点在改造后仍正常工作。

```bash
cd /Users/dev/Code/person/trajectory-platform/tests

python3 test_frontend_api.py
```

**测试内容：**
1. 健康检查
2. 上传非压缩格式（向后兼容）
3. 上传 gzip 压缩格式（新 SDK）
4. 列表查询（分页、过滤、排序、搜索）
5. 元数据详情
6. 详情分段加载（trajectory/history/info/raw/raw-data/events）
7. 标注更新
8. 软删除

**预期结果：** 全部通过（约 40+ 项检查）

**如果失败：**
- 检查前端是否能正常访问：打开浏览器访问 `http://127.0.0.1/traj`
- 检查 CORS 配置：`cat /opt/trajectory-platform/backend/.env | grep CORS`
- 检查 Nginx 配置：`cat /opt/trajectory-platform/deploy/nginx.conf`

---

### Step 5：对账验证

验证 DB 与存储的一致性，以及 SQLite → PG 迁移的完整性。

```bash
cd /Users/dev/Code/person/trajectory-platform/tests

# 基本对账（DB vs 存储）
python3 test_audit.py

# 增加抽样数量
python3 test_audit.py --sample-size 50

# 迁移验证（需要旧的 SQLite 文件）
python3 test_audit.py --test migration --sqlite /path/to/old/trajectories.db

# 只运行某个测试
python3 test_audit.py --test db-storage   # DB vs 存储对账
python3 test_audit.py --test fields       # 字段完整性
python3 test_audit.py --test stats        # 统计概览
```

**测试内容：**
1. DB vs 存储对账：DB 中每条记录在存储中都有对应文件
2. 字段完整性抽查：关键字段非空、数值合理
3. 迁移验证：SQLite 记录数 vs PG 记录数，字段值抽查
4. 统计概览：按 exit_status/model/quality_status 分布

**预期结果：**
- DB vs 存储一致性 100%
- 字段完整性无严重问题（允许少量警告）
- 迁移验证：记录数一致，字段值一致

**如果失败：**
- DB 记录多于存储文件：可能是上传失败，检查 `~/.claude-trace/.upload_queue.jsonl`
- 存储文件多于 DB 记录：可能是 traj 解析失败，检查服务端日志
- 迁移记录数不一致：检查迁移脚本日志，可能有重复 session_id 冲突

---

## 服务端对账脚本（定时任务）

服务端已有 `deploy/audit.sh` 脚本，建议配置 cron 定时执行：

```bash
# 在服务器上执行
ssh user@127.0.0.1

# 编辑 crontab
crontab -e

# 添加每日凌晨 5 点对账
0 5 * * * /opt/trajectory-platform/deploy/audit.sh >> /var/log/traj-audit.log 2>&1
```

查看对账日志：

```bash
tail -50 /var/log/traj-audit.log
```

---

## 故障排查

### 问题 1：上传失败（HTTP 403/401）

**原因：** token 不匹配或代理未启动

**排查：**
```bash
# 检查代理状态
cd /Users/dev/Code/person/claude-trace
./install-daemon.sh status

# 检查服务端 token
ssh user@127.0.0.1
cat /opt/trajectory-platform/backend/.env | grep UPLOAD_TOKEN
```

### 问题 2：SHA256 不匹配

**原因：** 网络传输损坏或压缩不一致

**排查：**
```bash
# 手动验证压缩文件 hash
gzip -c test.traj | sha256sum
```

### 问题 3：并发测试大量超时

**原因：** PostgreSQL 连接数不足或服务端性能瓶颈

**排查：**
```bash
# 检查 PG 连接数
ssh user@127.0.0.1
psql -U trajuser -d trajdb -c "SELECT COUNT(*) FROM pg_stat_activity;"

# 检查服务端负载
top
```

### 问题 4：队列文件持续增长

**原因：** 服务端长期不可达或上传持续失败

**排查：**
```bash
# 检查队列文件
cat ~/.claude-trace/.upload_queue.jsonl | wc -l

# 查看失败原因
cat ~/.claude-trace/.upload_queue.jsonl | jq '.error' | sort | uniq -c
```

---

## 灰度运行建议

测试全部通过后，建议灰度运行一周：

1. **保留本地数据**：设置 `TRAJ_CLEANUP_AFTER_UPLOAD=false`，上传成功后不删除本地文件
2. **监控队列**：每天检查 `~/.claude-trace/.upload_queue.jsonl`，确认无持续失败
3. **对账验证**：每天运行 `test_audit.py`，确认 DB vs 存储一致
4. **前端验证**：每天打开前端查看最新轨迹，确认详情页正常
5. **一周后**：如无问题，设置 `TRAJ_CLEANUP_AFTER_UPLOAD=true` 开启自动清理

---

## 快速验证命令

一键运行所有测试（需要服务端运行）：

```bash
cd /Users/dev/Code/person/trajectory-platform/tests

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

echo -e "\n✅ 全部测试通过！"
```

---

## 测试数据清理

测试脚本会自动清理测试数据（软删除），但如果需要手动清理：

```bash
# 查找所有测试 session
curl -s "http://127.0.0.1/traj/api/v1/trajectories?search=test-" \
  -u admin:your-password | jq '.items[].session_id'

# 批量删除（需要写脚本或手动逐个删除）
```

---

## 联系与反馈

如遇到问题或测试失败，请提供：
1. 测试脚本输出（完整日志）
2. 服务端日志：`/opt/trajectory-platform/backend/app.log`
3. 代理日志：`/tmp/claude-trace-proxy.log`
4. 环境信息：Python 版本、OS 版本、服务端配置
