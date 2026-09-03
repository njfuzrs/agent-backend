"""管理台登录/登出端点（规划 §PR-0.5 第 4 条）。

凭据不再进 localStorage：登录成功后服务端下发 HttpOnly cookie，
浏览器自动携带，JS 读不到 —— XSS 拿不到管理员口令。
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.core.auth.session import (
    clear_session,
    issue_session,
    require_web_session,
    verify_credentials,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    username: str


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, response: Response):
    """校验口令 → 下发 HttpOnly 会话 cookie。口令不回显、不落盘。"""
    if not verify_credentials(payload.username, payload.password):
        # 不区分「用户名错」与「口令错」—— 避免用户名枚举
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    issue_session(response, payload.username)
    return LoginResponse(username=payload.username)


@router.post("/logout")
async def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/me", response_model=LoginResponse)
async def me(username: str = Depends(require_web_session)):
    """前端启动时调它判断「是否已登录」，取代原来读 localStorage。"""
    return LoginResponse(username=username)
