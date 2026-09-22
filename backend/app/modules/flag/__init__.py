"""flag 模块：Feature Flag 下发（M2）。

一组内聚的表（feature_flags / feature_flag_audit）+ 两个路由前缀
（`/ctl/flags` 公开读、`/flags/**` 管理台写）+ 两条鉴权链（无认证 / cookie 会话）。
"""
