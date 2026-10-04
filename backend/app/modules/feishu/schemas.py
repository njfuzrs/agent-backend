"""feishu 模块管理台响应。任何字段都不含 token 明文或密文。"""

from typing import Optional

from pydantic import BaseModel


class FeishuCallItem(BaseModel):
    id: int
    created_at: str
    user_id: Optional[int] = None
    device_id: str
    tool: str
    target_token: str
    outcome: str
    error_code: Optional[str] = None
    latency_ms: int
    request_id: Optional[str] = None


class FeishuCallListResponse(BaseModel):
    items: list[FeishuCallItem]


class FeishuGrantItem(BaseModel):
    user_id: int
    scope: str
    access_expires_at: str
    refresh_expires_at: Optional[str] = None
    granted_at: str
    updated_at: str
    version: int
    # 授权快满 365 天，需要本人重新登录
    reauth_soon: bool


class FeishuGrantListResponse(BaseModel):
    enabled: bool
    items: list[FeishuGrantItem]
