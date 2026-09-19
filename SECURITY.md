# 安全策略

## 上报漏洞

**请不要用公开 issue 上报安全漏洞。** 公开 issue 在修复发布前就把问题暴露给了所有人。

请用 GitHub 的私密上报通道：
**[Security → Report a vulnerability](https://github.com/njfuzrs/agent-backend/security/advisories/new)**

走这个入口而不是邮箱，是因为它对双方都更省事：上报内容在草案阶段只有你和维护者可见，
修复过程、CVE 申请与最终公开都在同一个页面里，不需要交换任何邮箱地址。

上报时尽量包含：

- 漏洞类型与影响（能读到什么、能执行什么、需要什么前置条件）
- 复现步骤，或一个最小复现用例
- 受影响的版本（commit hash 或你部署的镜像/目录）与运行环境（Python / OS / 是否经 nginx `/traj/`）
- 你认为的严重程度，以及是否已在野外被利用

我们会在**5 个工作日内**回复确认收到。修复后会在 `CHANGELOG.md` 里记录，
如果你愿意署名，我们会在致谢里写上你（默认不写，除非你明确说可以）。

**本项目是个人项目，不是有 SLA 的商业产品**，没有安全响应团队，也没有漏洞赏金。
响应速度取决于维护者的可用时间 —— 这一点如实说明，不做承诺。

## 支持的版本

只支持**最新源码**（本仓暂无发版包、无 backport）。旧部署自行对照 `main` 升级。

---

## ① 数据面会存完整轨迹（使用前必读）

本仓的数据面是**轨迹接收端**，不是摘要服务。一旦上传成功，对象存储 / 本地目录里落的是完整会话，不是打码后的统计。

| 内容 | 说明 |
| --- | --- |
| `.traj` | 解析后的轨迹（含消息、工具调用、用量） |
| `raw` | 采集端原始记录 |
| `events` | 会话事件流 |

**默认不对外匿名写入。** 上传走 `POST /api/v1/upload/session-file`，必须带 `X-Upload-Token`（或管理面 Basic Auth / 会话 cookie 才能读）。token 空、错、旧值一律 401。客户端可再带 `X-Content-SHA256`，服务端比对失败返回 400。

🔴 **消息体不做内容级脱敏。** 对话里粘贴过的 token、私钥、`.env`、源码、命令输出，都会按采集端给的原文入库。本仓不是 claude-trace 的 Scrubber，也不会在写入时改写 payload。把生产库、对象存储、备份盘都当成与源码同等敏感的数据。

软删除只标 `deleted_at`；对象与 DB 行要等 30 天后由运维脚本真删。真删之前，持有管理面凭据的人仍可能读到。

下面这些**不算漏洞**（已知现状，不需要重复上报「轨迹里有密钥」）：

- 数据面按设计保存完整 `.traj` / raw / events
- 管理台目前是共享口令 + HttpOnly cookie，不是每人一号（路线图，见 README）
- 传输层可以是明文 HTTP（生产 TLS 是 P2，不在本仓开源当天的范围）

下面这些**算漏洞，请上报**：

- 无 token / 错 token 仍能写入或读出轨迹
- `X-Content-SHA256` 可被绕过（内容被掉包仍 2xx）
- 路径遍历：`session_id` 等外部可控值写到对象存储约定前缀之外
- 凭据出现在日志、错误信息、健康检查、导出文件，或被发到调用方没配置的第三方
- 未鉴权的列表 / 详情 / 导出 / 删除

自建接收端请用 HTTPS，并限制来源。采集协议以 [claude-trace `docs/upload-protocol.md`](https://github.com/njfuzrs/claude-trace/blob/main/docs/upload-protocol.md) 为准。

---

## ② 控制面比数据面更敏感

`/ctl/` 是控制面：flag / policy / 设备身份将对**全体客户端**生效。控制面被打穿，等于把所有接入端的护栏关掉。

因此：

- **控制面不得复用数据面凭据。** 尤其禁止把 `X-Upload-Token` / `verify_upload_token` / `verify_basic_auth` 挂到 `/ctl/` 上。能上传轨迹的人，不应该因此获得下发策略的权力。
- `/ctl/` 必须走 `require_device`。这条由 `tests/test_boundaries.py` 反射检查锁定。
- 开源当天 `require_device` 对 `/ctl/` 仍返回 **501**（M1 未开工）。501 是「未实现」，不是「未鉴权即可用」。把它改成挂上传 token 的 200，属于安全回归，不是功能。

下面这些**不算漏洞**：

- `/ctl/` 现在 501（路线图，见 README「还没有什么」）
- 控制面与数据面用两套依赖链（这是纪律，不是缺陷）

下面这些**算漏洞，请上报**：

- 任意 `/ctl/` 端点能在无 `require_device` 的情况下被调用（含「先 501 再偷偷换鉴权」）
- 控制面模块 import 了 `verify_upload_token` / `verify_basic_auth`
- 用数据面 token 成功打到本应属于控制面的能力（flag / policy / 设备签发）

纪律原文在仓库 `tests/test_boundaries.py` 的模块文档，以及规划里「双平面鉴权隔离」那一条。破坏它的 PR 不会被合并。

---

## 这个项目的攻击面（上报前值得知道）

Agent Backend 是一个**带管理台的服务端**。设计前提是：部署者自己保管 `AUTH_PASSWORD` / `UPLOAD_TOKEN` / 对象存储密钥，并且不把它们写进 git。

已知、已公开的现状（不需要重复开 issue，但若你发现我们低估了影响，欢迎走私密通道）：

- `AUTH_PASSWORD` / `UPLOAD_TOKEN` **无默认值**，缺了进程 `SystemExit`。不要提「加回 changeme 方便本地」的 PR。
- 管理台凭据走 HttpOnly + SameSite cookie，不进 `localStorage`。
- schema 只走 Alembic；运行时代码不得 `create_all`。
- 生产路径 `/opt/trajectory-platform` 与 nginx 前缀 `/traj/` 与开源仓名分叉，是有意的；改冻结区 URL 会切断 sid-code / claude-trace 采集。

供应链：本仓首发不做 PyPI / npm 包。依赖更新走 Dependabot；发现被投毒的 lockfile 或 CI 脚本请上报。
