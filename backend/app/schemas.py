"""Pydantic 请求/响应模型"""

from pydantic import BaseModel, Field
from typing import Optional


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


# ── 健康检查 ──
class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
