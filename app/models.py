"""SQLAlchemy ORM 模型。

设计原则：
- 一个 Project 包含多个 Experiment
- 一个 Experiment 包含多个 Metric 和 Note
- 时间戳统一 UTC，模板渲染时再转本地

字段命名：保持简洁直接，避免缩写。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, Boolean
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
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Artifact.uploaded_at.desc()",
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
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact",
        back_populates="goal",
        cascade="all, delete-orphan",
        order_by="Artifact.uploaded_at.desc()",
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
        config_md: 超参数 / 配置（Markdown 原文，方便人记;之前是 JSON,2026-08-05 改）
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

    # 超参数 / 配置记录。原本是 JSON dict,2026-08-05 改成 Markdown 文字:
    # 因为主要是为了人记录,JSON 既难写又难读,而且训练脚本自己有自己的配置管理。
    config_md: Mapped[str] = mapped_column(Text, default="")

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
        order_by="Metric.timestamp.asc(), Metric.id.asc()",  # 按记录时间排序,不再按 step
    )
    notes: Mapped[list["Note"]] = relationship(
        "Note",
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="Note.created_at.desc()",  # 最新在前
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        "Artifact",
        back_populates="experiment",
        cascade="all, delete-orphan",
        order_by="Artifact.uploaded_at.desc()",
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
        key: 指标名（如 "loss"、"accuracy"、"psnr_db"）
        value: 数值
        note: 可选的备注 / 上下文（这一轮调了什么,跑的是哪个子集）
        step: 训练步数 / epoch，可空（保留兼容,但 2026-08-05 后不再画曲线）
        timestamp: 记录时刻
    """

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(100), index=True)
    value: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    experiment: Mapped[Experiment] = relationship("Experiment", back_populates="metrics")

    def __repr__(self) -> str:
        return f"<Metric {self.key}={self.value}>"


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


# ---------------------------------------------------------------------------
# 文件与成果（图片、模型权重、其它附件）
# ---------------------------------------------------------------------------


class Artifact(Base):
    """统一存放上传的文件：图片（结果图、示意图）、模型权重（.pt 等）、
    以及任意附件。

    归属：必属于 Project / Goal / Experiment 中的**恰好一个**。
    由路由层校验(数据库 SQLite 对 CHECK 支持有限,用应用层 + 三个独立 nullable FK)。

    字段：
        id: 主键
        project_id / goal_id / experiment_id: 三选一必填
        original_name: 用户上传时的文件名（展示用）
        stored_name: 实际存在磁盘上的唯一文件名（避免冲突和路径注入）
        stored_path: 相对于 artifact_dir 的路径,例如 'projects/3/img_xxx.png'
        kind: image / model / other
        mime_type: 浏览器给的 MIME,例如 'image/png'、'application/octet-stream'
        size_bytes: 文件大小
        description: Markdown 备注（这张图说明了什么 / 这个权重是哪次训练出的）
        uploaded_at: 上传时间
    """

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    goal_id: Mapped[int | None] = mapped_column(
        ForeignKey("goals.id", ondelete="CASCADE"), index=True, nullable=True
    )
    experiment_id: Mapped[int | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), index=True, nullable=True
    )

    original_name: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), index=True)
    stored_path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20), default="other")  # image/model/other
    mime_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project | None"] = relationship("Project", back_populates="artifacts")
    goal: Mapped["Goal | None"] = relationship("Goal", back_populates="artifacts")
    experiment: Mapped["Experiment | None"] = relationship(
        "Experiment", back_populates="artifacts"
    )

    def __repr__(self) -> str:
        return f"<Artifact id={self.id} kind={self.kind!r} name={self.original_name!r}>"


# ---------------------------------------------------------------------------
# 周复盘
# ---------------------------------------------------------------------------


class WeeklyReview(Base):
    """每周一次的复盘笔记。

    字段：
        id: 主键
        week_start_date: 这一周的开始日期（周一），用 date 类型存
        title: 标题（默认 "第 N 周 (YYYY-MM-DD ~ YYYY-MM-DD)"，可手改）
        content: 主体（Markdown，记录这一周干了啥）
        highlights: 这一周的高光（Markdown，简短列表）
        blockers: 遇到的卡点 / 待解决（Markdown）
        next_focus: 下周重点（Markdown）
        created_at / updated_at: UTC 时间
    """

    __tablename__ = "weekly_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    week_start_date: Mapped[datetime] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[str] = mapped_column(Text, default="")
    blockers: Mapped[str] = mapped_column(Text, default="")
    next_focus: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def __repr__(self) -> str:
        return f"<WeeklyReview id={self.id} week_start={self.week_start_date}>"


# ---------------------------------------------------------------------------
# 项目思维导图
# ---------------------------------------------------------------------------


