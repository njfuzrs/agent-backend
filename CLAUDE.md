# CLAUDE.md

## 强制约束

所有对话和回答必须使用中文

## 项目概述

本仓是 **Agent Backend**（企业级 Agent 后端）：sid-code 与 claude-trace 共同面对的服务端，控制面（policy / flag / 身份）与数据面（轨迹 / 事件）同仓部署、鉴权隔离。

轨迹存储与分析是已经落地的**第一个模块**（`modules/trajectory/`），身份是第二个（`modules/identity/`），flag 是第三个（`modules/flag/`），policy 是第四个（`modules/policy/`）。event / cost 按里程碑迭代，目录可以先不建。

GitHub 目标仓名 `njfuzrs/agent-backend`。生产路径 `/opt/trajectory-platform`、nginx 前缀 `/traj/`、unit 文件名 `trajectory-platform.service` **故意不改**（采集 URL 已对外冻结）。

## 关联项目：claude-trace（采集工具）

数据面轨迹来源于 [claude-trace](https://github.com/njfuzrs/claude-trace) 采集工具。

- 采集方式：HTTP 代理（proxy.py）+ Claude Hooks（collector.py）双通道
- 数据流向：`claude-trace 采集 → sessions/{sid}/ → uploader 自动上传 / sync.py 批量补传（均在 claude-trace 仓）→ 本仓云端存储`
- 上传协议：[claude-trace `docs/upload-protocol.md`](https://github.com/njfuzrs/claude-trace/blob/main/docs/upload-protocol.md)
- 分析侧入湖：公开仓 agent-traj-bench 的 `pipeline/s0/s0-pull.py`（云端 → 本地 `data/pulled_sessions/`，gitignore）

## 技术栈

- 后端：Python 3 + FastAPI + SQLAlchemy + PostgreSQL（服务器）/ SQLite（本地开发）
- 存储：对象存储（服务器）/ 本地文件系统（本地开发），通过 `STORAGE_BACKEND` 环境变量切换
- 前端：React + TypeScript + Vite + Ant Design + TanStack Query + pnpm
- 部署：Nginx 反代 + systemd
- 依赖：oss2（OSS SDK）、asyncpg（PG 异步驱动）、aiosqlite（SQLite 异步驱动）

## 项目结构

```
agent-backend/
├── backend/                # FastAPI 后端
│   ├── alembic.ini         # 迁移配置（DATABASE_URL 从 app.core.config 读，不在此重复）
│   ├── migrations/         # Alembic 迁移链，schema 演进的唯一入口
│   │   ├── env.py
│   │   └── versions/0001_baseline_4_tables.py
│   ├── pyproject.toml      # ruff / pytest 配置
│   ├── app/
│   │   ├── main.py         # 只做装配：CORS + 路由注册 + 启动时 schema 版本检查
│   │   ├── core/           # 平台内核
│   │   │   ├── config.py       # 分段配置（DataPlane/ControlPlane/Storage）
│   │   │   ├── db.py           # 引擎 + session（不再建表，schema 归 Alembic）
│   │   │   ├── auth/
│   │   │   │   ├── data_plane.py     # Basic Auth + Upload Token（含冻结区鉴权）
│   │   │   │   ├── control_plane.py  # require_device：Bearer 设备凭据
│   │   │   │   └── session.py        # 管理台 HttpOnly cookie 会话
│   │   │   └── router/auth.py   # /auth/login、/logout、/me
│   │   └── modules/        # 业务模块 = 一组内聚的表 + 一个路由前缀 + 一条鉴权链
│   │       ├── trajectory/     # 模块一：轨迹存储与分析（已交付）
│   │       │   ├── model.py    # ORM 模型（trajectories / tool_steps）
│   │       │   ├── schemas.py  # Pydantic 模型
│   │       │   ├── router/     # upload / trajectories / stats / export
│   │       │   └── service/    # storage / traj_parser / tool_steps / stats_service
│   │       ├── identity/       # 模块二：设备注册与凭据（M1）
│   │       │   ├── model.py    # organizations / teams / devices / device_credentials / enroll_codes
│   │       │   ├── schemas.py
│   │       │   ├── router/     # enroll / whoami / admin
│   │       │   └── service/    # enroll / admin / secrets
│   │       ├── flag/           # 模块三：Feature Flag 下发（M2）
│   │       │   ├── model.py    # feature_flags / feature_flag_audit
│   │       │   ├── schemas.py
│   │       │   ├── router/     # flags（下发）/ admin
│   │       │   └── service/    # flags / guard
│   │       ├── policy/         # 模块四：策略下发（M3）
│   │       │   ├── model.py    # policies / policy_audit
│   │       │   ├── schemas.py
│   │       │   ├── router/     # serve（GET /ctl/policy）/ admin
│   │       │   └── service/    # policies / guard
│   │       ├── event/          # 模块五：事件上报与审计（M4）
│   │       │   ├── model.py    # events / event_rejects
│   │       │   ├── schemas.py
│   │       │   ├── router/     # ingest（POST /events）/ admin
│   │       │   └── service/    # ingest / queries / guard
│   │       # 规划中、目录尚未建：cost
│   └── requirements.txt
├── frontend/               # React 前端
│   ├── src/
│   │   ├── App.tsx         # 外壳 + 导航 + 登录守卫（产品名 Agent Backend）
│   │   ├── modules/trajectory/   # 与后端同构分模块
│   │   │   ├── pages/       # TrajectoryList / TrajectoryDetail / Dashboard
│   │   │   ├── components/  # Timeline, ToolCallBlock, ThinkingBlock ...
│   │   │   ├── services/    # api.ts（cookie 会话，凭据不进 localStorage）
│   │   │   └── types/       # trajectory.ts
│   │   ├── modules/identity/     # 设备列表 / 一次性注册码
│   │   ├── modules/flag/         # Feature Flag 列表
│   │   ├── modules/policy/       # 策略列表（device/team/org）
│   │   └── utils/          # 跨模块工具（format / chart / trajectoryDetail）
│   └── vite.config.ts      # base: '/traj/'（冻结区，不要改）
├── data/                   # 数据目录（.gitignore）
├── deploy/                 # 部署配置与运维脚本
│   ├── remote_setup.sh         # 一键部署脚本（首次装机入口）
│   ├── trajectory-platform.service  # systemd unit（文件名故意不改）
│   ├── nginx.conf              # Nginx 配置参考
│   ├── pg_env.sh               # 被 source 的公共库：resolve_root / load_pg_from_env / psql_q / psql_v
│   ├── migrate_to_pg.py        # SQLite → PG 数据迁移
│   ├── migrate_to_oss.sh       # 本地文件 → OSS 迁移（一次性，2026-03 已执行完）
│   ├── release.sh              # 生产切换唯一入口（服务器上跑；读 AGENT_BACKEND_ROOT；成功后快照到 releases/<sha>）
│   ├── rollback.sh             # 退到 releases/<sha> 的代码快照（只换 app/ 与 dist/，绝不 alembic downgrade）
│   ├── push_code.sh            # 本机入口：build + rsync 暂存 + ssh release.sh
│   ├── backup_pg.sh            # PG 备份到 OSS（cron 03:00；从 .env 读密码）
│   ├── audit.sh                # DB vs OSS 每日对账（cron 05:00）
│   ├── cleanup_deleted.sh      # 软删除 30 天后真删（cron 06:00）
│   ├── migrate.sh              # 生产库 schema 演进入口（current/check/stamp/plan/upgrade）
│   ├── backup.sh               # ⚠️ 已废弃（SQLite 时代），执行即 exit 1 → 用 backup_pg.sh
│   ├── setup.sh                # ⚠️ 已废弃（会建 traj_files/、给 backup.sh 装 cron）→ 用 remote_setup.sh
│   └── rsync_sync.sh           # ⚠️ 已废弃（OSS 下 rsync+reindex 双空转）→ 用 claude-trace sync.py
├── scripts/                # 服务侧运维脚本（现仅 backfill_sid_code.sh）
└── tests/                  # test_boundaries.py 为门禁核心；其余 5 个脚本为手动验收工具
```

## 架构要点

- **存储后端抽象**：`modules/trajectory/service/storage.py` 定义了 `StorageBackend` 协议，`LocalStorage`（本地文件）和 `OSSStorage`（对象存储 + LRU 缓存）两个实现，通过 `STORAGE_BACKEND` 环境变量切换
- **数据库兼容**：`core/db.py` 根据 `DATABASE_URL` 前缀自动选择驱动，SQLite 模式自动执行 WAL pragma
- **运维脚本的 PG 凭据只有一条路**：`deploy/pg_env.sh` 的 `load_pg_from_env` 从 `$ROOT/.env` 的 `DATABASE_URL` 解析并走 TCP。不要在脚本里写 `psql -U trajuser`——不带密码会走 unix socket 撞 pg_hba 的 `local all all peer`，每天 cron 必然 FATAL（audit 曾因此报出 `差异: -9574` 假警报，cleanup 则从未成功清理过一条）。由 `tests/test_deploy_scripts.py` 的 `test_pg_scripts_never_use_peer_auth` 拦截
- **`psql_v` 必须走 stdin**：`psql -c` 不对 `:'name'` 做变量插值（生产 14.24 实测 `syntax error at or near ":"`）；stdin 脚本模式遇 SQL 错误默认仍返回 0，必须带 `-v ON_ERROR_STOP=1`，否则 `cleanup_deleted.sh` 的 `if ! psql_v` 会把删除失败当成功。由 `test_psql_v_*` 拦截
- **shell 里变量紧跟中文标点必须写 `${name}`**：`$var（` 在 UTF-8 locale 下 bash 会把全角字符首字节并进变量名，`set -u` 下直接 `unbound variable`；`LANG=C` 侥幸能跑，所以本地测不出来。由 `test_no_var_glued_to_fullwidth_char` 全仓扫描
- **schema 演进只有一条路**：`alembic upgrade head`。原 `init_db()` 的 `create_all` + `_migrate_sqlite_columns()` 已删除 —— 前者不改已有表的列，后者被 `is_sqlite` 挡住（生产是 PG，等于生产无加列路径）。运行时代码不得建表，由边界测试拦截
- **双平面鉴权隔离**：数据面（`verify_upload_token` / `verify_basic_auth`）与控制面（`require_device`）两条依赖链互不引用。控制面被打穿等于全体客户端护栏被关，所以不与数据面共用凭据。`/ctl/` 端点必须挂 `require_device`（签发入口 `/ctl/enroll` 除外走一次性注册码；`GET /ctl/flags` 是客户端裸 fetch 的有意豁免）。`GET /ctl/policy` **必须**挂 `require_device`，不要加进豁免名单。`POST /api/v1/events` 数据面方向、控制面鉴权，**不在 `/ctl/` 下**，现有门禁 ② 扫不到，由 `test_events_ingest_requires_device` 专门盯。由边界测试反射检查
- **管理台凭据不落 localStorage**：独立登录页走 `/api/v1/auth/login` 下发 HttpOnly + SameSite cookie（无状态 HMAC 签名，跨 worker 有效）。未登录或 401 跳 `/login`。Basic Auth 保留给脚本与 curl
- **软删除**：DELETE 接口设置 `deleted_at` 时间戳，所有查询自动过滤 `deleted_at IS NULL`，30 天后由 cron 任务真正清理对象存储文件和 DB 记录
- **上传校验**：客户端可传 `X-Content-SHA256` 头，服务端计算并比对，不一致返回 400
- **文件格式兼容**：读取时自动尝试 `.gz` 和非 `.gz` 格式，兼容迁移前的旧数据

## 编码规范

- 所有代码注释必须使用中文
- 后端文件命名使用 snake_case
- 前端组件命名使用 PascalCase
- 前端包管理器使用 pnpm，不要用 npm 或 yarn
- 数据交换统一使用 JSON 格式
- 不要生成零散的文档文件。设计文档不在本仓

## 部署信息

生产部署路径、凭据位置、systemd / Nginx / 备份对账命令写在运维侧，不进本仓。本仓只保留 `deploy/` 脚本与 `.env.example` 占位符。

## 常用命令

```bash
# 本地开发 — 后端（SQLite + 本地文件模式，无需 OSS/PG）
# 先复制 .env.example 为 backend/.env 并填写 AUTH_PASSWORD / UPLOAD_TOKEN
cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8900

# 本地开发 — 前端（自动代理 /api → localhost:8900）
cd frontend && pnpm dev

# 构建前端
cd frontend && pnpm run build

# 门禁自查（提交前跑一遍）
cd backend && ruff check . && alembic check          # lint + schema 漂移
python -m pytest ../tests/test_boundaries.py ../tests/test_identity.py ../tests/test_flag.py ../tests/test_policy.py ../tests/test_event.py ../tests/test_deploy_scripts.py -v  # 边界 + 身份 + flag + policy + event + 发版脚本

# 数据库迁移
cd backend && alembic upgrade head                   # 本地：建库/升级
cd backend && alembic revision --autogenerate -m '描述'  # 改完 model 后生成迁移
deploy/migrate.sh plan                               # 生产：先干跑看 SQL
deploy/migrate.sh upgrade                            # 生产：自动备份后执行

# 部署后端到服务器（目标主机必须显式设置，仓库无公网 IP 默认值）
#   export TRAJ_REMOTE_HOST=<host>
#   export TRAJ_SSH_KEY=~/.ssh/xxx    # 或设置 SSHPASS（sshpass 接口变量名，不是密码本身）
bash deploy/push_code.sh    # 本机热修：构建前端 → 暂存 → 远端 release.sh
# 合入 main 且 CI 绿后 GitHub Actions「Deploy」自动发；紧急用 workflow_dispatch

# 回滚（服务器上跑；GitHub 侧是 Deploy → workflow_dispatch 填 to_sha）
deploy/rollback.sh --list        # 看还留着哪些快照（release.sh 保留最近 5 份）
deploy/rollback.sh <sha>         # 退到那一份；不带参数则退到 .deploy-sha.prev
# 只换代码不动库。schema 已前进时会拒绝 —— 正路是 forward fix，或先恢复
# trajdb_pre_<sha>_*.sql.gz 再退。任何情况都不跑 alembic downgrade
```
