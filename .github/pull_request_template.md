## 这个 PR 做了什么

<!-- 一句话说明。一个 PR 只做一件事 -->

## 为什么

<!-- 解决什么问题。如果是修 bug，说明根因 -->

## 怎么验证的

<!-- 贴真实输出，不要只写「测试通过」 -->

```text
cd backend && ruff check . && alembic check
python -m pytest ../tests/test_boundaries.py -v
# 若改了 frontend/：
cd frontend && pnpm exec tsc -b
```

- [ ] `ruff check .` 通过（必须在 `backend/` 下跑）
- [ ] `alembic check` 通过
- [ ] `pytest tests/test_boundaries.py` 通过（7 个 test_）
- [ ] 改了 `frontend/` 时 `pnpm exec tsc -b` 通过
- [ ] 改了行为，同步改了 README / CLAUDE.md（文档与实现不一致在本项目算 bug）
- [ ] 注释用中文（项目约定，见 CONTRIBUTING.md）

## `/traj/` 冻结区

以下改了就必须在 PR 里写出来（默认应该是「没改」）。改了会切断 sid-code / claude-trace 采集：

- [ ] **没有**改 `POST /api/v1/upload/session-file`、`GET /api/v1/health`
- [ ] **没有**改 nginx 前缀 `/traj/`、前端 `vite.base`、`apiBasePath`
- [ ] **没有**改生产路径 `/opt/trajectory-platform` 或 unit 文件名（与开源仓名分叉是有意的）

若确实要动冻结区，写明影响面、迁移步骤、谁来改采集端：

<!-- 无则删除本段 -->

## 鉴权

- [ ] 没有给 `AUTH_PASSWORD` / `UPLOAD_TOKEN` 加默认值
- [ ] 没有让 `/ctl/` 复用 `X-Upload-Token` / Basic Auth
- [ ] 没有把管理台凭据写进 `localStorage`
