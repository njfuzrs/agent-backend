"""三组日志测试共用的捕获器。

应用日志不向 root 传播（core/logging.py）：线上传播一次，uvicorn 会把同一条
再打成纯文本。caplog 默认挂在 root 上，因此什么都抓不到。

把 caplog 的 handler 直接挂到 ``agent`` 上，三个测试文件都生效。
configure_logging 只替换自己装的 handler，不会拆掉这一个。
捕获窗口仍由各用例的 caplog.at_level 控制。
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _capture_agent_logs(caplog):
    logger = logging.getLogger("agent")
    logger.addHandler(caplog.handler)
    try:
        yield
    finally:
        logger.removeHandler(caplog.handler)
