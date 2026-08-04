"""Pydantic schemas：用于 API 序列化与请求验证。

ORM 模型用于数据库；Pydantic 模型用于对外（API、表单）。
这样可以独立演化数据库与接口契约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
    config: dict[str, Any] = Field(default_factory=dict)
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
    config: dict[str, Any] | None = None
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

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------


class MetricCreate(BaseModel):
    key: str = Field(..., min_length=1, max_length=100)
    value: float
    step: int | None = None


class MetricRead(BaseModel):
    id: int
    experiment_id: int
    key: str
    value: float
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