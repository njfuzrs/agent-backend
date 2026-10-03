"""auth 模块 Pydantic 模型。"""

from typing import Optional

from pydantic import BaseModel, Field


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
    # 名下绑定的设备数（P2）
    device_count: int = 0


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


class CliExchangeRequest(BaseModel):
    """POST /auth/cli/exchange。code 是 callback 回跳给 CLI 的一次性登录码。"""

    code: str = Field(..., min_length=1, max_length=256)
    verifier: str = Field(..., min_length=43, max_length=128)
    device_id: str = Field(..., min_length=1, max_length=128)
    platform: str = Field("", max_length=64)
    ver: str = Field("", max_length=64)


class CliUser(BaseModel):
    id: int
    name: str
    union_id: str


class CliExchangeResponse(BaseModel):
    """凭据明文只在这一次响应里出现。形状与 /ctl/enroll 的响应兼容，多一个 user。"""

    credential: str
    expires_at: str
    device_id: str
    org_id: str
    team_id: str = ""
    user: CliUser
