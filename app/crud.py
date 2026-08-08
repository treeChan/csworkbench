"""数据库 CRUD 操作。

所有函数都接收 Session 作为第一个参数，便于在请求作用域内复用，
也方便测试时 mock。
"""

from __future__ import annotations

import secrets
from datetime import datetime, date
from pathlib import Path
from typing import Sequence

from fastapi import UploadFile
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import get_artifact_dir


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


def list_projects(db: Session) -> Sequence[models.Project]:
    """列出所有项目，按更新时间倒序。"""
    stmt = select(models.Project).order_by(models.Project.updated_at.desc())
    return db.scalars(stmt).all()


def get_project(db: Session, project_id: int) -> models.Project | None:
    return db.get(models.Project, project_id)


def get_project_by_name(db: Session, name: str) -> models.Project | None:
    stmt = select(models.Project).where(models.Project.name == name)
    return db.scalars(stmt).first()


def create_project(db: Session, data: schemas.ProjectCreate) -> models.Project:
    project = models.Project(
        name=data.name,
        description=data.description,
        tags=data.tags,
        current_stage=data.current_stage,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(
    db: Session, project: models.Project, data: schemas.ProjectUpdate
) -> models.Project:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project: models.Project) -> None:
    db.delete(project)
    db.commit()


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------


def list_experiments(
    db: Session, project_id: int
) -> Sequence[models.Experiment]:
    stmt = (
        select(models.Experiment)
        .where(models.Experiment.project_id == project_id)
        .order_by(models.Experiment.updated_at.desc())
    )
    return db.scalars(stmt).all()


def get_experiment(db: Session, experiment_id: int) -> models.Experiment | None:
    return db.get(models.Experiment, experiment_id)


