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
│   ├── app/
│   │   ├── main.py         # 入口
│   │   ├── config.py       # 配置（含 OSS/PG 配置项、is_oss/is_sqlite 属性）
│   │   ├── database.py     # 数据库引擎（兼容 SQLite 和 PostgreSQL）
│   │   ├── models.py       # ORM 模型（含 oss_key, sha256, user_id 等字段）
│   │   ├── schemas.py      # Pydantic 模型
│   │   ├── routers/
│   │   │   ├── upload.py       # 上传 API（SHA256 校验 + 存储抽象）
│   │   │   └── trajectories.py # 列表/详情/标注/软删除 API
│   │   ├── services/
│   │   │   ├── traj_parser.py  # .traj 文件解析
│   │   │   └── storage.py      # 存储抽象层（LocalStorage / OSSStorage + LRU 缓存）
│   │   └── utils/
│   │       └── auth.py     # Basic Auth + Upload Token
│   └── requirements.txt
├── frontend/               # React 前端
│   ├── src/
│   │   ├── App.tsx         # 根组件 + 路由
│   │   ├── pages/          # 页面（TrajectoryList, TrajectoryDetail）
│   │   ├── components/     # 组件（Timeline, ToolCallBlock, ThinkingBlock）
│   │   ├── services/       # API 客户端（api.ts）
│   │   └── types/          # 类型定义（trajectory.ts）
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
├── docs/                   # 文档
│   ├── deployment.md           # 部署文档（访问地址、服务管理、日常操作）
│   └── data-reliability-plan.md # 数据可靠性方案
└── sync.py                 # 本地 → 云端增量同步脚本
```

## 架构要点

- **存储后端抽象**：`services/storage.py` 定义了 `StorageBackend` 协议，`LocalStorage`（本地文件）和 `OSSStorage`（阿里云 OSS + LRU 缓存）两个实现，通过 `STORAGE_BACKEND` 环境变量切换
- **数据库兼容**：`database.py` 根据 `DATABASE_URL` 前缀自动选择驱动，SQLite 模式自动执行 WAL pragma 和增量列迁移
- **软删除**：DELETE 接口设置 `deleted_at` 时间戳，所有查询自动过滤 `deleted_at IS NULL`，30 天后由 cron 任务真正清理 OSS 文件和 DB 记录
- **上传校验**：客户端可传 `X-Content-SHA256` 头，服务端计算并比对，不一致返回 400
- **文件格式兼容**：读取时自动尝试 `.gz` 和非 `.gz` 格式，兼容迁移前的旧数据

## 编码规范

- 所有代码注释必须使用中文
- 后端文件命名使用 snake_case
- 前端组件命名使用 PascalCase
- 前端包管理器使用 pnpm，不要用 npm 或 yarn
- 数据交换统一使用 JSON 格式
- 不要生成零散的文档文件，文档集中在 docs/ 目录

## 部署信息

详见 [docs/deployment.md](docs/deployment.md)，包含：
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

# 部署后端到服务器
export SSHPASS='18752006620@zRs'
sshpass -e rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '.env' --exclude '*.pyc' \
  -e "ssh -o StrictHostKeyChecking=no" \
  ~/Code/person/trajectory-platform/backend/ \
  root@127.0.0.1:/opt/trajectory-platform/backend/
sshpass -e ssh -o StrictHostKeyChecking=no root@127.0.0.1 \
  'systemctl restart trajectory-platform'
```
