# CLAUDE.md

## 强制约束

所有对话和回答必须使用中文

## 项目概述

trajectory-platform 是 Agent 轨迹数据存储分析平台，用于云端存储、浏览、搜索、标注 Claude Code 等工具的 .traj 轨迹数据。

## 关联项目：claude-trace（采集工具）

本平台的数据来源于 [claude-trace](../claude-trace/) 采集工具，位于 `~/Code/person/claude-trace/`。

- 采集工具路径：`~/Code/person/claude-trace/`
- 本地轨迹数据：`~/Code/person/claude-trace/trajectories/sessions/`
  - 按会话维度存储：`sessions/{session_id}/session.traj`、`raw.jsonl`、`events.jsonl`
- 采集方式：HTTP 代理（proxy.py）+ Claude Hooks（collector.py）双通道
- 数据流向：`claude-trace 采集 → sessions/{sid}/ → 会话结束自动上传 / sync.py 批量同步 → trajectory-platform 云端存储`

## 技术栈

- 后端：Python 3 + FastAPI + SQLAlchemy + PostgreSQL（服务器）/ SQLite（本地开发）
- 存储：阿里云 OSS（服务器）/ 本地文件系统（本地开发），通过 `STORAGE_BACKEND` 环境变量切换
- 前端：React + TypeScript + Vite + Ant Design + TanStack Query + pnpm
- 部署：Nginx 反代 + systemd 服务 + 阿里云 ECS
- 依赖：oss2（OSS SDK）、asyncpg（PG 异步驱动）、aiosqlite（SQLite 异步驱动）

## 项目结构

```
trajectory-platform/
├── backend/                # FastAPI 后端
│   ├── alembic.ini         # 迁移配置（DATABASE_URL 从 app.core.config 读，不在此重复）
│   ├── migrations/         # Alembic 迁移链，schema 演进的唯一入口
│   │   ├── env.py
│   │   └── versions/0001_baseline_4_tables.py
│   ├── pyproject.toml      # ruff / pytest 配置
│   ├── app/
│   │   ├── main.py         # 只做装配：CORS + 路由注册 + 启动时 schema 版本检查
│   │   ├── core/           # 平台内核
│   │   │   ├── config.py       # 分段配置（DataPlane/ControlPlane/Storage/Scoring）
│   │   │   ├── db.py           # 引擎 + session（不再建表，schema 归 Alembic）
│   │   │   ├── auth/
│   │   │   │   ├── data_plane.py     # Basic Auth + Upload Token（含冻结区鉴权）
│   │   │   │   ├── control_plane.py  # require_device，M1 前抛 501
│   │   │   │   └── session.py        # 管理台 HttpOnly cookie 会话
│   │   │   └── router/auth.py   # /auth/login、/logout、/me
│   │   └── modules/        # 业务模块 = 一组内聚的表 + 一个路由前缀 + 一条鉴权链
│   │       └── trajectory/     # 原平台全部能力，现降为一个模块
│   │           ├── model.py    # ORM 模型（4 张表）
│   │           ├── schemas.py  # Pydantic 模型
│   │           ├── router/     # upload / trajectories / stats / compare / export / scoring
│   │           └── service/    # storage / traj_parser / tool_steps / stats_service / scoring
│   └── requirements.txt
├── frontend/               # React 前端
│   ├── src/
│   │   ├── App.tsx         # 外壳 + 导航 + 登录框
│   │   ├── modules/trajectory/   # 与后端同构分模块
│   │   │   ├── pages/       # TrajectoryList / TrajectoryDetail / Dashboard / Compare*
│   │   │   ├── components/  # Timeline, ToolCallBlock, ThinkingBlock ...
│   │   │   ├── services/    # api.ts（cookie 会话，凭据不进 localStorage）
│   │   │   └── types/       # trajectory.ts
│   │   └── utils/          # 跨模块工具（format / chart / trajectoryDetail）
│   └── vite.config.ts
├── data/                   # 数据目录（.gitignore）
├── deploy/                 # 部署配置与运维脚本
│   ├── remote_setup.sh         # 一键部署脚本
│   ├── trajectory-platform.service  # systemd 服务配置
│   ├── nginx.conf              # Nginx 配置参考
│   ├── migrate_to_pg.py        # SQLite → PG 数据迁移
│   ├── migrate_to_oss.sh       # 本地文件 → OSS 迁移
│   ├── backup_pg.sh            # PG 每日备份到 OSS（cron 03:00）
│   ├── audit.sh                # DB vs OSS 每日对账（cron 05:00）
│   └── cleanup_deleted.sh      # 软删除 30 天后真删（cron 06:00）
│   └── migrate.sh              # 生产库 schema 演进入口（current/check/stamp/plan/upgrade）
├── scripts/                # 服务侧运维脚本（现仅 backfill_sid_code.sh，清洗链路已迁出）
├── tests/                  # test_boundaries.py 为门禁核心；其余 5 个脚本为手动验收工具
├── pull.py                 # 云端 → 本地增量拉取
└── sync.py                 # 本地 → 云端增量同步脚本
```

