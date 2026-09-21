# 贡献指南

先说几件容易被当成笔误、其实是有意决策的事，免得你按常规改法提了 PR 又被要求改回去。

面向 AI agent 的仓库约定在 [CLAUDE.md](./CLAUDE.md)，两份文件受众不同但规则一致。

## 许可

本仓库的 [LICENSE](./LICENSE) 是 **MIT**，你的贡献将按同一许可分发。没有 CLA，提 PR 即视为同意按该许可授权。

## 几条有意的项目约定

### 1. 代码注释一律用中文

这不是笔误，是项目约定（见 `CLAUDE.md` 的编码规范）。新增代码请继续用中文注释。
标识符、日志 key、commit message 的类型前缀用英文，注释与文档正文用中文。
请**不要**提交「把注释改成英文以保持国际化」的 PR。

### 2. 前端包管理器只用 pnpm

不要用 npm 或 yarn。锁文件是 `frontend/pnpm-lock.yaml`。用另外两个工具会另写一份锁，CI 的 `--frozen-lockfile` 对不上。

### 3. 生产路径与开源仓名分叉是有意的

GitHub 仓是 `agent-backend`，线上仍是 `/opt/trajectory-platform`、systemd unit `trajectory-platform.service`、nginx 前缀 `/traj/`。
采集 URL 已对外冻结（sid-code / claude-trace 线上在用），本仓不靠改目录名来「对齐」。
请**不要**提交只为把路径改成 `/opt/agent-backend` 或把 `vite.base` 改离 `/traj/` 的 PR。

### 4. 双平面鉴权隔离

数据面（`verify_upload_token` / `verify_basic_auth`）与控制面（`require_device`）两条依赖链互不引用。
`/ctl/` 必须挂 `require_device`（签发入口 `/ctl/enroll` 除外，走一次性注册码）。控制面被打穿等于全体客户端护栏被关，所以不与数据面共用凭据。
这条由 `tests/test_boundaries.py` 锁定，破坏它的 PR 不会被合并。细节见 [SECURITY.md](./SECURITY.md)。

### 5. schema 只有一条路

`alembic upgrade head`。运行时代码不得建表、不得 `create_all`。改了 `model.py` 请生成迁移，不要在启动逻辑里加列。

---

## 环境要求

- **Python 3.10 或更高**（与生产 3.10.12 / `backend/pyproject.toml` 的 `target-version = "py310"` 对齐）。
  本地用更新的 CPython 可以开发，但不要用 3.11+ 才有的语法（例如 `from datetime import UTC`）—— ruff 按 py310 拦，生产 3.10 会 ImportError 起不来。
- **Node.js 22 + pnpm 9**（前端；CI 按这个跑）
- 本地开发默认 SQLite + 本地文件，**不需要** OSS / PostgreSQL

```bash
git clone https://github.com/njfuzrs/agent-backend.git
cd agent-backend
cp .env.example backend/.env   # 填 AUTH_PASSWORD 与 UPLOAD_TOKEN，缺了进程会 SystemExit

cd backend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8900

# 另一个终端
cd frontend && pnpm install && pnpm dev
```

开发工具：

```bash
# 后端 venv 内
pip install ruff pytest
# 本机（macOS）
brew install gitleaks pre-commit
pre-commit install          # 装 git hook，提交前自动跑 ruff / gitleaks 等
```

---

## 跑测试与检查

门禁是这三句，必须绿才提 PR：

```bash
cd backend && ruff check . && alembic check
python -m pytest ../tests/test_boundaries.py ../tests/test_identity.py ../tests/test_deploy_scripts.py -v
```

从仓库根也可以：`backend/venv/bin/python -m pytest tests/test_boundaries.py tests/test_identity.py tests/test_deploy_scripts.py -v`。

`tests/test_e2e.py`、`test_concurrent.py`、`test_fault.py`、`test_frontend_api.py`、`test_audit.py` 是对着**活服务端**的手动验收，**不进 CI**，也不作为 PR 门槛。怎么跑见 `tests/README.md`。

前端类型检查（改了 `frontend/` 时请跑）：

```bash
cd frontend && pnpm exec tsc -b
```

密钥扫描：

```bash
gitleaks git --redact --log-opts='--all'    # 扫 git 历史；公开仓口径
# 不要用 `gitleaks detect --no-git` 当公开口径 —— 它会扫到本机 backend/.env
```

`ruff` 必须在 `backend/` 下跑，否则吃不到 `pyproject.toml` 的 `target-version = "py310"`。根目录没有这份文件。

---

## 提交与 PR

- commit message 用 `类型: 简述`（`feat:` / `fix:` / `chore:` / `docs:` / `test:`）
- 一个 PR 做一件事。鉴权、迁移、前端、文档请分开提
- 改了行为就同步改 README / CLAUDE.md —— 文档与实现不一致在这里算 bug
- 改了冻结区（`POST /api/v1/upload/session-file`、`GET /api/v1/health`、nginx `/traj/`、前端 `vite.base`）必须在 PR 里显式写出。默认答案应该是「没改」
- 改了 ORM 模型必须带 Alembic 迁移，并在 PR 里写 `alembic check` 的输出

## 不接受的改动

- 给 `AUTH_PASSWORD` / `UPLOAD_TOKEN` 加回默认值
- 让 `/ctl/` 复用 `X-Upload-Token` 或 Basic Auth
- 把管理台凭据写进 `localStorage`
- 运行时 `create_all` / 手写「启动时加列」
- 用 npm / yarn 替换 pnpm
- 把生产路径、unit 文件名、`/traj/` 前缀改掉来「和仓名对齐」
- 给 `.github/workflows/deploy.yml` 加 `pull_request` 触发，或把业务凭据写进 GitHub Secrets
- 把路线图（flag / policy / 事件 / 成本）写成已经交付

## 上报安全问题

**不要开公开 issue。** 走 GitHub 私密通道，见 [SECURITY.md](SECURITY.md)。