class Mindmap(Base):
    """一个项目对应一张导图（singleton，project_id 唯一）。

    字段：
        id: 主键
        project_id: 所属项目（UNIQUE，保证一对一）
        created_at / updated_at: UTC 时间
    """

    __tablename__ = "mindmaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # 反向关系：画布上的所有节点
    nodes: Mapped[list["MindmapNode"]] = relationship(
        "MindmapNode",
        back_populates="mindmap",
        cascade="all, delete-orphan",
        order_by="MindmapNode.id.asc()",
    )
    edges: Mapped[list["MindmapEdge"]] = relationship(
        "MindmapEdge",
        back_populates="mindmap",
        cascade="all, delete-orphan",
        order_by="MindmapEdge.id.asc()",
    )

    def __repr__(self) -> str:
        return f"<Mindmap id={self.id} project_id={self.project_id}>"


class MindmapNode(Base):
    """画布上的一个节点（形状/文本框/便签等）。

    自动节点（kind='auto'）由 sync_auto_tree 根据 Project/Goal/Experiment
    生成与维护；手动节点（kind='manual'）由用户在画布上自由增删改。
    source_type / source_id 仅 auto 节点使用，用于同步时识别数据源。

    字段：
        id: 主键
        mindmap_id: 所属导图
        kind: 'auto' 或 'manual'
        source_type: 'project' / 'goal' / 'experiment' / None
        source_id: 源实体 PK（仅 auto 节点）
        parent_id: 自引用 FK，自动树用它构造父子关系
        shape_type: 'rect' / 'rounded' / 'ellipse' / 'diamond' / 'hexagon' /
                    'arrow' / 'text' / 'sticky-yellow' / 'sticky-pink' / 'sticky-blue'
        label: 显示文字
        x, y: 画布坐标（左上角）
        w, h: 宽高（arrow 形状固定）
        z_index: 层级
        color: 预留颜色（目前用 shape_type 区分便签色）
        created_at / updated_at: UTC 时间
    """

    __tablename__ = "mindmap_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    mindmap_id: Mapped[int] = mapped_column(
        ForeignKey("mindmaps.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[str] = mapped_column(String(10), default="manual")  # auto/manual

    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("mindmap_nodes.id", ondelete="CASCADE"), nullable=True
    )

    shape_type: Mapped[str] = mapped_column(String(30), default="rect")
    label: Mapped[str] = mapped_column(Text, default="")

    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    w: Mapped[float] = mapped_column(Float, default=120.0)
    h: Mapped[float] = mapped_column(Float, default=60.0)
    z_index: Mapped[int] = mapped_column(Integer, default=0)

    font_size: Mapped[int] = mapped_column(Integer, default=13)
    font_family: Mapped[str] = mapped_column(String(20), default="system")

    color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    mindmap: Mapped[Mindmap] = relationship("Mindmap", back_populates="nodes")
    parent: Mapped["MindmapNode | None"] = relationship(
        "MindmapNode",
        back_populates="children",
        remote_side="MindmapNode.id",
        foreign_keys=[parent_id],
    )
    children: Mapped[list["MindmapNode"]] = relationship(
        "MindmapNode",
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys=[parent_id],
    )

    def __repr__(self) -> str:
        return (
            f"<MindmapNode id={self.id} kind={self.kind!r} "
            f"shape={self.shape_type!r} label={self.label[:20]!r}>"
        )


class MindmapEdge(Base):
    """手动连线（节点之间的有向边）。

    区别于自动树通过 parent_id 渲染的连线（parent-child 关系），
    本表是用户在画布上手动拖出的连线，可自由指定 source/target 和箭头。

    字段：
        id: 主键
        mindmap_id: 所属导图
        source_id: 拖出端节点
        target_id: 箭头指向端节点
        arrow: 端点是否画箭头（用户可切）
        created_at: UTC 时间
    """

    __tablename__ = "mindmap_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    mindmap_id: Mapped[int] = mapped_column(
        ForeignKey("mindmaps.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("mindmap_nodes.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("mindmap_nodes.id", ondelete="CASCADE"), index=True
    )

    arrow: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # 反向引用（不是必须的，但方便日后做图查询）
    mindmap: Mapped[Mindmap] = relationship("Mindmap", back_populates="edges")
    source: Mapped[MindmapNode] = relationship(
        "MindmapNode", foreign_keys=[source_id]
    )
    target: Mapped[MindmapNode] = relationship(
        "MindmapNode", foreign_keys=[target_id]
    )

    def __repr__(self) -> str:
        return (
            f"<MindmapEdge id={self.id} {self.source_id}→{self.target_id} "
            f"arrow={self.arrow}>"
        )