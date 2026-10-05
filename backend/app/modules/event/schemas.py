"""event 模块请求 / 响应模型。

上报请求**故意只声明到 `events: list[dict]`**，不给单条事件建 Pydantic 模型：
契约裁决权在客户端（`analytics/exporters/http.ts` 今天就在发的那个 body），
服务端必须接受它。用严格模型会把「单条事件字段非法」变成 422 整批退 —— 而契约 §1
要求的是 202 + rejected 计数（**不因一条坏事件退掉整批**，否则这批会在客户端磁盘上
循环 24h 然后全丢）。逐条校验因此放在 service/guard.py，不在 Pydantic 层。
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class EventIngestRequest(BaseModel):
    """`{"events": [...]}`。外层形状由客户端写死，不可改（契约 §10）。

    `events` 必填：缺这个键是 400（形状错是 bug，要让人看见）。空数组**不是**错误 ——
    客户端 flush() 有 `if (batch.length === 0) return`，但重放路径可能送空。
    """

    events: list[dict[str, Any]]


class EventIngestResponse(BaseModel):
    """202 的 body。三个数加起来 = 请求里的事件条数。

    accepted = 真正落库的行数（DB rowcount，不是「校验通过数」）
    deduped  = 指纹撞上唯一索引被跳过的
    rejected = 白名单外 / 字段非法，未入库
    """

    accepted: int = 0
    deduped: int = 0
    rejected: int = 0


class EventItem(BaseModel):
    """一条事件的查询投影。metadata 原样给人看（对标 flag 页把 422 detail 原样展示）。"""

    id: int
    event_name: str
    device_id: str
    org_id: str
    team_id: str = ""
    # 上报时设备绑定的人（users.id）。服务端从凭据写入，可信；NULL = 未登录设备
    user_ref: Optional[int] = None
    session_id: Optional[str] = None
    client_ts: int
    received_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DailyBucket(BaseModel):
    """按 `received_at` 日分桶。管理台柱状图用这个，不用「导出成功率」单一百分比
    （那个数会在停机结束后自己回到 99%，丢掉的几天不留痕迹）。停机窗口由人在发版
    记录里登记，本接口**不**自动推断（需要 health 时序，属另一个功能）。
    """

    date: str
    count: int


class EventListResponse(BaseModel):
    total: int
    items: list[EventItem]
    daily: list[DailyBucket] = Field(default_factory=list)


class SessionCoverageItem(BaseModel):
    """会话覆盖的一行。`has_trajectory=False` 与 `event_count=0` 是两个不同的缺口信号。"""

    session_id: Optional[str] = None
    has_trajectory: bool = False
    event_count: int = 0
    policy_enforced: int = 0
    guardrail_triggered: int = 0
    context_assembled: int = 0
    device_id: str = ""
    first_client_ts: Optional[int] = None
    last_client_ts: Optional[int] = None


class SessionCoverageResponse(BaseModel):
    """三类计数缺一不可（服务端设计 §4）。

    `with_trajectory` / `without_trajectory` 是「有事件」那一侧的拆分；
    `trajectory_without_events` 是**有轨迹但零事件**的会话数 —— 它是「事件通道没
    接上」的唯一信号，只做前两类等于让缺口隐身。
    """

    sessions_with_events: int = 0
    with_trajectory: int = 0
    without_trajectory: int = 0
    trajectory_without_events: int = 0
    items: list[SessionCoverageItem]


class PolicyAuditItem(BaseModel):
    """按 device 聚合的一行。

    `policy_enforced` 的 applied 与 none/error **必须分开**：outcome=none 是「该设备
    三层都没配策略」，error 是「拉取失败回落本地」。合并成「策略拉取次数」会让一台
    **策略从未生效**的设备看起来和正常设备一样活跃（管理台 §4 口径纪律 1）。

    `guardrail_*` 三列并排，`unknown` 既不算真报也不算误报（口径纪律 2）：客户端无法
    自动判定误报，unknown 是「缺少后续信号」，主要来自会话崩溃。算进真报会高估护栏
    价值，算进误报会低估。**不做「误报率」这个百分比** —— 分母含 unknown 时它没有意义。
    """

    device_id: str
    org_id: str = ""
    team_id: str = ""
    policy_applied: int = 0
    policy_none_or_error: int = 0
    guardrail_total: int = 0
    guardrail_true_positive: int = 0
    guardrail_false_positive: int = 0
    guardrail_unknown: int = 0
    permission_deny: int = 0
    last_received_at: Optional[str] = None


class PolicyAuditResponse(BaseModel):
    since: str
    items: list[PolicyAuditItem]


class EventRejectItem(BaseModel):
    event_name: str
    reason: str = ""
    count: int = 0
    first_seen_at: str
    last_seen_at: str


class EventRejectListResponse(BaseModel):
    """被拒事件名。total 是所有名字的 count 之和 —— 管理台首屏那个数。"""

    total: int = 0
    items: list[EventRejectItem]


class PluginUserUsage(BaseModel):
    """插件 × 人的一行。user_ref 为 NULL 是「未登录设备」合计，不能丢：丢了总数对不上。"""

    user_ref: Optional[int] = None
    name: str = ""
    union_id: str = ""
    calls: int = 0
    last_received_at: Optional[str] = None


class PluginUsageItem(BaseModel):
    """一个插件的汇总。`users` 是去重人数（未登录设备合计算作一人份，单独标出）。"""

    plugin_name: str
    # 市场名可能不止一个（同名插件上过两个市场），全部列出，不挑一个
    marketplaces: list[str] = Field(default_factory=list)
    calls: int = 0
    mcp_calls: int = 0
    skill_calls: int = 0
    users: int = 0
    has_anonymous: bool = False
    by_user: list[PluginUserUsage] = Field(default_factory=list)


class PluginUsageResponse(BaseModel):
    """按插件统计 tool_invoked。

    `truncated=True` 表示窗口内行数超过扫描上限，只聚合了最新的 `scanned` 行 ——
    数字是**下限**，不是总数。metadata 存 Text，聚合在 Python 侧做，必须有上限。
    """

    since: str
    days: int
    scanned: int = 0
    truncated: bool = False
    items: list[PluginUsageItem]
