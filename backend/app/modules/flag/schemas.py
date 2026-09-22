"""flag 模块请求 / 响应模型。

下发响应故意**不用 Pydantic 模型**：客户端契约是「扁平 JSON，key → 任意 JSON 值」，
键名在运行时才知道，没法声明字段。路由直接返回 dict（见 router/flags.py）。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

# 客户端 `FlagValue` 的四种形态（feature-flags.ts）。
# 顺序有意义：Pydantic 按声明顺序试，bool 必须在 int 前面
# （否则 True 会被当成 1，下发出去是 `1` 而不是 `true`，客户端 `=== true` 判断失败）。
FlagValue = bool | int | float | str | dict[str, Any] | list[Any]


class FlagUpsert(BaseModel):
    """新建或整体更新一条 flag。value 必填 —— 「改 flag 但不说改成什么」没有意义。"""

    value: FlagValue
    description: str = Field("", max_length=512)
    reason: str = Field("", max_length=512)


class FlagCreate(FlagUpsert):
    key: str = Field(..., min_length=1, max_length=128)


class FlagItem(BaseModel):
    key: str
    value: FlagValue
    description: str = ""
    disabled: bool = False
    created_at: str
    updated_at: str
    updated_by: str = ""


class FlagListResponse(BaseModel):
    items: list[FlagItem]


class FlagDeleteResponse(BaseModel):
    key: str
    deleted: bool


class FlagAuditItem(BaseModel):
    id: int
    key: str
    action: str
    old_value: Optional[FlagValue] = None
    new_value: Optional[FlagValue] = None
    reason: str = ""
    actor: str = ""
    created_at: str


class FlagAuditListResponse(BaseModel):
    items: list[FlagAuditItem]
