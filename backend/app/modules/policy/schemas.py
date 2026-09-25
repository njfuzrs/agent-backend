"""policy 模块请求 / 响应模型。

下发响应故意**不用 Pydantic 模型**：客户端契约是「顶层就是 PolicySettings」，
`response_model` 会把未声明字段滤掉（flag 下发踩过这个坑）。路由直接返回 dict。

管理台写入 `extra=forbid`：未知字段 422，这是「不能自创字段」的机械化。
`settings` 不准含 `source` / `policyEndpoint`（Pydantic 未声明 + extra=forbid；
guard 再拦一层嵌套规则）。
"""

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

ScopeType = Literal["device", "team", "org"]


def _strict_bool(value: Any) -> Any:
    """只收真正的 bool。Pydantic 默认会把字符串 "false" 收成 False，这里不要。"""
    if isinstance(value, bool) or value is None:
        return value
    raise ValueError("必须是布尔值")


StrictBool = Annotated[bool, BeforeValidator(_strict_bool)]
BypassMode = Literal["disable", "allow"]
CustomizationSurface = Literal["commands", "skills", "agents", "hooks", "mcp-servers"]


class PolicyPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: Optional[list[str]] = None
    deny: Optional[list[str]] = None
    ask: Optional[list[str]] = None


class PolicyLimitValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: Optional[str] = None


class PolicySettingsIn(BaseModel):
    """写入用的 PolicySettings 子集。不含 source（下发时写死 remote）。"""

    model_config = ConfigDict(extra="forbid")

    permissions: Optional[PolicyPermissions] = None
    policyLimits: Optional[dict[str, PolicyLimitValue]] = None
    allowManagedPermissionRulesOnly: Optional[bool] = None
    disableAllHooks: Optional[bool] = None
    allowManagedHooksOnly: Optional[bool] = None
    disabledModes: Optional[list[str]] = None
    disableBypassPermissionsMode: Optional[BypassMode] = None
    strictPluginOnlyCustomization: Optional[bool | list[CustomizationSurface]] = None
    # 省略 = 未配置 = 不关遥控。false 才是约束。不做成 flag：flag 端点无认证。
    bridgeEnabled: Optional[StrictBool] = None


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: ScopeType
    scope_id: str = Field(..., min_length=1, max_length=128)
    org_id: str = Field(..., min_length=1, max_length=128)
    settings: PolicySettingsIn
    reason: str = Field(..., min_length=1, max_length=512)


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: PolicySettingsIn
    reason: str = Field(..., min_length=1, max_length=512)


class PolicyItem(BaseModel):
    id: int
    scope_type: str
    scope_id: str
    org_id: str
    settings: dict[str, Any]
    disabled: bool = False
    created_at: str
    updated_at: str
    updated_by: str = ""
    etag: str = ""


class PolicyListResponse(BaseModel):
    items: list[PolicyItem]


class PolicyDeleteResponse(BaseModel):
    id: int
    deleted: bool


class PolicyAuditItem(BaseModel):
    id: int
    policy_id: Optional[int] = None
    scope_type: str
    scope_id: str
    action: str
    old_settings: Optional[dict[str, Any]] = None
    new_settings: Optional[dict[str, Any]] = None
    reason: str = ""
    actor: str = ""
    created_at: str
    # 跳回同一次请求的日志。旧行与脚本直接改库的行是 None。
    request_id: Optional[str] = None


class PolicyAuditListResponse(BaseModel):
    items: list[PolicyAuditItem]


