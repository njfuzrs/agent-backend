"""SQLAlchemy ORM 模型"""

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    REAL,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class Trajectory(Base):
    __tablename__ = "trajectories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Text, nullable=False, unique=True, index=True)

    # 工具来源
    tool_source = Column(Text, nullable=False, default="claude-code")
    model = Column(Text, nullable=False, default="")

    # 时间
    start_time = Column(Text)
    end_time = Column(Text)
    duration_ms = Column(Integer)

    # Token
    tokens_sent = Column(Integer, default=0)
    tokens_received = Column(Integer, default=0)
    cache_read_tokens = Column(Integer, default=0)
    cache_creation_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    total_cost_usd = Column(REAL, default=0.0)

    # 轨迹
    total_steps = Column(Integer, default=0)
    total_api_calls = Column(Integer, default=0)
    exit_status = Column(Text, default="")
    tools_used = Column(Text, default="[]")       # JSON 数组
    files_edited = Column(Text, default="[]")     # JSON 数组

    # 环境
    working_directory = Column(Text, default="")

    # 分类
    task_type = Column(Text, default="")
    project_name = Column(Text, default="")
    tags = Column(Text, default="[]")             # JSON 数组

    # 质量
    quality_rating = Column(Integer, nullable=True)
    quality_status = Column(Text, default="unreviewed")
    quality_notes = Column(Text, default="")

    # 特征
    has_thinking = Column(Boolean, default=False)
    has_sub_agent = Column(Boolean, default=False)
    first_prompt = Column(Text, default="")

    # 文件
    traj_file_path = Column(Text, nullable=False)
    traj_file_size = Column(Integer, default=0)

    # OSS 存储
    oss_key = Column(Text, nullable=True)           # OSS 存储路径，如 sessions/{sid}/session.traj.gz
    sha256 = Column(Text, nullable=True)            # 上传文件的 SHA256
    file_size = Column(Integer, nullable=True)      # 压缩后文件大小（字节）

    # 多用户标识
    user_id = Column(Text, nullable=True)           # 上传用户标识
    device_id = Column(Text, nullable=True)         # 上传设备标识

    # 软删除
    deleted_at = Column(Text, nullable=True)        # ISO8601 时间戳，NULL 表示未删除

    # 时间戳
    uploaded_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)

    # ---- AI 评分字段 ----
    ai_score = Column(Integer, nullable=True, index=True)                # 综合评分 0-100
    ai_quality_status = Column(Text, default="pending")                  # auto_approved / auto_rejected / needs_review / pending / error
    ai_grade = Column(Text, default="")                                  # A / B / C / D / F

    # 第一层：规则引擎
    rule_score = Column(Integer, nullable=True)
    rule_details = Column(Text, default="{}")                            # JSON: {"R01": 100, ...}
    rule_flags = Column(Text, default="[]")                              # JSON: ["EXCESSIVE_STEPS"]

    # 第二层：启发式分析
    heuristic_score = Column(Integer, nullable=True)
    heuristic_details = Column(Text, default="{}")                       # JSON: {"H01": 90, ...}
    heuristic_patterns = Column(Text, default="[]")                      # JSON: ["good:search_first"]

    # 第三层：LLM 评估
    llm_score = Column(Integer, nullable=True)
    llm_details = Column(Text, default="{}")                             # JSON: {"task_complexity": 80, ...}
    llm_reasoning = Column(Text, default="")
    llm_suggested_task_type = Column(Text, default="")
    llm_eval_model = Column(Text, default="")

    # 评分元信息
    scored_at = Column(Text, nullable=True)
    score_version = Column(Integer, default=0)

    tool_steps = relationship(
        "ToolStep",
        back_populates="trajectory",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_traj_tool_source", "tool_source"),
        Index("idx_traj_model", "model"),
        Index("idx_traj_start_time", "start_time"),
        Index("idx_traj_task_type", "task_type"),
        Index("idx_traj_quality_status", "quality_status"),
        Index("idx_traj_exit_status", "exit_status"),
        Index("idx_traj_project_name", "project_name"),
        Index("idx_traj_user_id", "user_id"),
        Index("idx_traj_device_id", "device_id"),
        Index("idx_traj_deleted_at", "deleted_at"),
        Index("idx_traj_ai_quality_status", "ai_quality_status"),
        Index("idx_traj_ai_grade", "ai_grade"),
        Index("idx_traj_score_version", "score_version"),
    )


class CompareGroup(Base):
    __tablename__ = "compare_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    description = Column(Text, default="")
    task_prompt = Column(Text, default="")
    created_at = Column(Text, nullable=False)

    items = relationship("CompareGroupItem", back_populates="group", cascade="all, delete-orphan")


class CompareGroupItem(Base):
    __tablename__ = "compare_group_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("compare_groups.id", ondelete="CASCADE"), nullable=False)
    trajectory_id = Column(Integer, ForeignKey("trajectories.id", ondelete="CASCADE"), nullable=False)
    notes = Column(Text, default="")

    group = relationship("CompareGroup", back_populates="items")
    trajectory = relationship("Trajectory")

    __table_args__ = (UniqueConstraint("group_id", "trajectory_id"),)


class ToolStep(Base):
    __tablename__ = "tool_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trajectory_id = Column(Integer, ForeignKey("trajectories.id", ondelete="CASCADE"), nullable=False)
    step_index = Column(Integer, nullable=False)
    message_type = Column(Text, nullable=False)
    tool_name = Column(Text, nullable=True)
    tool_input_summary = Column(Text, default="")
    is_error = Column(Boolean, default=False)
    timestamp = Column(Text, nullable=True)

    trajectory = relationship("Trajectory", back_populates="tool_steps")

    __table_args__ = (
        Index("idx_steps_traj_id", "trajectory_id"),
        Index("idx_steps_tool_name", "tool_name"),
    )
