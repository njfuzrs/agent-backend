"""Pydantic 请求/响应模型"""

from typing import Optional

from pydantic import BaseModel, Field


# ── 轨迹列表项 ──
class TrajectoryListItem(BaseModel):
    session_id: str
    tool_source: str
    model: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_ms: Optional[int] = None
    total_steps: int = 0
    total_api_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    exit_status: str = ""
    tools_used: list[str] = []
    first_prompt: str = ""
    quality_status: str = "unreviewed"
    quality_rating: Optional[int] = None
    traj_file_size: int = 0
    has_thinking: bool = False
    has_sub_agent: bool = False
    task_type: str = ""
    project_name: str = ""
    tags: list[str] = []
    user_id: Optional[str] = None


class TrajectoryListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[TrajectoryListItem]


# ── 轨迹元数据（详情页顶部卡片） ──
class TrajectoryMeta(TrajectoryListItem):
    tokens_sent: int = 0
    tokens_received: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    working_directory: str = ""
    files_edited: list[str] = []
    quality_notes: str = ""
    uploaded_at: str = ""
    updated_at: str = ""


# ── 轨迹详情分段 ──
class TrajectoryStepsResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[dict]


# ── 上传响应 ──
class UploadResponse(BaseModel):
    session_id: str
    status: str  # created / updated / exists / skipped
    metadata: dict = {}
    sha256: str = ""     # 回传给客户端用于二次校验
    oss_key: str = ""    # 存储路径（本地模式为空）


# ── 更新标注 ──
class TrajectoryUpdate(BaseModel):
    quality_rating: Optional[int] = Field(None, ge=1, le=5)
    quality_status: Optional[str] = None
    quality_notes: Optional[str] = None
    task_type: Optional[str] = None
    project_name: Optional[str] = None
    tags: Optional[list[str]] = None


class TrajectoryBatchUpdate(BaseModel):
    session_ids: list[str] = Field(..., min_length=1)
    quality_rating: Optional[int] = Field(None, ge=1, le=5)
    quality_status: Optional[str] = None
    quality_notes: Optional[str] = None
    task_type: Optional[str] = None
    project_name: Optional[str] = None
    tags: Optional[list[str]] = None


class TrajectoryBatchUpdateResponse(BaseModel):
    status: str = "updated"
    updated_count: int = 0
    session_ids: list[str] = []


class DistributionItem(BaseModel):
    name: str
    count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0


class StatsOverviewResponse(BaseModel):
    total_trajectories: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_steps_per_trajectory: float = 0.0
    avg_cost_per_trajectory: float = 0.0
    success_rate: float = 0.0
    tool_source_distribution: dict[str, int] = {}
    model_distribution: dict[str, int] = {}
    quality_distribution: dict[str, int] = {}


class TrendPoint(BaseModel):
    date: str
    count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_steps: float = 0.0
    success_rate: float = 0.0


class TrendsResponse(BaseModel):
    granularity: str
    data: list[TrendPoint] = []


class ToolStatsResponse(BaseModel):
    items: list[DistributionItem] = []


class ModelStatsResponse(BaseModel):
    items: list[DistributionItem] = []


class CostSeriesItem(BaseModel):
    name: str
    values: list[float] = []


class CostTimelinePoint(BaseModel):
    date: str
    total_cost_usd: float = 0.0
    by_tool_source: dict[str, float] = {}


class CostStatsResponse(BaseModel):
    granularity: str
    dates: list[str] = []
    series: list[CostSeriesItem] = []
    totals_by_tool_source: list[DistributionItem] = []
    totals_by_model: list[DistributionItem] = []
    timeline: list[CostTimelinePoint] = []


class TrajectoryExportRequest(BaseModel):
    session_ids: list[str] = Field(..., min_length=1)


class SFTExportRequest(BaseModel):
    session_ids: Optional[list[str]] = None
    tool_source: Optional[str] = None
    model: Optional[str] = None
    exit_status: Optional[str] = None
    task_type: Optional[str] = None
    quality_status: Optional[str] = None
    project_name: Optional[str] = None
    search: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    min_steps: Optional[int] = Field(None, ge=0)
    max_steps: Optional[int] = Field(None, ge=0)
    format: str = Field("messages", pattern="^(messages|tool|xml)$")
    include_thinking: bool = False


class CompareGroupCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = ""
    task_prompt: str = ""


class CompareGroupAddItemsRequest(BaseModel):
    session_ids: list[str] = Field(..., min_length=1)


class CompareGroupAddItemsResponse(BaseModel):
    group_id: int
    added_count: int = 0
    skipped_session_ids: list[str] = []


class CompareGroupListItem(BaseModel):
    id: int
    name: str
    description: str = ""
    task_prompt: str = ""
    created_at: str
    item_count: int = 0
    tool_sources: list[str] = []


class CompareGroupListResponse(BaseModel):
    items: list[CompareGroupListItem] = []


class CompareTrajectoryItem(BaseModel):
    trajectory_id: int
    session_id: str
    tool_source: str
    model: str
    start_time: Optional[str] = None
    duration_ms: Optional[int] = None
    total_steps: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    exit_status: str = ""
    quality_status: str = "unreviewed"
    first_prompt: str = ""
    tools_used: list[str] = []
    tool_usage: dict[str, int] = {}


class CompareGroupDetailResponse(BaseModel):
    id: int
    name: str
    description: str = ""
    task_prompt: str = ""
    created_at: str
    item_count: int = 0
    items: list[CompareTrajectoryItem] = []


class CompareRadarItem(BaseModel):
    trajectory_id: int
    session_id: str
    tool_source: str
    model: str
    values: list[float | str] = []
    normalized: list[float] = []


class CompareRadarResponse(BaseModel):
    group_id: int
    group_name: str
    dimensions: list[str] = []
    items: list[CompareRadarItem] = []


# ── 健康检查 ──
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
