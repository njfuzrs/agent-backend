"""cost 模块请求 / 响应模型。

上报请求**故意只声明到原始 dict**：契约裁决权在客户端（`UsageLedgerEntry` 今天
就在落盘的那个形状），服务端必须接受它。字段可少不可改名、不可改口径。
body 解析在 ingest 路由里手工做，缺 `sessionId` 要 400 不是 422。

下发响应故意**不用 Pydantic 模型当 response_model**：response_model 会把未声明
字段滤掉（flag 下发踩过这个坑）。路由直接返回 dict。

管理台写入 `extra=forbid`：未知字段 422。`enforcement` 只接受 alert / block，
没有 downgrade（T1）。

by-scope 响应不得出现单价字段 —— 规划口径陷阱的机械化。门禁扫 schema。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ScopeType = Literal["device", "team", "org"]
Period = Literal["session", "daily", "weekly", "monthly"]
Enforcement = Literal["alert", "block"]


class UsageIngestResponse(BaseModel):
    """200 的 body。区分 inserted / updated 是出口「30 次 1 行」的观测口。"""

    upserted: Literal["inserted", "updated"]


class UsageLedgerItem(BaseModel):
    """一条账本的查询投影。cost_usd 含影子；prompt_total 不含。并列，不要相除。"""

    id: int
    device_id: str
    org_id: str
    team_id: str = ""
    session_id: str
    ts: int
    received_at: str
    model: str
    provider: str
    prompt_total: int = 0
    cache_hit: int = 0
    cache_write: int = 0
    uncached_input: int = 0
    output: int = 0
    cost_usd: float
    savings_usd: float = 0
    duration_ms: int = 0
    side_input_tokens: Optional[int] = None
    side_output_tokens: Optional[int] = None
    side_cost_usd: Optional[float] = None
    endpoint_host: Optional[str] = None
    app_version: Optional[str] = None
    peak_ratio: Optional[float] = None


class UsageLedgerListResponse(BaseModel):
    total: int
    items: list[UsageLedgerItem]


class UsageByScopeItem(BaseModel):
    """按 device 一行。回答「谁在烧钱」。

    服务端不得返回单价字段。cost_usd 与 prompt_total 口径不同源
    （前者含影子、后者不含），相除是错数。
    """

    device_id: str
    org_id: str = ""
    team_id: str = ""
    sessions: int = 0
    cost_usd: float = 0
    side_cost_usd: Optional[float] = None
    prompt_total: int = 0
    cache_hit: int = 0
    output: int = 0
    last_received_at: Optional[str] = None


class UsageByScopeResponse(BaseModel):
    period: str
    period_key: str
    items: list[UsageByScopeItem]


class BudgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_type: ScopeType
    scope_id: str = Field(..., min_length=1, max_length=128)
    org_id: str = Field(..., min_length=1, max_length=128)
    period: Period
    limit_usd: float = Field(..., gt=0)
    enforcement: Enforcement = "alert"
    reason: str = Field(..., min_length=1, max_length=512)


class BudgetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit_usd: Optional[float] = Field(None, gt=0)
    enforcement: Optional[Enforcement] = None
    period: Optional[Period] = None
    reason: str = Field(..., min_length=1, max_length=512)


class BudgetItem(BaseModel):
    id: int
    scope_type: str
    scope_id: str
    org_id: str
    period: str
    limit_usd: float
    enforcement: str
    used_usd: float = 0
    disabled: bool = False
    created_at: str
    updated_at: str
    updated_by: str = ""


class BudgetListResponse(BaseModel):
    items: list[BudgetItem]


class BudgetDeleteResponse(BaseModel):
    id: int
    deleted: bool


class BudgetAuditItem(BaseModel):
    id: int
    budget_id: Optional[int] = None
    scope_type: str
    scope_id: str
    action: str
    old: Optional[dict] = None
    new: Optional[dict] = None
    reason: str = ""
    actor: str = ""
    created_at: str
    # 跳回同一次请求的日志。旧行与脚本直接改库的行是 None。
    request_id: Optional[str] = None


class BudgetAuditListResponse(BaseModel):
    items: list[BudgetAuditItem]
