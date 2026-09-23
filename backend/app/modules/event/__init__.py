"""event 模块：analytics 事件上报与审计视图（M4）。

一组内聚的表（events / event_rejects）+ 两个路由（`POST /events` 设备凭据写、
`/events/**` 管理台 cookie 读）+ 两条鉴权链。

**本模块是本仓第一个「数据流向是数据面、鉴权用控制面」的端点**：事实从客户端
流出（数据面方向），但写入的是他人可见的审计记录，所以鉴权必须是控制面那档
（`require_device`，fail-closed）。路径**故意不在 `/ctl/` 下** —— `/ctl/` 是策略
向客户端流入的方向，放进去语义错位（契约 §1）。

代价是现有门禁 ② 按路径含 `/ctl/` 筛，**扫不到本模块** —— 漏挂鉴权不会有任何
东西红。所以 tests/test_boundaries.py 有三条专门给 events 的门禁，见该文件
「⑧ events」一节。删掉那三条等于把本模块的鉴权变成无人看守。

失败语义（契约 §6）：
    写入 fail-open —— 单条坏事件只计 rejected，不退整批（退整批会让客户端把这批
                      在磁盘上循环 24h 然后全丢）；
    鉴权 fail-closed —— 无凭据 / 坏凭据一律 401；
    白名单 fail-closed —— 未知事件名不入库，计 rejected + logger.warning。
"""
