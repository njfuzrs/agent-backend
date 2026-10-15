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
    user_ref: Optional[int] = None
    # 本次请求滑动续期后的凭据过期时间（ISO8601 UTC），客户端据此回写本地记录。
    # 旧后端没有这个字段，客户端缺省时不做事。
    expires_at: str = ""


class OrganizationCreate(BaseModel):
    org_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field("", max_length=256)
    # 选填：老调用方（脚本、测试）不带理由也能建组织；管理台页面会要求填写。
    reason: str = Field("", max_length=512)


class OrganizationItem(BaseModel):
    org_id: str
    name: str
    created_at: str
    device_count: int = 0


class OrganizationListResponse(BaseModel):
    items: list[OrganizationItem]


class OrganizationRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=512)


# 新建团队的 slug 规则。老接口（注册码顺带建团队）不收紧，避免打断已在用的调用方。
SLUG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class TeamCreate(BaseModel):
    org_id: str = Field(..., min_length=1, max_length=128)
    team_id: str = Field(..., min_length=1, max_length=128, pattern=SLUG_PATTERN)
    name: str = Field("", max_length=256)
    reason: str = Field(..., min_length=1, max_length=512)


class TeamRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=512)


class TeamItem(BaseModel):
    org_id: str
    team_id: str
    name: str
    created_at: str
    device_count: int = 0


class TeamListResponse(BaseModel):
    items: list[TeamItem]


class DeviceMove(BaseModel):
    """调整设备归属。team_id 为空 = 只挂在组织上，不进任何团队。"""

    org_id: str = Field(..., min_length=1, max_length=128)
    team_id: Optional[str] = Field(None, max_length=128)
    reason: str = Field(..., min_length=1, max_length=512)


class DeviceMoveResponse(BaseModel):
    device_id: str
    org_id: str
    team_id: str = ""
    changed: bool


class IdentityAuditItem(BaseModel):
    id: int
    action: str
    target_type: str
    target_id: str
    org_id: str
    detail: dict
    reason: str
    actor: str
    created_at: str
    request_id: Optional[str] = None


class IdentityAuditListResponse(BaseModel):
    items: list[IdentityAuditItem]


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
    user_ref: Optional[int] = None
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
