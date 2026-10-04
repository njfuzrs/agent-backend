"""feishu 模块：委托授权（P4，方案 §5.3）。

一组内聚的表（feishu_tokens / feishu_call_audit）+ 两个路由前缀
（`/ctl/feishu/mcp` 远程 MCP、`/feishu/**` 管理台只读）+ 两条鉴权链（设备凭据 / cookie 会话）。

不变量：Agent 的有效权限 = 员工本人的飞书权限 ∩ 应用 scope ∩ 远程策略。
任何一层拿不到都拒绝，**不降级到 tenant_access_token**。token 不出服务端。
"""
