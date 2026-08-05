"""Pydantic schemas：用于 API 序列化与请求验证。

ORM 模型用于数据库；Pydantic 模型用于对外（API、表单）。
这样可以独立演化数据库与接口契约。
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    tags: str = ""
    current_stage: int = 1


class ProjectCreate(ProjectBase):
    """创建项目时的入参。"""


class ProjectUpdate(BaseModel):
    """部分字段更新。"""
    name: str | None = None
    description: str | None = None
    tags: str | None = None
    current_stage: int | None = None


class ProjectRead(ProjectBase):
    id: int
    status: str = "active"
    created_at: datetime
    updated_at: datetime
    experiment_count: int = 0
    artifact_count: int = 0

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Goal (大目标)
# ---------------------------------------------------------------------------


class GoalBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    order: int | None = None


class GoalCreate(GoalBase):
    pass


class GoalUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    order: int | None = None


class GoalRead(GoalBase):
    id: int
    project_id: int
    created_at: datetime
    updated_at: datetime
    experiment_count: int = 0
    artifact_count: int = 0

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------


class ExperimentBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    hypothesis: str = ""
    design_notes: str = ""
    result_summary: str = ""
    # 2026-08-05: 原来是 config (JSON dict),因为主要是为了人记录,改成 Markdown 文字
    config_md: str = ""
    due_date: str | None = None  # ISO 日期字符串
    git_commit: str | None = None
    goal_id: int


class ExperimentCreate(ExperimentBase):
    pass


class ExperimentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    hypothesis: str | None = None
    design_notes: str | None = None
    result_summary: str | None = None
    config_md: str | None = None
    due_date: str | None = None
    git_commit: str | None = None
    goal_id: int | None = None


class ExperimentRead(ExperimentBase):
    id: int
    project_id: int
    created_at: datetime
    updated_at: datetime
    metric_count: int = 0
    note_count: int = 0
    artifact_count: int = 0

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------


class MetricCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    value: float
    # 2026-08-05: 加 note 字段,记录这一行的上下文("这轮改了 lr=0.001" 之类)
    # step 保留兼容(以前可能填过),但 2026-08-05 后不再用来画曲线
    note: str = ""
    step: int | None = None


class MetricRead(BaseModel):
    id: int
    experiment_id: int
    key: str
    value: float
    note: str = ""
    step: int | None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Note
# ---------------------------------------------------------------------------


class NoteCreate(BaseModel):
    content: str = Field(..., min_length=1)


class NoteRead(BaseModel):
    id: int
    experiment_id: int
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


class DecisionCreate(BaseModel):
    content: str = Field(..., min_length=1)
    status: str = "待跟进"


class DecisionUpdate(BaseModel):
    content: str | None = None
    status: str | None = None


class DecisionRead(BaseModel):
    id: int
    project_id: int
    content: str
    status: str
    created_at: datetime
    resolved_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Artifact (文件与成果：图片、模型权重、其他附件)
# ---------------------------------------------------------------------------


class ArtifactBase(BaseModel):
    description: str = ""


class ArtifactCreate(ArtifactBase):
    """API 接收 multipart 上传后,路由层把文件存到磁盘后调用此 schema
    只包含 description(文件名、字节数等元数据从文件对象提取)。"""

    project_id: int | None = None
    goal_id: int | None = None
    experiment_id: int | None = None


class ArtifactUpdate(BaseModel):
    description: str | None = None
    kind: str | None = None  # 允许改 image/model/other


class ArtifactRead(BaseModel):
    id: int
    project_id: int | None
    goal_id: int | None
    experiment_id: int | None
    original_name: str
    kind: str
    mime_type: str
    size_bytes: int
    description: str
    uploaded_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# WeeklyReview (周复盘)
# ---------------------------------------------------------------------------


class WeeklyReviewBase(BaseModel):
    week_start_date: date  # 这一周的开始日期（周一）
    title: str = ""
    content: str = ""
    highlights: str = ""
    blockers: str = ""
    next_focus: str = ""


class WeeklyReviewCreate(WeeklyReviewBase):
    pass


class WeeklyReviewUpdate(BaseModel):
    week_start_date: date | None = None
    title: str | None = None
    content: str | None = None
    highlights: str | None = None
    blockers: str | None = None
    next_focus: str | None = None


class WeeklyReviewRead(WeeklyReviewBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)