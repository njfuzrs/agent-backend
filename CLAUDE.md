# CLAUDE.md

## 强制约束

所有对话和回答必须使用中文

## 项目概述

trajectory-platform 是 Agent 轨迹数据存储分析平台，用于云端存储、浏览、搜索、标注 Claude Code 等工具的 .traj 轨迹数据。

## 关联项目：claude-trace（采集工具）

本平台的数据来源于 [claude-trace](../claude-trace/) 采集工具，位于 `~/Code/person/claude-trace/`。

- 采集工具路径：`~/Code/person/claude-trace/`
- 本地轨迹数据：`~/Code/person/claude-trace/trajectories/sessions/`
  - 按会话维度存储：`sessions/{session_id}/session.traj`、`raw.jsonl`、`events.jsonl`、`raw/`
- 采集方式：HTTP 代理（proxy.py）+ Claude Hooks（collector.py）双通道
- 数据流向：`claude-trace 采集 → sessions/{sid}/ → 会话结束自动上传 / sync.py 批量同步 → trajectory-platform 云端存储`

## 技术栈

- 后端：Python 3 + FastAPI + SQLAlchemy + SQLite（WAL 模式） + aiosqlite
- 前端：React + TypeScript + Vite + Ant Design + TanStack Query + pnpm
- 部署：Nginx 反代 + systemd 服务 + 阿里云 ECS

## 项目结构

```
trajectory-platform/
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── main.py         # 入口
│   │   ├── config.py       # 配置
│   │   ├── database.py     # 数据库引擎
│   │   ├── models.py       # ORM 模型
│   │   ├── schemas.py      # Pydantic 模型
│   │   ├── routers/        # API 路由（upload.py, trajectories.py）
│   │   ├── services/       # 业务逻辑（traj_parser.py）
│   │   └── utils/          # 工具函数（auth.py）
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
├── deploy/                 # 部署配置
├── docs/                   # 文档
│   └── deployment.md       # 部署文档（访问地址、服务管理、日常操作）
└── sync.py                 # 本地 → 云端增量同步脚本
```

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
- 服务器环境与目录结构
- 服务管理命令（systemd / Nginx）
- 日常操作（上传数据 / 更新代码 / 备份）
- API 速查表
- 故障排查指南

## 常用命令

```bash
# 本地开发 — 后端
cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8900

# 本地开发 — 前端（自动代理 /api → localhost:8900）
cd frontend && pnpm dev

# 同步轨迹数据到云端
python3 sync.py          # 增量
python3 sync.py --all    # 全量

# 构建前端
cd frontend && pnpm run build
```
