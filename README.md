# Agent Backend

企业级 Agent 后端。

sid-code 与 claude-trace 共同面对的服务端：控制面（身份 / flag / policy / 预算 / 遥控 / 插件市场）与数据面（轨迹 / 事件 / 用量）同仓部署、鉴权隔离。

> GitHub 仓名 `agent-backend`。nginx 前缀仍是 `/traj/`、前端 `base` 仍是 `/traj/` —— **与仓名分叉是有意的**，采集端 URL 已对外冻结。

## 模块

| 模块 | 目录（后端 / 前端同名） | 里程碑 |
| --- | --- | --- |
| trajectory | `modules/trajectory/` | 模块一，已运行 |
| identity | `modules/identity/` | M1 |
| flag | `modules/flag/` | M2 |
| policy | `modules/policy/` | M3 |
| event | `modules/event/` | M4 |
| cost | `modules/cost/` | M5 |
| bridge | `modules/bridge/` | M6 |
| auth | `modules/auth/` | P1 / P2 |
| feishu | `modules/feishu/` | P4 |
| marketplace | `modules/marketplace/` | P5 / P2′ |

后端在 `backend/app/modules/`，前端在 `frontend/src/modules/`。每个模块 = 一组内聚的表 + 一个路由前缀 + 一条鉴权链。控制面 / 数据面是平面名，不是产品名。

## 能做什么

- **轨迹上传冻结区**：`POST /api/v1/upload/session-file`、`GET /api/v1/health`（sid-code / claude-trace 线上在用，改了就断采集）。另有 `GET /api/v1/ready` 就绪检查，不进冻结区
- **管理台**：轨迹浏览 / 搜索 / 标注、仪表盘、设备、Flag、策略、审计、用量与预算、遥控、用户、飞书授权、插件市场
- **双平面鉴权**：数据面走 `X-Upload-Token` / Basic Auth / 管理台 cookie；控制面走设备凭据 `Authorization: Bearer`，两条链互不引用
- **设备身份**：一次性注册码换凭据（`POST /api/v1/ctl/enroll`），`GET /api/v1/ctl/whoami` 回传续期后的过期时间
- **Flag 下发**：`GET /api/v1/ctl/flags`，详见下文
- **远程策略**：`GET /api/v1/ctl/policy` 挂设备凭据；未配置策略返回 204，客户端 fail-open 到本地 managed-settings
- **事件与用量**：`POST /api/v1/events`、`POST /api/v1/usage/ledger`，控制面鉴权；`user_ref` 入库时从设备凭据取，body 里的同名字段一律忽略
- **预算**：`GET /api/v1/ctl/budget` 下发，管理台按 scope / 按人看用量
- **遥控（bridge）**：`POST /api/v1/ctl/bridge/sessions` 签发配对，WebSocket 中继是**独立进程**（见下文部署）
- **管理台登录**：飞书 OAuth 优先（state + nonce + PKCE S256），共享口令作应急（`AUTH_PASSWORD_LOGIN_ENABLED`，每次口令登录写 `break_glass` 审计）。角色只有 admin / member
- **CLI 飞书登录**：`sid-code login` 走 `/auth/feishu/cli/start` → 一次性登录码 → `POST /auth/cli/exchange` 签设备凭据并绑人
- **委托授权**：配了 `TOKEN_ENC_KEY` 时飞书 token 加密落库；远程 MCP `POST /api/v1/ctl/feishu/mcp` 以员工本人权限只读文档 / 知识库，token 不出服务端，绝不降级到应用身份
- **插件市场**：管理台登记 → 上传 tar.gz（draft）→ 写理由发布 / 下架；设备端 `GET /api/v1/ctl/marketplace/index` 与 `/artifacts/{name}/{version}`。CI 可用 `mkt_…` 上传凭据传 draft，不能发布

## 怎么跑（本地）

复制 [`.env.example`](.env.example) 为 `backend/.env`，填入 `AUTH_PASSWORD` 与 `UPLOAD_TOKEN`。缺这两项进程会 `SystemExit`，这是故意的。飞书登录、委托授权、OSS、PostgreSQL、遥控中继都是可选项，各段注释写了怎么配。

```bash
# 后端（SQLite + 本地文件，无需 OSS / PostgreSQL）
cd backend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8900

# 遥控中继（可选，独立进程，只能 1 个 worker）
uvicorn app.modules.bridge.sidecar.main:app --port 8901

# 前端（开发代理 /api → 127.0.0.1:8900）
cd frontend && pnpm install && pnpm dev
```

管理台默认走 `http://127.0.0.1:5173`（Vite）。生产经 nginx 挂在 `/traj/`。

门禁（与 CI 一致）：

```bash
cd backend && ruff check . && alembic check
cd .. && python -m pytest tests/test_boundaries.py tests/test_auth_login.py tests/test_auth_cli.py \
  tests/test_feishu_delegation.py tests/test_marketplace.py tests/test_identity_ledger.py \
  tests/test_identity.py tests/test_flag.py tests/test_policy.py tests/test_event.py \
  tests/test_event_plugin_usage.py tests/test_cost.py tests/test_deploy_scripts.py tests/test_logging.py -v
cd frontend && pnpm exec tsc -b
```

`tests/test_e2e.py` 等对着活服务端的脚本是手动验收工具，不进 CI，说明见 [`tests/README.md`](tests/README.md)。