## 架构要点

- **存储后端抽象**：`modules/trajectory/service/storage.py` 定义了 `StorageBackend` 协议，`LocalStorage`（本地文件）和 `OSSStorage`（阿里云 OSS + LRU 缓存）两个实现，通过 `STORAGE_BACKEND` 环境变量切换
- **数据库兼容**：`core/db.py` 根据 `DATABASE_URL` 前缀自动选择驱动，SQLite 模式自动执行 WAL pragma
- **schema 演进只有一条路**：`alembic upgrade head`。原 `init_db()` 的 `create_all` + `_migrate_sqlite_columns()` 已删除 —— 前者不改已有表的列，后者被 `is_sqlite` 挡住（生产是 PG，等于生产无加列路径）。运行时代码不得建表，由边界测试拦截
- **双平面鉴权隔离**：数据面（`verify_upload_token` / `verify_basic_auth`）与控制面（`require_device`）两条依赖链互不引用。控制面被打穿等于全体客户端护栏被关，所以不与数据面共用凭据。`/ctl/` 端点必须挂 `require_device`，由边界测试反射检查
- **管理台凭据不落 localStorage**：登录走 `/api/v1/auth/login` 下发 HttpOnly + SameSite cookie（无状态 HMAC 签名，跨 worker 有效）。Basic Auth 保留给脚本与 curl
- **软删除**：DELETE 接口设置 `deleted_at` 时间戳，所有查询自动过滤 `deleted_at IS NULL`，30 天后由 cron 任务真正清理 OSS 文件和 DB 记录
- **上传校验**：客户端可传 `X-Content-SHA256` 头，服务端计算并比对，不一致返回 400
- **文件格式兼容**：读取时自动尝试 `.gz` 和非 `.gz` 格式，兼容迁移前的旧数据

## 编码规范

- 所有代码注释必须使用中文
- 后端文件命名使用 snake_case
- 前端组件命名使用 PascalCase
- 前端包管理器使用 pnpm，不要用 npm 或 yarn
- 数据交换统一使用 JSON 格式
- 不要生成零散的文档文件，文档集中在 ../docs-research/trajectory-platform/ 目录

## 部署信息

详见 [../docs-research/trajectory-platform/deployment.md](../docs-research/trajectory-platform/deployment.md)，包含：
- 访问地址与认证信息
- 服务器环境（PostgreSQL 14 + OSS + Nginx）
- 目录结构与 .env 配置
- 服务管理命令（systemd / Nginx / PostgreSQL / OSS）
- 日常操作（上传数据 / 更新代码 / 备份 / 对账）
- API 速查表（含 upload/session-file 完整参数）
- 数据库 Schema
- 故障排查指南
- 架构变更记录

## 常用命令

```bash
# 本地开发 — 后端（SQLite + 本地文件模式，无需 OSS/PG）
cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8900

# 本地开发 — 前端（自动代理 /api → localhost:8900）
cd frontend && pnpm dev

# 同步轨迹数据到云端
python3 sync.py          # 增量
python3 sync.py --all    # 全量

# 构建前端
cd frontend && pnpm run build

# 门禁自查（提交前跑一遍。GitLab CI 已随迁 GitHub 移除，这四条改为本地/GitHub Actions 执行）
cd backend && ruff check . && alembic check          # lint + schema 漂移
python -m pytest ../tests/test_boundaries.py -v      # 四条边界测试

# 数据库迁移
cd backend && alembic upgrade head                   # 本地：建库/升级
cd backend && alembic revision --autogenerate -m '描述'  # 改完 model 后生成迁移
deploy/migrate.sh plan                               # 生产：先干跑看 SQL
deploy/migrate.sh upgrade                            # 生产：自动备份后执行

# 部署后端到服务器（凭据从环境读，不写进文档）
#   export SSHPASS='<见服务器凭据保管处>'   或   export TRAJ_SSH_KEY=~/.ssh/xxx
bash deploy/push_code.sh    # 构建前端 → 同步 → 迁移 → 重启 → 健康检查
```
