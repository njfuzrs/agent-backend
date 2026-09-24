"""cost 模块：用量账本 upsert + 预算下发 + 一个固定聚合（M5）。

一组内聚的表（usage_ledger / budgets / budget_audit）+ 三个路由
（`POST /usage/ledger` 设备凭据写、`GET /ctl/budget` 设备凭据读、
`/usage/ledger/**` 与 `/budgets/**` 管理台 cookie）+ 两条鉴权链。

**本模块是本仓第二个「数据流向是数据面、鉴权用控制面」的端点**（第一个是
events）：账本从客户端流出，但 upsert 能覆盖别人的成本，所以鉴权必须是
控制面那档（`require_device`，fail-closed）。路径**故意不在 `/ctl/` 下** ——
`/ctl/` 是策略向客户端流入的方向，放进去语义错位（契约 §1）。

代价是现有门禁 ② 按路径含 `/ctl/` 筛，**扫不到上报端点** —— 漏挂鉴权不会
有任何东西红。所以 tests/test_boundaries.py 有专门给 usage/ledger 的门禁。
`GET /ctl/budget` 在 `/ctl/` 下会被 ② 扫到，仍单开一条：豁免名单加错一行
就会无认证下发预算（block 档 = 远程关停全公司 agent）。

失败语义（契约 §6）：
    写入 fail-open —— 一行 upsert 失败 5xx，让客户端重试；没有「部分成功」；
    鉴权 fail-closed —— 无凭据 / 坏凭据一律 401。接受匿名写入 = 任何人能把
                      别人的成本改成 0（比 events 灌水更严重：账本是覆盖）；
    预算下发 fail-open —— 拉不到当没配远程预算。本地 costLimit 仍硬停；
    远程超限默认 alert（告警放行），管理台可切 block。
"""
