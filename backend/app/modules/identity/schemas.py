"""身份模块请求 / 响应模型。凭据明文只出现在 EnrollResponse.credential。"""

from typing import Optional

from pydantic import BaseModel, Field


class EnrollRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=128)
    user_id: Optional[str] = Field(None, max_length=256)
    org_id: Optional[str] = Field(None, max_length=128)
    team_id: Optional[str] = Field(None, max_length=128)
    platform: str = Field("", max_length=64)
    ver: str = Field("", max_length=64)


class EnrollResponse(BaseModel):
    credential: str
    expires_at: str
    device_id: str
    org_id: str
    team_id: str = ""


class WhoAmIResponse(BaseModel):
    device_id: str
    org_id: str
    team_id: str = ""
    user_id: str = ""


class OrganizationCreate(BaseModel):
    org_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field("", max_length=256)


class OrganizationItem(BaseModel):
    org_id: str
    name: str
    created_at: str
    device_count: int = 0


class OrganizationListResponse(BaseModel):
    items: list[OrganizationItem]


class EnrollCodeCreate(BaseModel):
    org_id: str = Field(..., min_length=1, max_length=128)
    org_name: str = Field("", max_length=256)
    team_id: Optional[str] = Field(None, max_length=128)
    team_name: str = Field("", max_length=256)
    note: str = Field("", max_length=512)
    ttl_hours: Optional[int] = Field(None, ge=1, le=24 * 30)


class EnrollCodeCreated(BaseModel):
    """明文 code 只在这一次响应里出现。"""

    code: str
    org_id: str
    team_id: str = ""
    expires_at: str
    note: str = ""


class EnrollCodeItem(BaseModel):
    id: int
    org_id: str
    team_id: str = ""
    expires_at: str
    used_at: Optional[str] = None
    created_at: str
    created_by: str = ""
    note: str = ""


class EnrollCodeListResponse(BaseModel):
    items: list[EnrollCodeItem]


class DeviceListItem(BaseModel):
    device_id: str
    org_id: str
    team_id: str = ""
    user_id: str = ""
    platform: str = ""
    ver: str = ""
    last_seen_at: Optional[str] = None
    created_at: str
    credential_expires_at: Optional[str] = None
    revoked: bool = False


class DeviceListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[DeviceListItem]


class RevokeResponse(BaseModel):
    device_id: str
    revoked: bool
