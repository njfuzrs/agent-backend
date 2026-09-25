"""WebSocket 中继进程。

只从 `python -m` / uvicorn 启动，**不要**被 app.main import。主应用
`--workers 2`，中继必须单进程，见本目录 main.py 顶部的说明。
"""
