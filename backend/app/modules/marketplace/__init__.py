"""marketplace 模块：企业插件市场（P5，方案 §5.5）。

一组内聚的表（market_items / market_versions / market_audit）+ 两个路由前缀
（`/ctl/marketplace/**` 设备只读、`/marketplace/**` 管理台）+ 两条鉴权链（设备凭据 / cookie 会话）。

失败语义：市场下载是授予信任的通道，fail-closed —— 包校验不过不入库，制品哈希对不上不下发。
index 拉取失败时「已装的照常用」是客户端的 fail-static，服务端不替它兜。
"""
