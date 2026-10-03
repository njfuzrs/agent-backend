"""auth 模块 Pydantic 模型。"""

from typing import Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    # username 保留给旧前端：它只认这个字段。值等于 actor。
    username: str
    kind: str  # password / user
    role: str
    name: str = ""
    is_admin: bool


class LoginOptions(BaseModel):
    """登录页据此决定展示哪些按钮。"""

    feishu_enabled: bool
    password_enabled: bool


class UserItem(BaseModel):
    id: int
    provider: str
    union_id: str
    name: str
    email: str
    role: str
    status: str
    created_at: str
    last_login_at: Optional[str] = None


class UserListResponse(BaseModel):
    items: list[UserItem]


class UserRoleUpdate(BaseModel):
    role: str


class AuthAuditItem(BaseModel):
    id: int
    created_at: str
    user_id: Optional[int] = None
    actor: str
    event: str
    detail_json: Optional[str] = None
    request_id: Optional[str] = None


class AuthAuditListResponse(BaseModel):
    items: list[AuthAuditItem]
