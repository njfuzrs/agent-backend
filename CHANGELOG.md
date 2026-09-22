# 更新日志

本文件记录值得用户知道的变更。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 开源首发之前的历史只存在于 git log 中，没有回溯整理 —— 那段时期是内部服务，
> 没有外部用户。不为美化去改已有 commit message。

本仓暂不发 PyPI / npm 包，版本号未切。用户可见的变化先记在 `[Unreleased]`。

## [Unreleased]

### 新增

- identity 模块：一次性注册码换设备凭据（`POST /api/v1/ctl/enroll`），`require_device` 查 sha256、拒吊销/过期。管理台可看设备列表与 `last_seen_at`。
- flag 模块（M2）：`GET /api/v1/ctl/flags` 扁平 JSON 全量下发，值保持原生 JSON 类型；停用或删除 flag 后该 key 不再出现在响应里，客户端据此回落默认值。管理台 `/traj/flags` 提供 CRUD、停用/启用与变更审计（走 cookie 会话）。
  该下发端点**无认证**是客户端契约决定的（`feature-flags.ts` 发裸 `fetch`，没有 `Authorization` 头），代价用两道锁补：路径只读，且写入侧门禁拒掉放宽安全限制的 key/description（`bypass` / `disable_sandbox` / `disable_all_hooks` 等）—— flag 只能施加约束，放宽类归 M3 Policy。key 必须匹配 `^[a-z][a-z0-9_]*$`，否则客户端 `SID_CODE_FLAG_<KEY>` 环境变量覆盖会静默失效。
- 治理文件：`LICENSE`（MIT）、`CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`、
  CI、Dependabot、pre-commit + gitleaks。
- 根 README：产品名 **Agent Backend**（企业级 Agent 后端），写明「现在有什么 / 还没有什么」
  与 M1–M6 路线图。轨迹是已交付的模块一，不是整个产品。
- 生产发版入口 `deploy/release.sh`：服务器上停服务 / 备份 / 迁库 / 切代码 / 启动 / 冒烟的唯一脚本。`push_code.sh` 改为 rsync 到 `/tmp/agent-backend-release-$SHA/` 再调它。根目录读 `AGENT_BACKEND_ROOT`。

### 变更

- 管理台改为独立登录页：未登录或会话过期跳 `/login`，顶栏提供登出；不再用弹窗输口令。
- 边界测试的 `/ctl/` 无认证豁免从「代码里写死 enroll 一条」改成显式白名单 `CTL_AUTH_EXEMPTIONS`（锁方法 + 路径），并新增三条测试把每个豁免的补偿措施机械化：下发端点不得有写方法、flag 写口必须挂 `require_web_session`、门禁必须拒掉放宽安全限制的 key。白名单里指向已不存在端点的条目也会红。
- `timeutil` 从 `app/modules/identity/service/` 移到 `app/core/` —— 它是平台工具，`core/auth/control_plane.py` 早已在用，留在模块里等于 core 依赖 module。
- 去掉轨迹对比（`/compare` 前后端）与 AI 评分/等级（scoring 路由、上传自动评分、筛选与展示）。
- 管理台 / FastAPI / 浏览器标题从 Trajectory Platform 改为企业后端名。
- 服务侧补传 `scripts/backfill_sid_code.sh` 的 `TRAJ_UPLOAD_URL` 改为 opt-in（无默认值）。
- `.env.example` 与部署脚本去掉公网 IP 默认值；目标主机必须显式设置 `TRAJ_REMOTE_HOST`。

### 修复

- `backup_pg.sh`：调用 `ossutil64`、git 可执行位、从 `$ROOT/.env` 的 `DATABASE_URL` 解析 `PGPASSWORD`；无密码非 0。`audit.sh` / `cleanup_deleted.sh` 同样改调 `ossutil64`。
- 边界测试遍历路由时兼容 FastAPI 0.141+ 的 `_IncludedRouter`（不再把冻结区误报成端点消失）。

### 说明

- 生产路径 `/opt/trajectory-platform`、unit 文件名、nginx `/traj/` **保持原样**。
  开源仓名与线上目录分叉是有意的，采集 URL 已冻结。