## 上传协议

本仓是接收端。采集端协议以 [claude-trace `docs/upload-protocol.md`](https://github.com/njfuzrs/claude-trace/blob/main/docs/upload-protocol.md) 为准：gzip + `X-Content-SHA256` + `X-Upload-Token` + multipart。上传默认关闭，采集端必须同时配置 URL 与 token 才会外发。

服务侧补传见 `scripts/backfill_sid_code.sh`：`TRAJ_UPLOAD_URL` 无默认值（opt-in）。示例：

```bash
export TRAJ_UPLOAD_URL=https://www.sid-code.cc/traj   # 或你的自建端点
export TRAJ_UPLOAD_TOKEN=...
bash scripts/backfill_sid_code.sh --dry-run
```

## Flag 下发

`GET /api/v1/ctl/flags` 返回扁平 JSON（`{"content_tracing": true, "max_turns_limit": 40}`），值是原生 JSON 类型。客户端全量替换本地缓存，所以**响应里没有某个 key = 这个 flag 已删，回落默认值** —— 停用与删除都靠这条语义生效。

这个端点**无需认证**，是客户端契约决定的：sid-code 的 `feature-flags.ts` 发的是裸 `fetch`，没有 `Authorization` 头，挂上鉴权会让它拿 401 后静默吞掉，表现为「功能全在、真实会话零生效」。代价用两道锁补回来：

1. 该路径只读，写口在 `/api/v1/flags/**`（管理台 cookie 会话）；
2. 写入时门禁拒掉放宽安全限制的 key/description（`bypass`、`disable_sandbox`、`disable_all_hooks` 等）。**Flag 只能施加约束**，放宽类开关归 Policy（设备凭据）。

key 必须是 `^[a-z][a-z0-9_]*$`：客户端用 `SID_CODE_FLAG_<KEY>` 做环境变量覆盖，非法标识符会让这条路径静默失效。每次变更都写审计（谁、什么时候、从什么改成什么、为什么），删除 flag 后审计记录保留。

## 部署

合入 `main` 且 CI 绿后，GitHub Actions `Deploy` 会自动发到 `https://www.sid-code.cc/traj/`。紧急重放：Actions → Deploy → `workflow_dispatch`（输入 SHA）。流水线只调服务器上的 `deploy/release.sh`，不改 nginx、不 `mv /opt`。本机热修可 `TRAJ_REMOTE_HOST=... bash deploy/push_code.sh`，与 CD 走同一份 `release.sh`。目标主机必须显式设置，仓库里没有公网 IP 默认值。

`release.sh` 的根目录读 `AGENT_BACKEND_ROOT`；未设时 `/opt/agent-backend` 存在就用它，否则用 `/opt/trajectory-platform`。systemd 同理优先 `agent-backend.service`，不存在再用 `trajectory-platform.service`。unit 参考件：

- `deploy/agent-backend.service.template`：主应用，`--workers 2`
- `deploy/agent-backend-bridge.service`：遥控中继，`--workers 1`、只绑 127.0.0.1:8901。配对是进程内存，**不要改成多 worker**，也不要把 WS 路由挂进主应用。中继 unit 不随发版覆盖，装机与升级要人手复制

生产库 schema 演进只有一条路：`deploy/migrate.sh plan` 先干跑看 SQL，`deploy/migrate.sh upgrade` 自动备份后执行。运行时代码不建表。

### 回滚

`release.sh` 每次发版成功后，把 `backend/app/` 与 `frontend/dist/` 快照到 `$ROOT/releases/<sha>/`，保留最近 5 份（`KEEP_RELEASES` 可调）。要退版：Actions → Deploy → `workflow_dispatch`，**`to_sha` 填要退到的 SHA**（填了它就只回滚，不发版、不构建）。服务器上也可以直接 `deploy/rollback.sh --list` 看有哪些快照、`deploy/rollback.sh <sha>` 退，不带参数则退到 `.deploy-sha.prev`。

回滚只换代码，**不动数据库**。快照记着当时的 alembic revision；如果之后跑过迁移，线上 schema 与快照不一致时 `rollback.sh` 会拒绝——因为退代码不退 DDL 可能让旧 ORM 读到不认识的表结构。这种情况下的正路是 forward fix（修好再发一个新 SHA），或者先用迁库前那份 `trajdb_pre_<sha>_*.sql.gz` 恢复库再回滚。`--force` 只在确认那次迁移是纯加表/加列（旧代码不碰）时才用。任何情况下都不跑 `alembic downgrade`。

## 路线图

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 工程地基（模块化、Alembic、双平面鉴权骨架） | 已合入 |
| M1 | 设备身份 / `require_device` | 已合入 |
| M2 | Flag 下发 | 已合入 |
| M3 | 远程 Policy | 已合入 |
| M4 | 组织事件与审计 | 已合入 |
| M5 | 用量账本与预算 | 已合入 |
| M6 | 遥控中继（bridge） | 已合入 |
| P1–P5 | 飞书登录、CLI 登录、身份落账、委托授权、插件市场 | 已合入 |
| P2′ | 插件市场 CI 上传凭据 | 已合入 |

## 许可

[MIT](LICENSE)。贡献指南见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全上报见 [SECURITY.md](SECURITY.md)。
