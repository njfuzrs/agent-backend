"""bridge 模块：遥控配对（M6）。

一组内聚的表（bridge_sessions / bridge_session_tokens / bridge_audit）+ 两个
HTTP 路由（`POST /ctl/bridge/sessions` 设备凭据签发、`/bridge/sessions/**`
管理台 cookie）+ 一个**独立进程**的 WebSocket 中继。

**WebSocket 不挂在本模块的路由里，也不挂进主应用。** 生产主进程是
`uvicorn --workers 2`，连接对象是进程内的：CLI 打到 worker A、管理台打到
worker B，两边都在等配对，谁也不报错。所以中继是另一个进程
（`sidecar/main.py`，`--workers 1`），只共享这三张表。把 WS 路由 include 进
`app.main` 会让 `test_bridge_ws_not_mounted_on_main_app` 红。

失败语义（契约 §4）：
    签发 fail-closed —— 无凭据 / 坏凭据一律 401。禁止 X-Upload-Token 当鉴权。
    握手 fail-closed —— 无首帧 / 坏 token / URL 带 token= 一律 close 4001。
    中继不解释业务帧。它只鉴权、配对、转发。
"""
