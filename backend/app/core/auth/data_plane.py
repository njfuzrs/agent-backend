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

# auto_error=False：没有 Authorization 头时返回 None 而不是直接 401，
# 这样才能落到 cookie 会话那条分支上。
security = HTTPBasic(auto_error=False)


def verify_basic_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security),
):
    """Web 端认证。接受两种凭据，按顺序尝试：

    1. **cookie 会话**（HttpOnly，管理台走这条）—— 见 core/auth/session.py
    2. **HTTP Basic**（脚本 / curl / 现有 5 个测试脚本走这条）

    保留 Basic 是刻意的：`tests/test_*.py` 五个脚本与运维 curl 都在用它，
    砍掉会让「现有 e2e 全绿」这条 M0 出口检查失效。
    去掉的只是「前端把密码存进 localStorage」这一件事。
    """
    session_user = read_session(request)
    if session_user:
        return session_user

    if credentials is not None:
        ok_user = secrets.compare_digest(credentials.username, settings.AUTH_USERNAME)
        ok_pass = secrets.compare_digest(credentials.password, settings.AUTH_PASSWORD)
        if ok_user and ok_pass:
            return credentials.username

    raise HTTPException(
        status_code=401,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Basic"},
    )


def verify_upload_token(request: Request):
    """上传端认证：X-Upload-Token Header"""
    token = request.headers.get("X-Upload-Token", "")
    if not secrets.compare_digest(token, settings.UPLOAD_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid upload token")
