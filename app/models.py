"""SQLAlchemy ORM 模型。

设计原则：
- 一个 Project 包含多个 Experiment
- 一个 Experiment 包含多个 Metric 和 Note
- 时间戳统一 UTC，模板渲染时再转本地

字段命名：保持简洁直接，避免缩写。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ---------------------------------------------------------------------------
# 项目
# ---------------------------------------------------------------------------


class Project(Base):
    """一个研究 / 工程方向，如"声学成像"、"SSL 文献"。

    字段：
        id: 主键
        name: 项目名（唯一，便于识别）
        description: 项目简介
        tags: 标签，逗号分隔字符串（简单够用）
        status: active / paused / completed / archived
        created_at / updated_at: UTC 时间
    """

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")

    # 当前流水线阶段 1-8（1 文献调研 / 2 研究问题 / 3 工况设计 / 4 数值模拟
    # / 5 试验验证 / 6 数据分析 / 7 论文写作 / 8 投稿与修回）
    current_stage: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # 反向关系：项目下的所有大目标
    goals: Mapped[list["Goal"]] = relationship(
        "Goal",
        back_populates="project",
        cascade="all, delete-orphan",  # 删项目时连目标一起删
        order_by="Goal.order.asc().nulls_last(), Goal.created_at.asc()",
    )
    # 反向关系：项目下未归属大目标的实验（兼容旧数据 / 兜底）
    experiments: Mapped[list["Experiment"]] = relationship(
        "Experiment",
        back_populates="project",
        cascade="all, delete-orphan",  # 删项目时连实验一起删
    )
    decisions: Mapped[list["Decision"]] = relationship(
        "Decision",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Decision.created_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<Project id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# 大目标
# ---------------------------------------------------------------------------


class Goal(Base):
    """项目下需要完成的若干个大目标。

    字段：
        id: 主键
        project_id: 所属项目
        name: 目标名（如 "12×12 阵列声源定位算法选型"）
        description: 目标描述 / 完成标准
        status: pending / active / completed / abandoned
        priority: 高 / 中 / 低
        order: 手动排序（小的在前），可空
    """

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/active/completed/abandoned
    priority: Mapped[str] = mapped_column(String(10), default="中")
    order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    project: Mapped[Project] = relationship("Project", back_populates="goals")
    experiments: Mapped[list["Experiment"]] = relationship(
        "Experiment",
        back_populates="goal",
        cascade="all, delete-orphan",  # 删目标时连实验一起删
        order_by="Experiment.created_at.asc()",
    )

    def __repr__(self) -> str:
        return f"<Goal id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# 实验
# ---------------------------------------------------------------------------


class Experiment(Base):
    """大目标下的一次具体实验（小目标）。

    字段：
        id: 主键
        project_id: 所属项目
        goal_id: 所属大目标（必填）
        name: 实验名（如 "IASA-2D-12x12-1"）
        description: 简介
        hypothesis: 实验设计意图 / 想达到什么
        design_notes: 实验设计备注（参数选择依据、方案等）
        result_summary: 实验结果小结（Markdown）
        config: 超参数 / 配置（JSON，灵活存储）
        status: draft / running / completed / failed
        git_commit: 可选的当前 commit hash
    """

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    goal_id: Mapped[int | None] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    design_notes: Mapped[str] = mapped_column(Text, default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")

    # JSON 列：存任意嵌套配置
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default="draft")
    priority: Mapped[str] = mapped_column(String(10), default="中")  # 高/中/低
    due_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    project: Mapped[Project] = relationship("Project", back_populates="experiments")
    goal: Mapped["Goal | None"] = relationship("Goal", back_populates="experiments")
    metrics: Mapped[list["Metric"]] = relationship(
        "Metric",
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="Metric.step",  # 默认按 step 排序
    )
    notes: Mapped[list["Note"]] = relationship(
        "Note",
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="Note.created_at.desc()",  # 最新在前
    )

    def __repr__(self) -> str:
        return f"<Experiment id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


class Metric(Base):
    """一次实验过程中记录的一个数据点。

    字段：
        id: 主键
        experiment_id: 所属实验
        key: 指标名（如 "loss"、"accuracy"）
        value: 数值
        step: 训练步数 / epoch，可空
    """

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[float] = mapped_column(Float)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    experiment: Mapped[Experiment] = relationship("Experiment", back_populates="metrics")

    def __repr__(self) -> str:
        return f"<Metric {self.key}={self.value} step={self.step}>"


# ---------------------------------------------------------------------------
# 笔记
# ---------------------------------------------------------------------------


class Note(Base):
    """实验的 Markdown 笔记。

    字段：
        id: 主键
        experiment_id: 所属实验
        content: Markdown 原文
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    experiment: Mapped[Experiment] = relationship("Experiment", back_populates="notes")

    def __repr__(self) -> str:
        return f"<Note id={self.id} exp_id={self.experiment_id}>"


# ---------------------------------------------------------------------------
# 决策（待跟进事项）
# ---------------------------------------------------------------------------


class Decision(Base):
    """项目下的开放决策 / 待回答问题。

    字段：
        id: 主键
        project_id: 所属项目
        content: 决策内容（一个问题或待办决策）
        status: 待跟进 / 已解决
    """

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="待跟进")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped[Project] = relationship("Project", back_populates="decisions")

    def __repr__(self) -> str:
        return f"<Decision id={self.id} status={self.status}>"