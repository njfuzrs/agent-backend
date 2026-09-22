"""policy 模块：策略下发（M3）。

一组内聚的表（policies / policy_audit）+ 两个路由前缀
（`/ctl/policy` 设备凭据读、`/policies/**` 管理台写）+ 两条鉴权链
（`require_device` / cookie 会话）。

失败语义（规划 §6）：下发是「施加约束」，客户端拉不到就回落本地 managed
（fail-open）；管理台写入缺 reason / 未知字段 / 空策略是 fail-closed。
下发 **可以** fail-open，**不可以** 无认证。
"""