def _parse_date(s: str | date | None) -> date | None:
    if s is None:
        return None
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def create_experiment(
    db: Session, project_id: int, data: schemas.ExperimentCreate
) -> models.Experiment:
    exp = models.Experiment(
        project_id=project_id,
        goal_id=data.goal_id,
        name=data.name,
        description=data.description,
        hypothesis=data.hypothesis,
        design_notes=data.design_notes,
        result_summary=data.result_summary,
        # 2026-08-05: config (JSON dict) → config_md (Markdown 文字)
        config_md=data.config_md,
        due_date=_parse_date(data.due_date),
        git_commit=data.git_commit,
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp


def update_experiment(
    db: Session, experiment: models.Experiment, data: schemas.ExperimentUpdate
) -> models.Experiment:
    raw = data.model_dump(exclude_unset=True)
    if "due_date" in raw:
        raw["due_date"] = _parse_date(raw["due_date"])
    for field, value in raw.items():
        setattr(experiment, field, value)
    db.commit()
    db.refresh(experiment)
    return experiment


def delete_experiment(db: Session, experiment: models.Experiment) -> None:
    db.delete(experiment)
    db.commit()


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------


def list_metrics(
    db: Session, experiment_id: int
) -> Sequence[models.Metric]:
    # 2026-08-05: 不再按 step 排序画曲线,按记录时间排就行
    stmt = (
        select(models.Metric)
        .where(models.Metric.experiment_id == experiment_id)
        .order_by(models.Metric.timestamp.asc(), models.Metric.id.asc())
    )
    return db.scalars(stmt).all()


def create_metric(
    db: Session, experiment_id: int, data: schemas.MetricCreate
) -> models.Metric:
    metric = models.Metric(
        experiment_id=experiment_id,
        key=data.key,
        value=data.value,
        note=data.note,
        step=data.step,
    )
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


def delete_metric(db: Session, metric: models.Metric) -> None:
    db.delete(metric)
    db.commit()


# ---------------------------------------------------------------------------
# Note
# ---------------------------------------------------------------------------


def list_notes(db: Session, experiment_id: int) -> Sequence[models.Note]:
    stmt = (
        select(models.Note)
        .where(models.Note.experiment_id == experiment_id)
        .order_by(models.Note.created_at.desc())
    )
    return db.scalars(stmt).all()


def create_note(
    db: Session, experiment_id: int, data: schemas.NoteCreate
) -> models.Note:
    note = models.Note(experiment_id=experiment_id, content=data.content)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def delete_note(db: Session, note: models.Note) -> None:
    db.delete(note)
    db.commit()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def count_experiments(db: Session, project_id: int) -> int:
    stmt = select(func.count(models.Experiment.id)).where(
        models.Experiment.project_id == project_id
    )
    return int(db.scalar(stmt) or 0)


def count_metrics(db: Session, experiment_id: int) -> int:
    stmt = select(func.count(models.Metric.id)).where(
        models.Metric.experiment_id == experiment_id
    )
    return int(db.scalar(stmt) or 0)


def count_notes(db: Session, experiment_id: int) -> int:
    stmt = select(func.count(models.Note.id)).where(
        models.Note.experiment_id == experiment_id
    )
    return int(db.scalar(stmt) or 0)


# ---------------------------------------------------------------------------
# Goal
# ---------------------------------------------------------------------------


def list_goals(db: Session, project_id: int) -> Sequence[models.Goal]:
    """列项目下的所有大目标，按 order 升序、再按创建时间。"""
    stmt = (
        select(models.Goal)
        .where(models.Goal.project_id == project_id)
        .order_by(
            models.Goal.order.asc().nulls_last(),
            models.Goal.created_at.asc(),
        )
    )
    return db.scalars(stmt).all()


def get_goal(db: Session, goal_id: int) -> models.Goal | None:
    return db.get(models.Goal, goal_id)


def create_goal(
    db: Session, project_id: int, data: schemas.GoalCreate
) -> models.Goal:
    goal = models.Goal(
        project_id=project_id,
        name=data.name,
        description=data.description,
        order=data.order,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def update_goal(
    db: Session, goal: models.Goal, data: schemas.GoalUpdate
) -> models.Goal:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(goal, field, value)
    db.commit()
    db.refresh(goal)
    return goal


def delete_goal(db: Session, goal: models.Goal) -> None:
    db.delete(goal)
    db.commit()


def count_experiments_by_goal(db: Session, goal_id: int) -> int:
    stmt = select(func.count(models.Experiment.id)).where(
        models.Experiment.goal_id == goal_id
    )
    return int(db.scalar(stmt) or 0)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def list_open_decisions(db: Session, limit: int = 20) -> Sequence[models.Decision]:
    """列出所有未关闭的决策（最近优先）。"""
    stmt = (
        select(models.Decision)
        .where(models.Decision.status == "待跟进")
        .order_by(models.Decision.created_at.desc())
        .limit(limit)
    )
    return db.scalars(stmt).all()


def list_decisions(db: Session, project_id: int) -> Sequence[models.Decision]:
    stmt = (
        select(models.Decision)
        .where(models.Decision.project_id == project_id)
        .order_by(models.Decision.created_at.desc())
    )
    return db.scalars(stmt).all()


def get_decision(db: Session, decision_id: int) -> models.Decision | None:
    return db.get(models.Decision, decision_id)


def create_decision(
    db: Session, project_id: int, data: schemas.DecisionCreate
) -> models.Decision:
    dec = models.Decision(
        project_id=project_id,
        content=data.content,
    )
    db.add(dec)
    db.commit()
    db.refresh(dec)
    return dec


def resolve_decision(db: Session, decision: models.Decision) -> models.Decision:
    decision.status = "已解决"
    decision.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(decision)
    return decision


def delete_decision(db: Session, decision: models.Decision) -> None:
    db.delete(decision)
    db.commit()


# ---------------------------------------------------------------------------
# Artifact (文件与成果)
# ---------------------------------------------------------------------------


# 已知是模型权重的文件后缀（小写）
_MODEL_EXTS = {".pt", ".pth", ".onnx", ".h5", ".hdf5", ".safetensors", ".bin", ".ckpt", ".pb"}


def _classify_kind(original_name: str, mime_type: str | None) -> str:
    """根据文件名/MIME 推断 kind:image / model / other。"""
    if mime_type and mime_type.startswith("image/"):
        return "image"
    # 用后缀兜底
    suffix = Path(original_name).suffix.lower()
    if suffix in _MODEL_EXTS:
        return "model"
    if mime_type and mime_type == "application/octet-stream":
        return "model" if suffix in _MODEL_EXTS else "other"
    return "other"


def _owner_subdir(owner_kind: str, owner_id: int) -> str:
    """在 artifact_dir 下的子目录名,例如 'project_3'。"""
    return f"{owner_kind}_{owner_id}"


def _safe_filename(original: str) -> str:
    """生成不冲突、不可路径注入的文件名。保留原后缀,前缀随机。"""
    suffix = Path(original).suffix.lower()
    # 防止后缀被注入目录分隔符 —— 只保留最后一段
    safe_suffix = "".join(c for c in suffix if c.isalnum() or c in ".~")
    return f"{secrets.token_hex(12)}{safe_suffix}"


def save_artifact_file(
    file: UploadFile,
    owner_kind: str,  # 'project' / 'goal' / 'experiment'
    owner_id: int,
) -> tuple[str, str, int, str]:
    """把上传的文件写到磁盘,返回 (stored_relative_path, stored_name, size_bytes, mime_type)。

    写入路径:{artifact_dir}/{owner_kind}_{owner_id}/{stored_name}
    """
    artifact_dir = get_artifact_dir()
    sub = artifact_dir / _owner_subdir(owner_kind, owner_id)
    sub.mkdir(parents=True, exist_ok=True)

    stored_name = _safe_filename(file.filename or "upload")
    dest = sub / stored_name

    # 一次性读取再写,大文件会占内存但 UI 不让传超大的,够用
    content = file.file.read()
    size = len(content)
    dest.write_bytes(content)
    file.file.seek(0)  # 复位方便上层再用

    rel_path = f"{_owner_subdir(owner_kind, owner_id)}/{stored_name}"
    mime = file.content_type or "application/octet-stream"
    return rel_path, stored_name, size, mime


def delete_artifact_file(stored_path: str) -> None:
    """从磁盘删除文件。不存在不报错(幂等)。"""
    artifact_dir = get_artifact_dir()
    target = artifact_dir / stored_path
    try:
        if target.is_file():
            target.unlink()
        # 顺手清空空目录(不要清非空目录,可能还有别的文件)
        parent = target.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        # 文件 IO 失败不能阻塞主流程
        pass


def create_artifact(
    db: Session,
    file: UploadFile,
    *,
    owner_kind: str,
    owner_id: int,
    description: str = "",
) -> models.Artifact:
    """保存文件到磁盘并写入 Artifact 行。原子性:文件写失败就不建 DB 行。"""
    rel_path, stored_name, size, mime = save_artifact_file(
        file, owner_kind, owner_id
    )
    kind = _classify_kind(file.filename or "", mime)

    kwargs: dict = {
        "original_name": file.filename or stored_name,
        "stored_name": stored_name,
        "stored_path": rel_path,
        "kind": kind,
        "mime_type": mime,
        "size_bytes": size,
        "description": description,
    }
    if owner_kind == "project":
        kwargs["project_id"] = owner_id
    elif owner_kind == "goal":
        kwargs["goal_id"] = owner_id
    elif owner_kind == "experiment":
        kwargs["experiment_id"] = owner_id
    else:
        raise ValueError(f"未知的 owner_kind: {owner_kind}")

    artifact = models.Artifact(**kwargs)
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def get_artifact(db: Session, artifact_id: int) -> models.Artifact | None:
    return db.get(models.Artifact, artifact_id)


def list_artifacts(
    db: Session,
    *,
    project_id: int | None = None,
    goal_id: int | None = None,
    experiment_id: int | None = None,
    kind: str | None = None,
) -> Sequence[models.Artifact]:
    """按归属与类型筛列。不传条件就列全部。"""
    stmt = select(models.Artifact)
    if project_id is not None:
        stmt = stmt.where(models.Artifact.project_id == project_id)
    if goal_id is not None:
        stmt = stmt.where(models.Artifact.goal_id == goal_id)
    if experiment_id is not None:
        stmt = stmt.where(models.Artifact.experiment_id == experiment_id)
    if kind is not None:
        stmt = stmt.where(models.Artifact.kind == kind)
    stmt = stmt.order_by(models.Artifact.uploaded_at.desc())
    return db.scalars(stmt).all()


def delete_artifact(db: Session, artifact: models.Artifact) -> None:
    """先删磁盘文件,再删 DB 行。"""
    stored_path = artifact.stored_path
    db.delete(artifact)
    db.commit()
    delete_artifact_file(stored_path)


def update_artifact(
    db: Session, artifact: models.Artifact, data: schemas.ArtifactUpdate
) -> models.Artifact:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(artifact, field, value)
    db.commit()
    db.refresh(artifact)
    return artifact


def count_artifacts_by_project(db: Session, project_id: int) -> int:
    stmt = select(func.count(models.Artifact.id)).where(
        models.Artifact.project_id == project_id
    )
    return int(db.scalar(stmt) or 0)


def count_artifacts_by_goal(db: Session, goal_id: int) -> int:
    stmt = select(func.count(models.Artifact.id)).where(
        models.Artifact.goal_id == goal_id
    )
    return int(db.scalar(stmt) or 0)


def count_artifacts_by_experiment(db: Session, experiment_id: int) -> int:
    stmt = select(func.count(models.Artifact.id)).where(
        models.Artifact.experiment_id == experiment_id
    )
    return int(db.scalar(stmt) or 0)


# ---------------------------------------------------------------------------
# WeeklyReview (周复盘)
# ---------------------------------------------------------------------------


def list_weekly_reviews(
    db: Session, limit: int = 50
) -> Sequence[models.WeeklyReview]:
    stmt = (
        select(models.WeeklyReview)
        .order_by(models.WeeklyReview.week_start_date.desc())
        .limit(limit)
    )
    return db.scalars(stmt).all()


def get_weekly_review(
    db: Session, review_id: int
) -> models.WeeklyReview | None:
    return db.get(models.WeeklyReview, review_id)


def create_weekly_review(
    db: Session, data: schemas.WeeklyReviewCreate
) -> models.WeeklyReview:
    review = models.WeeklyReview(
        week_start_date=data.week_start_date,
        title=data.title,
        content=data.content,
        highlights=data.highlights,
        blockers=data.blockers,
        next_focus=data.next_focus,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def update_weekly_review(
    db: Session,
    review: models.WeeklyReview,
    data: schemas.WeeklyReviewUpdate,
) -> models.WeeklyReview:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(review, field, value)
    db.commit()
    db.refresh(review)
    return review


def delete_weekly_review(db: Session, review: models.WeeklyReview) -> None:
    db.delete(review)
    db.commit()


# ---------------------------------------------------------------------------
# Dashboard 聚合
# ---------------------------------------------------------------------------


def count_all_projects(db: Session) -> int:
    """全局项目数。"""
    stmt = select(func.count(models.Project.id))
    return int(db.scalar(stmt) or 0)


def count_all_experiments(db: Session) -> int:
    """全局实验数。"""
    stmt = select(func.count(models.Experiment.id))
    return int(db.scalar(stmt) or 0)


def count_results_uploaded(db: Session) -> int:
    """全局已上传结果的实验数 (result_summary 非空)。"""
    stmt = select(func.count(models.Experiment.id)).where(
        (models.Experiment.result_summary.isnot(None))
        & (models.Experiment.result_summary != "")
    )
    return int(db.scalar(stmt) or 0)


def count_open_decisions(db: Session) -> int:
    """全局未关闭的决策数。"""
    stmt = select(func.count(models.Decision.id)).where(
        models.Decision.status == "待跟进"
    )
    return int(db.scalar(stmt) or 0)


def get_project_stats(db: Session, project_id: int) -> dict:
    """单个项目的 dashboard 统计: 目标数 / 实验数 / 已出结果数 / 最早待传截止日。"""
    goal_count = int(
        db.scalar(
            select(func.count(models.Goal.id)).where(
                models.Goal.project_id == project_id
            )
        )
        or 0
    )
    exp_count = int(
        db.scalar(
            select(func.count(models.Experiment.id)).where(
                models.Experiment.project_id == project_id
            )
        )
        or 0
    )
    done_count = int(
        db.scalar(
            select(func.count(models.Experiment.id)).where(
                models.Experiment.project_id == project_id,
                models.Experiment.result_summary.isnot(None),
                models.Experiment.result_summary != "",
            )
        )
        or 0
    )
    pending_count = exp_count - done_count

    # 该项目下最早的一个待传截止日期（结果未上传的实验）
    earliest_due: date | None = None
    next_due_exp_id: int | None = None
    next_due_exp_name: str | None = None
    if pending_count > 0:
        stmt = (
            select(models.Experiment)
            .where(
                models.Experiment.project_id == project_id,
                (models.Experiment.result_summary.is_(None))
                | (models.Experiment.result_summary == ""),
            )
            .order_by(models.Experiment.due_date.asc().nulls_last())
            .limit(1)
        )
        next_exp = db.scalars(stmt).first()
        if next_exp is not None and next_exp.due_date is not None:
            earliest_due = next_exp.due_date
            next_due_exp_id = next_exp.id
            next_due_exp_name = next_exp.name

    artifact_count = count_artifacts_by_project(db, project_id)

    return {
        "goal_count": goal_count,
        "exp_count": exp_count,
        "done_count": done_count,
        "pending_count": pending_count,
        "earliest_due": earliest_due,
        "next_due_exp_id": next_due_exp_id,
        "next_due_exp_name": next_due_exp_name,
        "artifact_count": artifact_count,
    }


def list_priority_experiments(
    db: Session, limit: int = 6
) -> Sequence[models.Experiment]:
    """列出尚无结果的实验（=待办），按更新时间倒序。"""
    stmt = (
        select(models.Experiment)
        .where(
            (models.Experiment.result_summary.is_(None))
            | (models.Experiment.result_summary == "")
        )
        .order_by(models.Experiment.updated_at.desc())
        .limit(limit)
    )
    return db.scalars(stmt).all()


def list_pending_experiments(
    db: Session, limit: int = 10
) -> Sequence[models.Experiment]:
    """待处理实验 = 尚未上传结果的实验，按截止日期排。"""
    stmt = (
        select(models.Experiment)
        .where(
            (models.Experiment.result_summary.is_(None))
            | (models.Experiment.result_summary == "")
        )
        .order_by(
            models.Experiment.due_date.asc().nulls_last(),
            models.Experiment.updated_at.desc(),
        )
        .limit(limit)
    )
    return db.scalars(stmt).all()


def recent_activity(
    db: Session, limit: int = 10
) -> list[dict]:
    """综合最近活动：实验创建 / 笔记新增 / 指标记录 / 文件上传。"""
    activities: list[dict] = []

    # 最新实验
    for exp in db.scalars(
        select(models.Experiment)
        .order_by(models.Experiment.created_at.desc())
        .limit(limit)
    ):
        activities.append(
            {
                "type": "实验",
                "type_icon": "flask",
                "content": f"创建实验：{exp.name}",
                "time": exp.created_at,
                "status": exp.status,
                "link": f"/experiments/{exp.id}",
            }
        )

    # 最新笔记
    for note in db.scalars(
        select(models.Note)
        .order_by(models.Note.created_at.desc())
        .limit(limit)
    ):
        snippet = note.content.replace("\n", " ")[:50]
        activities.append(
            {
                "type": "笔记",
                "type_icon": "pencil",
                "content": snippet,
                "time": note.created_at,
                "status": "已添加",
                "link": f"/experiments/{note.experiment_id}",
            }
        )

    # 最新上传
    for art in db.scalars(
        select(models.Artifact)
        .order_by(models.Artifact.uploaded_at.desc())
        .limit(limit)
    ):
        activities.append(
            {
                "type": "文件",
                "type_icon": "paperclip",
                "content": f"上传文件：{art.original_name}",
                "time": art.uploaded_at,
                "status": art.kind,
                "link": f"/artifacts/{art.id}",
            }
        )

    # 按时间倒序，取前 limit 条
    activities.sort(key=lambda x: x["time"], reverse=True)
    return activities[:limit]


# ---------------------------------------------------------------------------
# Search（搜索）
# ---------------------------------------------------------------------------


def _snip(text: str | None, limit: int = 80) -> str:
    """把正文压成一行摘要，超长截断。"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def search_all(
    db: Session, query: str, fulltext: bool = False, limit: int = 20
) -> list[dict]:
    """全库搜索，按实体分组。返回 list[dict] 供 /search 模板直接渲染。

    默认「快速搜索」只匹配标题/名称类字段；fulltext=True 时追加正文 LIKE。
    个人数据量小，SQL LIKE 全扫毫秒级返回，不需要 FTS5、不需要进度条。
    """
    kw = f"%{query}%"
    groups: list[dict] = []

    # --- ① 项目：name（快速）；+ description / tags（全文）---
    p_where = models.Project.name.like(kw)
    if fulltext:
        p_where = or_(
            p_where,
            models.Project.description.like(kw),
            models.Project.tags.like(kw),
        )
    projects = db.scalars(
        select(models.Project)
        .where(p_where)
        .order_by(models.Project.updated_at.desc())
        .limit(limit)
    ).all()
    if projects:
        groups.append(
            {
                "key": "projects",
                "label": "项目",
                "icon": "📁",
                "items": [
                    {
                        "title": p.name,
                        "badge": p.status,
                        "snippet": _snip(p.description),
                        "link": f"/projects/{p.id}",
                    }
                    for p in projects
                ],
            }
        )

    # --- ② 实验与目标：name（快速）；+ 正文（全文）---
    e_where = models.Experiment.name.like(kw)
    if fulltext:
        e_where = or_(
            e_where,
            models.Experiment.description.like(kw),
            models.Experiment.hypothesis.like(kw),
            models.Experiment.design_notes.like(kw),
            models.Experiment.result_summary.like(kw),
            models.Experiment.config_md.like(kw),
        )
    exps = db.scalars(
        select(models.Experiment)
        .where(e_where)
        .order_by(models.Experiment.updated_at.desc())
        .limit(limit)
    ).all()
    g_where = models.Goal.name.like(kw)
    if fulltext:
        g_where = or_(g_where, models.Goal.description.like(kw))
    goals = db.scalars(
        select(models.Goal)
        .where(g_where)
        .order_by(models.Goal.updated_at.desc())
        .limit(limit)
    ).all()
    items = [
        {
            "title": e.name,
            "badge": "实验",
            "snippet": _snip(e.hypothesis or e.description),
            "link": f"/experiments/{e.id}",
        }
        for e in exps
    ] + [
        {
            "title": g.name,
            "badge": "大目标",
            "snippet": _snip(g.description),
            "link": f"/projects/{g.project_id}",
        }
        for g in goals
    ]
    if items:
        groups.append({"key": "exp_goal", "label": "实验与目标", "icon": "🧪", "items": items})

    # --- ③ 文件：original_name（快速）；+ description（全文）---
    a_where = models.Artifact.original_name.like(kw)
    if fulltext:
        a_where = or_(a_where, models.Artifact.description.like(kw))
    arts = db.scalars(
        select(models.Artifact)
        .where(a_where)
        .order_by(models.Artifact.uploaded_at.desc())
        .limit(limit)
    ).all()
    if arts:
        goal_map = {g.id: g for g in db.scalars(select(models.Goal))}
        groups.append(
            {
                "key": "artifacts",
                "label": "文件",
                "icon": "📦",
                "items": [
                    {
                        "title": a.original_name,
                        "badge": a.kind,
                        "snippet": _snip(a.description),
                        "link": (
                            f"/experiments/{a.experiment_id}"
                            if a.experiment_id
                            else (
                                f"/projects/{goal_map[a.goal_id].project_id}"
                                if a.goal_id and a.goal_id in goal_map
                                else f"/projects/{a.project_id}"
                            )
                        ),
                        "download": f"/api/artifacts/{a.id}/download",
                    }
                    for a in arts
                ],
            }
        )

    # --- ④ 笔记与决策：Decision.content（快速）；Note.content（全文）---
    decs = db.scalars(
        select(models.Decision)
        .where(models.Decision.content.like(kw))
        .order_by(models.Decision.created_at.desc())
        .limit(limit)
    ).all()
    notes: list[models.Note] = []
    if fulltext:
        notes = db.scalars(
            select(models.Note)
            .where(models.Note.content.like(kw))
            .order_by(models.Note.created_at.desc())
            .limit(limit)
        ).all()
    items = [
        {
            "title": _snip(d.content, 50),
            "badge": "决策",
            "snippet": _snip(d.content),
            "link": f"/projects/{d.project_id}",
        }
        for d in decs
    ] + [
        {
            "title": _snip(n.content, 50),
            "badge": "笔记",
            "snippet": _snip(n.content),
            "link": f"/experiments/{n.experiment_id}",
        }
        for n in notes
    ]
    if items:
        groups.append(
            {"key": "notes_decisions", "label": "笔记与决策", "icon": "📝", "items": items}
        )

    # --- ⑤ 周复盘：title（快速）；+ 正文（全文）---
    w_where = models.WeeklyReview.title.like(kw)
    if fulltext:
        w_where = or_(
            w_where,
            models.WeeklyReview.content.like(kw),
            models.WeeklyReview.highlights.like(kw),
            models.WeeklyReview.blockers.like(kw),
            models.WeeklyReview.next_focus.like(kw),
        )
    reviews = db.scalars(
        select(models.WeeklyReview)
        .where(w_where)
        .order_by(models.WeeklyReview.week_start_date.desc())
        .limit(limit)
    ).all()
    if reviews:
        groups.append(
            {
                "key": "reviews",
                "label": "周复盘",
                "icon": "📅",
                "items": [
                    {
                        "title": r.title or f"第 {r.week_start_date.isocalendar()[1]} 周",
                        "badge": r.week_start_date.isoformat(),
                        "snippet": _snip(r.highlights or r.content),
                        "link": f"/review/{r.id}",
                    }
                    for r in reviews
                ],
            }
        )

    return groups


# ---------------------------------------------------------------------------
# Mindmap（项目思维导图）
# ---------------------------------------------------------------------------


# 自动树布局参数（首次自动生成节点时算坐标；之后用户拖拽位置不再覆盖）
_AUTO_NODE_W = 160.0
_AUTO_NODE_H = 64.0
_AUTO_GAP_X = 80.0  # 兄弟间水平间距
_AUTO_GAP_Y = 32.0  # 父-子间垂直间距


def _layout_subtree(node_width: float, node_height: float) -> tuple[dict, float]:
    """返回子树每个节点的 (x, y) 和子树总宽。

    递归算：从叶子往根累加宽度。父节点 x = 子树宽/2 - node_width/2。
    简化版：每个节点宽 = _AUTO_NODE_W，子树宽 = max(节点宽, 子树宽之和 + 间距)。
    """
    return ({}, _AUTO_NODE_W)


def _compute_tree_layout(
    project_id: int,
    project_name: str,
    goals: list[models.Goal],
    experiments_by_goal: dict[int, list[models.Experiment]],
    root_x: float = 80.0,
    root_y: float = 80.0,
) -> dict[tuple[str, int], tuple[float, float, float, float]]:
    """算每个 (source_type, source_id) 的初始 (x, y, w, h)。

    简单 BFS 横向布局：root 在 (root_x, root_y)；每个 goal 在 root 右边、
    垂直堆叠；每个 experiment 在所属 goal 右边再堆叠。
    返回 {(source_type, source_id): (x, y, w, h)}。
    """
    layout: dict[tuple[str, int], tuple[float, float, float, float]] = {}

    # Project 根节点
    layout[("project", project_id)] = (root_x, root_y, _AUTO_NODE_W, _AUTO_NODE_H)

    # 大目标：垂直堆叠在 root 右边
    goal_x = root_x + _AUTO_NODE_W + _AUTO_GAP_X
    goal_y = root_y
    for g in goals:
        layout[("goal", g.id)] = (goal_x, goal_y, _AUTO_NODE_W, _AUTO_NODE_H)
        goal_y += _AUTO_NODE_H + _AUTO_GAP_Y

    # 实验：每个 goal 右边垂直堆叠
    exp_x = goal_x + _AUTO_NODE_W + _AUTO_GAP_X
    max_exp_bottom = root_y
    for g in goals:
        exps = experiments_by_goal.get(g.id, [])
        exp_y = goal_y_for_goal = layout[("goal", g.id)][1]
        for e in exps:
            layout[("experiment", e.id)] = (exp_x, exp_y, _AUTO_NODE_W, _AUTO_NODE_H)
            exp_y += _AUTO_NODE_H + _AUTO_GAP_Y
        if exp_y > max_exp_bottom:
            max_exp_bottom = exp_y

    return layout


def get_or_create_mindmap(db: Session, project_id: int) -> models.Mindmap:
    """一个项目一张导图，没有就建。"""
    mm = db.execute(
        select(models.Mindmap).where(models.Mindmap.project_id == project_id)
    ).scalar_one_or_none()
    if mm is not None:
        return mm
    mm = models.Mindmap(project_id=project_id)
    db.add(mm)
    db.commit()
    db.refresh(mm)
    return mm


def list_nodes(db: Session, mindmap_id: int) -> Sequence[models.MindmapNode]:
    """取导图所有节点，按 id 升序（保证父在前子在后，前端构树方便）。"""
    stmt = (
        select(models.MindmapNode)
        .where(models.MindmapNode.mindmap_id == mindmap_id)
        .order_by(models.MindmapNode.id.asc())
    )
    return db.scalars(stmt).all()


def get_node(db: Session, node_id: int) -> models.MindmapNode | None:
    return db.get(models.MindmapNode, node_id)


def create_node(
    db: Session, mindmap_id: int, data: schemas.MindmapNodeCreate
) -> models.MindmapNode:
    """创建 manual 节点。自动树节点不通过这个函数建，由 sync_auto_tree 负责。"""
    node = models.MindmapNode(
        mindmap_id=mindmap_id,
        kind="manual",
        shape_type=data.shape_type,
        label=data.label,
        x=data.x,
        y=data.y,
        w=data.w,
        h=data.h,
        z_index=data.z_index,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def update_node(
    db: Session, node: models.MindmapNode, data: schemas.MindmapNodeUpdate
) -> models.MindmapNode:
    """部分字段更新。"""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(node, field, value)
    # 顺手刷新 updated_at（onupdate 已自动，但显式更稳）
    node.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(node)
    return node


def delete_node(db: Session, node: models.MindmapNode) -> None:
    db.delete(node)
    db.commit()


def bulk_update_positions(
    db: Session, positions: list[schemas.MindmapNodePosition]
) -> int:
    """拖拽结束批量保存位置。一个 commit 内更新全部，少 IO。"""
    if not positions:
        return 0
    ids = [p.id for p in positions]
    nodes = db.scalars(
        select(models.MindmapNode).where(models.MindmapNode.id.in_(ids))
    ).all()
    pos_map = {p.id: (p.x, p.y) for p in positions}
    updated = 0
    now = datetime.utcnow()
    for n in nodes:
        if n.id in pos_map:
            n.x, n.y = pos_map[n.id]
            n.updated_at = now
            updated += 1
    db.commit()
    return updated


def sync_auto_tree(db: Session, project_id: int) -> dict:
    """根据当前 Project/Goal/Experiment 维护自动树节点。

    流程：
      1) 取当前应有的 source 集合（project, 所有 goal, 所有 experiment）
      2) 取数据库里现有的 auto 节点
      3) 多出来的 → 删；缺的 → 用树布局算初始坐标后建；label 变了 → 更新
      4) 用 source_type/source_id 决定父子关系（project→goal, goal→experiment）

    返回 {"added": int, "removed": int, "updated": int} 供前端 toast。
    """
    diff = {"added": 0, "removed": 0, "updated": 0}

    project = db.get(models.Project, project_id)
    if project is None:
        return diff

    mm = get_or_create_mindmap(db, project_id)
    goals = list(
        db.scalars(
            select(models.Goal)
            .where(models.Goal.project_id == project_id)
            .order_by(models.Goal.order.asc().nulls_last(), models.Goal.created_at.asc())
        ).all()
    )
    experiments_by_goal: dict[int, list[models.Experiment]] = {}
    all_experiments: list[models.Experiment] = []
    for g in goals:
        exps = list(
            db.scalars(
                select(models.Experiment)
                .where(models.Experiment.goal_id == g.id)
                .order_by(models.Experiment.created_at.asc())
            ).all()
        )
        experiments_by_goal[g.id] = exps
        all_experiments.extend(exps)

    # 计算应有的 (source_type, source_id) 集合
    expected: set[tuple[str, int]] = {("project", project_id)}
    for g in goals:
        expected.add(("goal", g.id))
    for e in all_experiments:
        expected.add(("experiment", e.id))

    # 现有 auto 节点
    existing_nodes = db.scalars(
        select(models.MindmapNode)
        .where(
            models.MindmapNode.mindmap_id == mm.id,
            models.MindmapNode.kind == "auto",
        )
    ).all()
    existing_by_src: dict[tuple[str, int], models.MindmapNode] = {
        (n.source_type, n.source_id): n for n in existing_nodes if n.source_type
    }
    existing_src_set = set(existing_by_src.keys())

    # 多出来的 → 删
    # 用 SQL bulk DELETE 直接处理:父节点的 ondelete="CASCADE" 已经把子节点
    # 删了,这里 db.delete() 一个不存在的 row 会刷 SAWarning,不如一次性 DELETE WHERE。
    extra_to_delete = list(existing_src_set - expected)
    if extra_to_delete:
        # 找出实际还存在的节点（父删完会自动级联删子）
        still_existing = db.scalars(
            select(models.MindmapNode).where(
                models.MindmapNode.mindmap_id == mm.id,
                models.MindmapNode.kind == "auto",
                models.MindmapNode.source_type.in_([s[0] for s in extra_to_delete]),
            )
        ).all()
        still_existing_src = {
            (n.source_type, n.source_id) for n in still_existing if n.source_type
        }
        for src in still_existing_src & set(extra_to_delete):
            # 直接 SQL DELETE,跳过 SQLAlchemy 的 rowcount 检查
            db.execute(
                text("DELETE FROM mindmap_nodes WHERE mindmap_id = :mid "
                     "AND kind = 'auto' AND source_type = :st AND source_id = :sid"),
                {"mid": mm.id, "st": src[0], "sid": src[1]},
            )
            diff["removed"] += 1

    # 缺的 → 建（用布局算坐标；按 source_type 顺序处理保证父先于子建）
    missing = expected - existing_src_set
    if missing:
        layout = _compute_tree_layout(
            project_id, project.name, goals, experiments_by_goal
        )
        # 父子映射（按 source_type 决定）：project → goal.id; goal → experiment.goal_id
        parent_for: dict[tuple[str, int], tuple[str, int] | None] = {}
        for g in goals:
            parent_for[("goal", g.id)] = ("project", project_id)
        for e in all_experiments:
            parent_for[("experiment", e.id)] = ("goal", e.goal_id) if e.goal_id else None

        # 本轮新建节点缓存（解决父子都在本轮建的情况）
        created_in_pass: dict[tuple[str, int], models.MindmapNode] = {}

        # 按 source_type 排序：project → goal → experiment
        type_order = {"project": 0, "goal": 1, "experiment": 2}
        for src in sorted(missing, key=lambda s: type_order.get(s[0], 99)):
            x, y, w, h = layout.get(src, (80.0, 80.0, _AUTO_NODE_W, _AUTO_NODE_H))
            # Project → 圆角（容器感），Goal → 椭圆（"目标=靶心"），Experiment → 菱形（实验感）
            shape_map = {
                "project": "rounded",
                "goal": "ellipse",
                "experiment": "diamond",
            }
            label_map: dict[tuple[str, int], str] = {("project", project_id): project.name}
            for g in goals:
                label_map[("goal", g.id)] = g.name
            for e in all_experiments:
                label_map[("experiment", e.id)] = e.name
            new_node = models.MindmapNode(
                mindmap_id=mm.id,
                kind="auto",
                source_type=src[0],
                source_id=src[1],
                shape_type=shape_map.get(src[0], "rect"),
                label=label_map.get(src, ""),
                x=x,
                y=y,
                w=w,
                h=h,
            )
            db.add(new_node)
            db.flush()  # 拿到 id 以便后续设 parent
            created_in_pass[src] = new_node

            parent_src = parent_for.get(src)
            if parent_src:
                parent_node = (
                    existing_by_src.get(parent_src)
                    or created_in_pass.get(parent_src)
                )
                if parent_node is not None:
                    new_node.parent_id = parent_node.id
            diff["added"] += 1

    # label / shape_type 变了 → 更新（不动 x/y/w/h）
    auto_shape_map = {
        "project": "rounded",
        "goal": "ellipse",
        "experiment": "diamond",
    }
    for src in expected & existing_src_set:
        n = existing_by_src[src]
        new_label = None
        if src[0] == "project":
            new_label = project.name
        elif src[0] == "goal":
            g = next((g for g in goals if g.id == src[1]), None)
            if g:
                new_label = g.name
        elif src[0] == "experiment":
            e = next((e for e in all_experiments if e.id == src[1]), None)
            if e:
                new_label = e.name
        changed = False
        if new_label is not None and n.label != new_label:
            n.label = new_label
            changed = True
        # 形状迁移：旧库里的 auto 节点可能是 rect，统一收敛到新映射
        target_shape = auto_shape_map.get(src[0])
        if target_shape and n.shape_type != target_shape:
            n.shape_type = target_shape
            changed = True
        if changed:
            n.updated_at = datetime.utcnow()
            diff["updated"] += 1

    db.commit()
    return diff


# ---------------------------------------------------------------------------
# 手动连线 (mindmap_edges) CRUD
# ---------------------------------------------------------------------------


def list_edges(db: Session, mindmap_id: int) -> list[models.MindmapEdge]:
    """返回该导图的全部手动连线（按 id 升序）。"""
    return list(
        db.scalars(
            select(models.MindmapEdge)
            .where(models.MindmapEdge.mindmap_id == mindmap_id)
            .order_by(models.MindmapEdge.id.asc())
        ).all()
    )


def get_edge(db: Session, edge_id: int) -> models.MindmapEdge | None:
    return db.get(models.MindmapEdge, edge_id)


def create_edge(
    db: Session, mindmap_id: int, data: schemas.MindmapEdgeCreate
) -> models.MindmapEdge:
    """创建一条手动连线。

    校验：
      1) source ≠ target（自环禁止）
      2) source 和 target 都必须属于该 mindmap
    """
    if data.source_id == data.target_id:
        raise ValueError("source_id and target_id must differ (no self-loop)")
    # 校验两端节点都属于同一 mindmap（避免越界引用）
    nodes = db.scalars(
        select(models.MindmapNode).where(
            models.MindmapNode.id.in_([data.source_id, data.target_id])
        )
    ).all()
    found_ids = {n.id for n in nodes}
    if data.source_id not in found_ids:
        raise ValueError(f"source node {data.source_id} not found")
    if data.target_id not in found_ids:
        raise ValueError(f"target node {data.target_id} not found")
    if any(n.mindmap_id != mindmap_id for n in nodes):
        raise ValueError("source/target nodes must belong to the same mindmap")

    edge = models.MindmapEdge(
        mindmap_id=mindmap_id,
        source_id=data.source_id,
        target_id=data.target_id,
        arrow=data.arrow,
    )
    db.add(edge)
    db.commit()
    db.refresh(edge)
    return edge


def update_edge(
    db: Session, edge: models.MindmapEdge, data: schemas.MindmapEdgeUpdate
) -> models.MindmapEdge:
    """部分字段更新（目前仅支持 arrow 切换）。"""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(edge, field, value)
    db.commit()
    db.refresh(edge)
    return edge


def delete_edge(db: Session, edge: models.MindmapEdge) -> None:
    db.delete(edge)
    db.commit()

