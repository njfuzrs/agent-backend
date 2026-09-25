"""数据面鉴权（Data Plane）：Basic Auth（Web 端）+ Upload Token（脚本端）

⚠️ 冻结区（规划 §1.2）：`verify_upload_token` 是 sid-code trace 上传链路的对端鉴权，
   行为必须逐字不变。

⚠️ 本模块的符号**禁止被控制面模块 import**（规划 §2.1）——
   即 app/core/auth/control_plane.py 与 app/modules/{identity,flag,policy,cost}/ 下
   出现 verify_upload_token / verify_basic_auth 即为纪律违反，
   由 tests/test_boundaries.py 机械化拦截。
"""

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.auth.session import read_session
from app.core.config import settings
from app.core.logging import bind_context, get_logger

logger = get_logger("agent.auth")

# auto_error=False：没有 Authorization 头时返回 None 而不是直接 401，
# 这样才能落到 cookie 会话那条分支上。
security = HTTPBasic(auto_error=False)


async def verify_basic_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Web 端认证。接受两种凭据，按顺序尝试：

    1. **cookie 会话**（HttpOnly，管理台走这条）—— 见 core/auth/session.py
    2. **HTTP Basic**（脚本 / curl / 现有 5 个测试脚本走这条）

    保留 Basic 是刻意的：`tests/test_*.py` 五个脚本与运维 curl 都在用它，
    砍掉会让「现有 e2e 全绿」这条 M0 出口检查失效。
    去掉的只是「前端把密码存进 localStorage」这一件事。

    必须是异步的。同步依赖被 FastAPI 丢进线程池，contextvar 不跨线程，
    这里写的 actor 与 auth 在访问日志里会读不到（方案 §3.4）。
    """
    session_user = read_session(request)
    if session_user:
        # 只记种类，不记 cookie 值。访问日志在请求结束时读这个上下文（方案 §3.4）。
        bind_context(auth="session", actor=session_user)
        return session_user

    if credentials is not None:
        ok_user = secrets.compare_digest(credentials.username, settings.AUTH_USERNAME)
        ok_pass = secrets.compare_digest(credentials.password, settings.AUTH_PASSWORD)
        if ok_user and ok_pass:
            bind_context(auth="basic", actor=credentials.username)
            return credentials.username
        # 头在但用户名或口令不对。不记尝试值：reason 是枚举，够区分。
        _reject("basic_rejected", basic=True)

    # 既没有会话也没有 Authorization 头。reason 与口令不对相同：
    # basic 没有「头缺失」和「头坏了」两种处置，枚举里不分。
    _reject("basic_rejected", basic=True)


async def verify_upload_token(request: Request):
    """上传端认证：X-Upload-Token Header。

    异步的原因与 verify_basic_auth 相同：同步依赖里写的 auth 进不了访问日志。
    鉴权行为不变，仍是冻结区的那一个比对。
    """
    token = request.headers.get("X-Upload-Token", "")
    if not secrets.compare_digest(token, settings.UPLOAD_TOKEN):
        # 头缺失与头不对同一个 reason：upload token 没有两种处置。
        # 只记 reason，token 的任何子串都不进日志（方案 §3.5）。
        _reject("token_rejected")
    bind_context(auth="upload_token")


def _reject(reason: str, basic: bool = False) -> None:
    """鉴权失败记一条 warning 后抛 401。成功不记：访问日志已有 auth 种类。

    msg 是固定短句，检索靠 reason。不从异常消息取 reason——
    异常消息可能带上游原文。

    必须抛。只记日志就等于错误凭据照样放行，而这条是冻结区的上传鉴权。
    basic 为真时带 WWW-Authenticate，浏览器才知道该用哪种方式重试。
    """
    logger.warning("credential rejected", event="auth_rejected", reason=reason)
    headers = {"WWW-Authenticate": "Basic"} if basic else None
    raise HTTPException(status_code=401, detail="Unauthorized", headers=headers)
