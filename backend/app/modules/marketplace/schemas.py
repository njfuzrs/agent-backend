"""marketplace 请求 / 响应模型。

管理台写入 `extra=forbid`：未知字段 422。插件的 name / version / description 只从包里的
plugin.json 取，不从表单取 —— 两处来源会让「目录里写的」和「装下去的」对不上。
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ItemKind = Literal["plugin", "skill", "mcp"]
VersionStatus = Literal["draft", "published", "yanked"]


class MarketItemCreate(BaseModel):
    """登记一个插件。name 必须和之后上传包里 plugin.json 的 name 一致。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    kind: ItemKind = "plugin"
    description: str = Field("", max_length=1024)
    maintainer: str = Field("", max_length=128)
    org_id: str = Field(..., min_length=1, max_length=128)
    # 空串 = 整个组织可见
    team_id: str = Field("", max_length=128)


class MarketItemUpdate(BaseModel):
    """改元数据。name 不能改：它是客户端 installed.json 里的身份。"""

    model_config = ConfigDict(extra="forbid")

    kind: Optional[ItemKind] = None
    description: Optional[str] = Field(None, max_length=1024)
    maintainer: Optional[str] = Field(None, max_length=128)
    org_id: Optional[str] = Field(None, min_length=1, max_length=128)
    team_id: Optional[str] = Field(None, max_length=128)


class MarketVersionItem(BaseModel):
    id: int
    version: str
    status: VersionStatus
    sha256: str
    size_bytes: int
    manifest: dict[str, Any]
    components: dict[str, Any]
    created_at: str
    created_by: str = ""
    published_at: Optional[str] = None
    published_by: Optional[str] = None
    yanked_at: Optional[str] = None


class MarketItemOut(BaseModel):
    id: int
    name: str
    kind: str
    description: str
    maintainer: str
    org_id: str
    team_id: str
    created_at: str
    updated_at: str
    created_by: str = ""
    # 最新已发布版本（按 semver），没有则为 None
    latest_version: Optional[str] = None
    versions: list[MarketVersionItem] = Field(default_factory=list)
    download_count: int = 0


class MarketItemListResponse(BaseModel):
    items: list[MarketItemOut]


class MarketAuditItem(BaseModel):
    id: int
    item_id: Optional[int] = None
    item_name: str
    version: str
    action: str
    reason: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    request_id: Optional[str] = None


class MarketAuditListResponse(BaseModel):
    items: list[MarketAuditItem]


class MarketDownloadItem(BaseModel):
    id: int
    created_at: str
    item_name: str
    version: str
    device_id: str
    org_id: str
    user_ref: Optional[int] = None


class MarketDownloadListResponse(BaseModel):
    items: list[MarketDownloadItem]


class MarketDownloadStatsItem(BaseModel):
    """按插件 × 人聚合。user_ref=None 合成「未登录」一行（与 cost by-user 同口径）。"""

    item_name: str
    user_ref: Optional[int] = None
    devices: int
    downloads: int
    last_at: str


class MarketDownloadStatsResponse(BaseModel):
    since: str
    items: list[MarketDownloadStatsItem]
