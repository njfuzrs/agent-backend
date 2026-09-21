# Agent Backend

企业级 Agent 后端。

sid-code 与 claude-trace 共同面对的企业级后端：控制面（policy / flag / 身份）与数据面（轨迹 / 事件）同仓部署、鉴权隔离。当前已交付轨迹存储与分析、设备身份两个模块，其余按里程碑迭代。

> GitHub 仓名 `agent-backend`。生产部署路径仍是 `/opt/trajectory-platform`、nginx 前缀仍是 `/traj/` —— **与开源仓名分叉是有意的**，采集端 URL 已对外冻结，本轮不切流。

## 现在有什么 / 还没有什么

**有（已交付）**

- 轨迹上传冻结区：`POST /api/v1/upload/session-file`、`GET /api/v1/health`（sid-code / claude-trace 线上在用，改了就断采集）
- 浏览 / 搜索 / 标注前端（轨迹、对比、仪表盘）
- 双平面鉴权：数据面走 `X-Upload-Token` / Basic Auth / 管理台 cookie；控制面走设备凭据 `Authorization: Bearer`，两条链互不引用
- 设备身份：一次性注册码换凭据（`POST /api/v1/ctl/enroll`），`require_device` 查 hash、拒吊销/过期

**没有（路线图，不要当成已交付）**

- Flag 下发
- 远程 Policy
- 组织事件接收
- 成本账本对账
- 生产 TLS / 登录体系（管理台目前是共享口令 + cookie；设备身份是可注入，不是 SSO）

把未做的写成路线图，是为了开源定位对准「企业后端」，而不是把骨架写成已经齐活的控制平台。

## 模块

| 模块 | 目录 | 状态 |
| --- | --- | --- |
| 轨迹（模块一） | `backend/app/modules/trajectory/`、`frontend/src/modules/trajectory/` | 已运行 |
| identity | `backend/app/modules/identity/`、`frontend/src/modules/identity/` | M1 已交付 |
| flag | 规划中 | M2 |
| policy | 规划中 | M3 |
| event | 规划中 | M4 |
| cost | 规划中 | M5 |

代码目录本仓暂不预建空模块。控制面 / 数据面是平面名，不是产品名。

## 怎么跑（本地）

复制 [`.env.example`](.env.example) 为 `backend/.env`，填入 `AUTH_PASSWORD` 与 `UPLOAD_TOKEN`。缺这两项进程会 `SystemExit`，这是故意的。

```bash
# 后端（SQLite + 本地文件，无需 OSS / PostgreSQL）
cd backend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8900

# 前端（开发代理 /api → 127.0.0.1:8900）
cd frontend && pnpm install && pnpm dev
```

管理台默认走 `http://127.0.0.1:5173`（Vite）。生产经 nginx 挂在 `/traj/`。

门禁：

```bash
cd backend && ruff check . && alembic check
python -m pytest ../tests/test_boundaries.py ../tests/test_identity.py -v
```

`tests/test_e2e.py` 等五份是对着活服务端的手动验收，不进 CI。

## 上传协议

本仓是接收端。采集端协议以 [claude-trace `docs/upload-protocol.md`](https://github.com/njfuzrs/claude-trace/blob/main/docs/upload-protocol.md) 为准：gzip + `X-Content-SHA256` + `X-Upload-Token` + multipart。上传默认关闭，采集端必须同时配置 URL 与 token 才会外发。

服务侧补传见 `scripts/backfill_sid_code.sh`：`TRAJ_UPLOAD_URL` 无默认值（opt-in）。示例：

```bash
export TRAJ_UPLOAD_URL=https://www.sid-code.cc/traj   # 或你的自建端点
export TRAJ_UPLOAD_TOKEN=...
bash scripts/backfill_sid_code.sh --dry-run
```

## 部署

生产目录、systemd unit 文件名、nginx `/traj/` **保持原样**（`/opt/trajectory-platform`、`trajectory-platform.service`）。开源仓改名不等于服务器改名。运维脚本在 `deploy/`，目标主机必须显式设置 `TRAJ_REMOTE_HOST`，仓库里没有公网 IP 默认值。

## 路线图

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 工程地基（模块化、Alembic、双平面鉴权骨架） | 已合入 |
| M1 | 设备身份 / `require_device` 从 501 变成真鉴权 | 已合入 |
| M2 | Flag 下发 | 未开工 |
| M3 | 远程 Policy（硬准入：生产 TLS） | 未开工 |
| M4 | 组织事件 | 未开工 |
| M5 | 成本账本 | 未开工 |
| M6 | 分发 / 遥控 | 按需，可后置 |

## 许可

[MIT](LICENSE)。贡献指南见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全上报见 [SECURITY.md](SECURITY.md)。
