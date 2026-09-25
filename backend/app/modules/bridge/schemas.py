"""bridge 模块请求 / 响应模型。

签发响应用显式 schema，**不要**返回 ORM 对象或 dict：token hash 与明文
都不能从别的字段漏出去。列表项同样显式枚举，没有 token 字段可漏。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    """CLI 创建 session 的可选元数据。身份不在这里，身份从凭据取。"""

    model_config = ConfigDict(extra="forbid")

    ver: Optional[str] = Field(default=None, max_length=64)
    cwd_basename: Optional[str] = Field(default=None, max_length=255)


class SessionIssued(BaseModel):
    """签发响应。session_token 明文只出现这一次，库里只有 hash。"""

    session_id: str
    session_token: str
    ws_url: str
    expires_at: str


class SessionWhoami(BaseModel):
    """当前设备未过期的 session。没有时 session_id 为空。"""

    session_id: Optional[str] = None
    state: Optional[str] = None
    expires_at: Optional[str] = None


class SessionItem(BaseModel):
    """管理台列表的一行。没有 hash，没有明文。"""

    id: str
    device_id: str
    org_id: str
    state: str
    ver: Optional[str] = None
    cwd_basename: Optional[str] = None
    created_at: str
    expires_at: str


class SessionListResponse(BaseModel):
    items: list[SessionItem]


class DisconnectRequest(BaseModel):
    """强制断开。reason 必填：这是审计事实，不是可选备注。"""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=512)
