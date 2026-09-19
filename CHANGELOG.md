# 更新日志

本文件记录值得用户知道的变更。格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 开源首发之前的历史只存在于 git log 中，没有回溯整理 —— 那段时期是内部服务，
> 没有外部用户。不为美化去改已有 commit message。

本仓暂不发 PyPI / npm 包，版本号未切。用户可见的变化先记在 `[Unreleased]`。

## [Unreleased]

### 新增

- 治理文件：`LICENSE`（MIT）、`CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`、
  CI、Dependabot、pre-commit + gitleaks。
- 根 README：产品名 **Agent Backend**（企业级 Agent 后端），写明「现在有什么 / 还没有什么」
  与 M1–M6 路线图。轨迹是已交付的模块一，不是整个产品。

### 变更

- 管理台 / FastAPI / 浏览器标题从 Trajectory Platform 改为企业后端名。
- 服务侧补传 `scripts/backfill_sid_code.sh` 的 `TRAJ_UPLOAD_URL` 改为 opt-in（无默认值）。
- `.env.example` 与部署脚本去掉公网 IP 默认值；目标主机必须显式设置 `TRAJ_REMOTE_HOST`。

### 说明

- 生产路径 `/opt/trajectory-platform`、unit 文件名、nginx `/traj/` **保持原样**。
  开源仓名与线上目录分叉是有意的，采集 URL 已冻结。
- `require_device` 对 `/ctl/` 仍是 501。不要把路线图当成已交付。
