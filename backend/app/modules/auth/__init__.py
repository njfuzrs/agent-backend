"""模块七：人员身份（P1 管理台飞书登录）。

一个模块 = 一组内聚的表（users / auth_states / auth_audit）+ 一个路由前缀（/auth、/users）
+ 一条鉴权链（cookie 会话）。会话的签发与校验仍在 core/auth/session.py，本模块提供它查的表。
"""
