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
from sqlalchemy import func, select
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

