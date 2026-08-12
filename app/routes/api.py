"""JSON API 路由。

设计目标：让训练脚本可以用一个 HTTP POST 把结果推过来。
例如：

    import requests
    r = requests.post("http://localhost:8000/api/projects", json={"name": "声学成像"})
    pid = r.json()["id"]

    r = requests.post(
        f"http://localhost:8000/api/projects/{pid}/experiments",
        json={"name": "IASA-2D-鸽子-v1"},
    )
    eid = r.json()["id"]

    requests.post(
        f"http://localhost:8000/api/experiments/{eid}/metrics",
        json={"key": "loss", "value": loss, "note": "epoch 1"},
    )

所有路由都返回标准 JSON。
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import crud, schemas
from app import models
from app.config import get_artifact_dir, settings
from app.database import get_db
from app.services import settings_service

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=list[schemas.ProjectRead])
def api_list_projects(db: Session = Depends(get_db)):
    projects = crud.list_projects(db)
    return [
        schemas.ProjectRead(
            id=p.id,
            name=p.name,
            description=p.description,
            tags=p.tags,
            status=p.status,
            created_at=p.created_at,
            updated_at=p.updated_at,
            experiment_count=crud.count_experiments(db, p.id),
            artifact_count=crud.count_artifacts_by_project(db, p.id),
        )
        for p in projects
    ]


@router.post("/projects", response_model=schemas.ProjectRead, status_code=201)
def api_create_project(
    data: schemas.ProjectCreate, db: Session = Depends(get_db)
):
    if crud.get_project_by_name(db, data.name):
        raise HTTPException(409, f"Project '{data.name}' already exists")
    project = crud.create_project(db, data)
    return schemas.ProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        tags=project.tags,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        experiment_count=0,
        artifact_count=0,
    )


@router.get("/projects/{project_id}", response_model=schemas.ProjectRead)
def api_get_project(project_id: int, db: Session = Depends(get_db)):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return schemas.ProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        tags=project.tags,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        experiment_count=crud.count_experiments(db, project_id),
        artifact_count=crud.count_artifacts_by_project(db, project_id),
    )


@router.patch("/projects/{project_id}", response_model=schemas.ProjectRead)
def api_update_project(
    project_id: int,
    data: schemas.ProjectUpdate,
    db: Session = Depends(get_db),
):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    crud.update_project(db, project, data)
    return schemas.ProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        tags=project.tags,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        experiment_count=crud.count_experiments(db, project_id),
        artifact_count=crud.count_artifacts_by_project(db, project_id),
    )


@router.delete("/projects/{project_id}", status_code=204)
def api_delete_project(project_id: int, db: Session = Depends(get_db)):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    crud.delete_project(db, project)


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------


def _goal_to_read(goal, experiment_count: int = 0) -> schemas.GoalRead:
    return schemas.GoalRead(
        id=goal.id,
        project_id=goal.project_id,
        name=goal.name,
        description=goal.description,
        status=goal.status,
        priority=goal.priority,
        order=goal.order,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        experiment_count=experiment_count,
    )


@router.get(
    "/projects/{project_id}/goals",
    response_model=list[schemas.GoalRead],
)
def api_list_goals(project_id: int, db: Session = Depends(get_db)):
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    goals = crud.list_goals(db, project_id)
    return [
        _goal_to_read(g, experiment_count=crud.count_experiments_by_goal(db, g.id))
        for g in goals
    ]


@router.post(
    "/projects/{project_id}/goals",
    response_model=schemas.GoalRead,
    status_code=201,
)
def api_create_goal(
    project_id: int,
    data: schemas.GoalCreate,
    db: Session = Depends(get_db),
):
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    goal = crud.create_goal(db, project_id, data)
    return _goal_to_read(goal)


@router.get("/goals/{goal_id}", response_model=schemas.GoalRead)
def api_get_goal(goal_id: int, db: Session = Depends(get_db)):
    goal = crud.get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    return _goal_to_read(goal, experiment_count=crud.count_experiments_by_goal(db, goal.id))


@router.patch("/goals/{goal_id}", response_model=schemas.GoalRead)
def api_update_goal(
    goal_id: int,
    data: schemas.GoalUpdate,
    db: Session = Depends(get_db),
):
    goal = crud.get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    crud.update_goal(db, goal, data)
    return _goal_to_read(goal, experiment_count=crud.count_experiments_by_goal(db, goal.id))


@router.delete("/goals/{goal_id}", status_code=204)
def api_delete_goal(goal_id: int, db: Session = Depends(get_db)):
    goal = crud.get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    crud.delete_goal(db, goal)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


def _exp_to_read(exp, metric_count: int = 0, note_count: int = 0, artifact_count: int = 0) -> schemas.ExperimentRead:
    return schemas.ExperimentRead(
        id=exp.id,
        project_id=exp.project_id,
        goal_id=exp.goal_id or 0,
        name=exp.name,
        description=exp.description,
        hypothesis=exp.hypothesis,
        design_notes=exp.design_notes,
        result_summary=exp.result_summary,
        config_md=exp.config_md,
        status=exp.status,
        priority=exp.priority,
        due_date=exp.due_date.isoformat() if exp.due_date else None,
        git_commit=exp.git_commit,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
        metric_count=metric_count,
        note_count=note_count,
        artifact_count=artifact_count,
    )


@router.get(
    "/projects/{project_id}/experiments",
    response_model=list[schemas.ExperimentRead],
)
def api_list_experiments(project_id: int, db: Session = Depends(get_db)):
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    exps = crud.list_experiments(db, project_id)
    return [
        _exp_to_read(
            e,
            metric_count=crud.count_metrics(db, e.id),
            note_count=crud.count_notes(db, e.id),
            artifact_count=crud.count_artifacts_by_experiment(db, e.id),
        )
        for e in exps
    ]


@router.post(
    "/projects/{project_id}/experiments",
    response_model=schemas.ExperimentRead,
    status_code=201,
)
def api_create_experiment(
    project_id: int,
    data: schemas.ExperimentCreate,
    db: Session = Depends(get_db),
):
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    # 校验大目标
    goal = crud.get_goal(db, data.goal_id)
    if goal is None or goal.project_id != project_id:
        raise HTTPException(400, f"Goal {data.goal_id} 不属于该项目")
    exp = crud.create_experiment(db, project_id, data)
    return _exp_to_read(exp)


@router.get("/experiments/{experiment_id}", response_model=schemas.ExperimentRead)
def api_get_experiment(experiment_id: int, db: Session = Depends(get_db)):
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    return _exp_to_read(
        exp,
        metric_count=crud.count_metrics(db, exp.id),
        note_count=crud.count_notes(db, exp.id),
        artifact_count=crud.count_artifacts_by_experiment(db, exp.id),
    )


@router.patch("/experiments/{experiment_id}", response_model=schemas.ExperimentRead)
def api_update_experiment(
    experiment_id: int,
    data: schemas.ExperimentUpdate,
    db: Session = Depends(get_db),
):
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    if data.goal_id is not None:
        goal = crud.get_goal(db, data.goal_id)
        if goal is None or goal.project_id != exp.project_id:
            raise HTTPException(400, f"Goal {data.goal_id} 不属于该项目")
    crud.update_experiment(db, exp, data)
    return _exp_to_read(exp)


@router.delete("/experiments/{experiment_id}", status_code=204)
def api_delete_experiment(experiment_id: int, db: Session = Depends(get_db)):
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    crud.delete_experiment(db, exp)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@router.get(
    "/experiments/{experiment_id}/metrics",
    response_model=list[schemas.MetricRead],
)
def api_list_metrics(experiment_id: int, db: Session = Depends(get_db)):
    if crud.get_experiment(db, experiment_id) is None:
        raise HTTPException(404, "Experiment not found")
    return crud.list_metrics(db, experiment_id)


@router.post(
    "/experiments/{experiment_id}/metrics",
    response_model=schemas.MetricRead,
    status_code=201,
)
def api_create_metric(
    experiment_id: int,
    data: schemas.MetricCreate,
    db: Session = Depends(get_db),
):
    if crud.get_experiment(db, experiment_id) is None:
        raise HTTPException(404, "Experiment not found")
    return crud.create_metric(db, experiment_id, data)


@router.post(
    "/experiments/{experiment_id}/metrics/batch",
    response_model=list[schemas.MetricRead],
    status_code=201,
)
def api_create_metrics_batch(
    experiment_id: int,
    metrics: list[schemas.MetricCreate],
    db: Session = Depends(get_db),
):
    """一次性提交多个指标（减少 HTTP 握手）。"""
    if crud.get_experiment(db, experiment_id) is None:
        raise HTTPException(404, "Experiment not found")
    created = []
    for m in metrics:
        created.append(crud.create_metric(db, experiment_id, m))
    return created


@router.delete("/metrics/{metric_id}", status_code=204)
def api_delete_metric(metric_id: int, db: Session = Depends(get_db)):
    from app.models import Metric

    metric = db.get(Metric, metric_id)
    if metric is None:
        raise HTTPException(404, "Metric not found")
    crud.delete_metric(db, metric)


@router.get(
    "/experiments/{experiment_id}/notes",
    response_model=list[schemas.NoteRead],
)
def api_list_notes(experiment_id: int, db: Session = Depends(get_db)):
    if crud.get_experiment(db, experiment_id) is None:
        raise HTTPException(404, "Experiment not found")
    return crud.list_notes(db, experiment_id)


@router.post(
    "/experiments/{experiment_id}/notes",
    response_model=schemas.NoteRead,
    status_code=201,
)
def api_create_note(
    experiment_id: int,
    data: schemas.NoteCreate,
    db: Session = Depends(get_db),
):
    if crud.get_experiment(db, experiment_id) is None:
        raise HTTPException(404, "Experiment not found")
    return crud.create_note(db, experiment_id, data)


@router.delete("/notes/{note_id}", status_code=204)
def api_delete_note(note_id: int, db: Session = Depends(get_db)):
    from app.models import Note

    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    crud.delete_note(db, note)


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@router.get("/decisions", response_model=list[schemas.DecisionRead])
def api_list_open_decisions(db: Session = Depends(get_db)):
    return crud.list_open_decisions(db)


@router.post(
    "/projects/{project_id}/decisions",
    response_model=schemas.DecisionRead,
    status_code=201,
)
def api_create_decision(
    project_id: int,
    data: schemas.DecisionCreate,
    db: Session = Depends(get_db),
):
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    return crud.create_decision(db, project_id, data)


@router.post("/decisions/{decision_id}/resolve", response_model=schemas.DecisionRead)
def api_resolve_decision(decision_id: int, db: Session = Depends(get_db)):
    dec = crud.get_decision(db, decision_id)
    if dec is None:
        raise HTTPException(404, "Decision not found")
    return crud.resolve_decision(db, dec)


@router.delete("/decisions/{decision_id}", status_code=204)
def api_delete_decision(decision_id: int, db: Session = Depends(get_db)):
    dec = crud.get_decision(db, decision_id)
    if dec is None:
        raise HTTPException(404, "Decision not found")
    crud.delete_decision(db, dec)


# ---------------------------------------------------------------------------
# Artifacts (文件与成果)
# ---------------------------------------------------------------------------


def _artifact_max_bytes() -> int:
    return settings.max_upload_size_mb * 1024 * 1024


@router.post(
    "/projects/{project_id}/artifacts",
    response_model=schemas.ArtifactRead,
    status_code=201,
)
async def api_upload_project_artifact(
    project_id: int,
    file: UploadFile = File(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    return await _do_artifact_upload(db, file, description, "project", project_id)


@router.post(
    "/goals/{goal_id}/artifacts",
    response_model=schemas.ArtifactRead,
    status_code=201,
)
async def api_upload_goal_artifact(
    goal_id: int,
    file: UploadFile = File(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    if crud.get_goal(db, goal_id) is None:
        raise HTTPException(404, "Goal not found")
    return await _do_artifact_upload(db, file, description, "goal", goal_id)


@router.post(
    "/experiments/{experiment_id}/artifacts",
    response_model=schemas.ArtifactRead,
    status_code=201,
)
async def api_upload_experiment_artifact(
    experiment_id: int,
    file: UploadFile = File(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    if crud.get_experiment(db, experiment_id) is None:
        raise HTTPException(404, "Experiment not found")
    return await _do_artifact_upload(db, file, description, "experiment", experiment_id)


async def _do_artifact_upload(
    db: Session, file: UploadFile, description: str, owner_kind: str, owner_id: int
):
    """共用的上传实现(三种归属走同一份代码)。"""
    # 大小限制
    content = await file.read()
    if len(content) > _artifact_max_bytes():
        raise HTTPException(
            413,
            f"文件超过 {settings.max_upload_size_mb} MB 上限",
        )
    # 把字节塞回去让 crud 处理
    import io as _io
    file.file = _io.BytesIO(content)
    artifact = crud.create_artifact(
        db,
        file,
        owner_kind=owner_kind,
        owner_id=owner_id,
        description=description,
    )
    return artifact


@router.get("/artifacts", response_model=list[schemas.ArtifactRead])
def api_list_artifacts(
    project_id: int | None = None,
    goal_id: int | None = None,
    experiment_id: int | None = None,
    kind: str | None = None,
    db: Session = Depends(get_db),
):
    return crud.list_artifacts(
        db,
        project_id=project_id,
        goal_id=goal_id,
        experiment_id=experiment_id,
        kind=kind,
    )


@router.get("/artifacts/{artifact_id}", response_model=schemas.ArtifactRead)
def api_get_artifact(artifact_id: int, db: Session = Depends(get_db)):
    art = crud.get_artifact(db, artifact_id)
    if art is None:
        raise HTTPException(404, "Artifact not found")
    return art


@router.patch("/artifacts/{artifact_id}", response_model=schemas.ArtifactRead)
def api_update_artifact(
    artifact_id: int,
    data: schemas.ArtifactUpdate,
    db: Session = Depends(get_db),
):
    art = crud.get_artifact(db, artifact_id)
    if art is None:
        raise HTTPException(404, "Artifact not found")
    crud.update_artifact(db, art, data)
    return art


@router.get("/artifacts/{artifact_id}/download")
def api_download_artifact(artifact_id: int, db: Session = Depends(get_db)):
    art = crud.get_artifact(db, artifact_id)
    if art is None:
        raise HTTPException(404, "Artifact not found")
    path = get_artifact_dir() / art.stored_path
    if not path.is_file():
        raise HTTPException(410, "文件已丢失（磁盘上找不到）")
    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(path),
        filename=art.original_name,
        media_type=art.mime_type or "application/octet-stream",
    )


@router.get("/artifacts/{artifact_id}/inline")
def api_inline_artifact(artifact_id: int, db: Session = Depends(get_db)):
    """inline 返回 (用于 <img> 缩略图 / 灯箱预览 / 文本文件内嵌展示)。
    与 /download 区别: 不带 Content-Disposition: attachment, 让浏览器直接渲染.
    """
    art = crud.get_artifact(db, artifact_id)
    if art is None:
        raise HTTPException(404, "Artifact not found")
    path = get_artifact_dir() / art.stored_path
    if not path.is_file():
        raise HTTPException(410, "文件已丢失（磁盘上找不到）")
    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(path),
        media_type=art.mime_type or "application/octet-stream",
        # 不传 filename 就不写 Content-Disposition, 默认 inline
    )


@router.delete("/artifacts/{artifact_id}", status_code=204)
def api_delete_artifact(artifact_id: int, db: Session = Depends(get_db)):
    art = crud.get_artifact(db, artifact_id)
    if art is None:
        raise HTTPException(404, "Artifact not found")
    crud.delete_artifact(db, art)


@router.post("/artifacts/{artifact_id}/move", response_model=schemas.ArtifactRead)
def api_move_artifact(
    artifact_id: int,
    folder: str = Form(""),  # C4: 目标子目录路径, 空串 = 根
    db: Session = Depends(get_db),
):
    """C4: 把 artifact 挪到指定子目录下. 只改 original_name, 不动磁盘文件."""
    art = crud.get_artifact(db, artifact_id)
    if art is None:
        raise HTTPException(404, "Artifact not found")
    crud.move_artifact(db, art, folder)
    return art


@router.post("/folders/rename")
def api_rename_folder(
    project_id: int = Form(...),
    old: str = Form(...),
    new: str = Form(...),
    db: Session = Depends(get_db),
):
    """C4: 把 project 下某子目录整体改名 (批量更新所有以 old/ 开头的 artifact)."""
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    n = crud.rename_folder(db, project_id=project_id, old_folder=old, new_folder=new)
    return {"renamed": n, "old": old, "new": new}


# ---------------------------------------------------------------------------
# Weekly Reviews (周复盘)
# ---------------------------------------------------------------------------


@router.get("/weekly-reviews", response_model=list[schemas.WeeklyReviewRead])
def api_list_weekly_reviews(
    limit: int = 50, db: Session = Depends(get_db)
):
    return crud.list_weekly_reviews(db, limit=limit)


@router.post(
    "/weekly-reviews",
    response_model=schemas.WeeklyReviewRead,
    status_code=201,
)
def api_create_weekly_review(
    data: schemas.WeeklyReviewCreate,
    db: Session = Depends(get_db),
):
    return crud.create_weekly_review(db, data)


@router.get(
    "/weekly-reviews/{review_id}",
    response_model=schemas.WeeklyReviewRead,
)
def api_get_weekly_review(review_id: int, db: Session = Depends(get_db)):
    r = crud.get_weekly_review(db, review_id)
    if r is None:
        raise HTTPException(404, "WeeklyReview not found")
    return r


@router.patch(
    "/weekly-reviews/{review_id}",
    response_model=schemas.WeeklyReviewRead,
)
def api_update_weekly_review(
    review_id: int,
    data: schemas.WeeklyReviewUpdate,
    db: Session = Depends(get_db),
):
    r = crud.get_weekly_review(db, review_id)
    if r is None:
        raise HTTPException(404, "WeeklyReview not found")
    crud.update_weekly_review(db, r, data)
    return r


@router.delete("/weekly-reviews/{review_id}", status_code=204)
def api_delete_weekly_review(review_id: int, db: Session = Depends(get_db)):
    r = crud.get_weekly_review(db, review_id)
    if r is None:
        raise HTTPException(404, "WeeklyReview not found")
    crud.delete_weekly_review(db, r)


# ---------------------------------------------------------------------------
# Experiment Export (一键导出 ZIP)
# ---------------------------------------------------------------------------


def _experiment_markdown(exp, project, goal, metrics, notes, artifacts) -> str:
    """把实验拼成一份 Markdown,放进 zip。"""
    lines: list[str] = []
    lines.append(f"# 实验：{exp.name}")
    lines.append("")
    lines.append(f"- 项目：**{project.name}**")
    if goal is not None:
        lines.append(f"- 大目标：**{goal.name}**")
    lines.append(f"- 创建：{exp.created_at.isoformat(timespec='seconds')} UTC")
    lines.append(f"- 更新：{exp.updated_at.isoformat(timespec='seconds')} UTC")
    if exp.due_date:
        lines.append(f"- 截止：{exp.due_date.isoformat()}")
    if exp.git_commit:
        lines.append(f"- Commit：`{exp.git_commit}`")
    lines.append(f"- 状态：{exp.status}")
    lines.append("")

    if exp.description:
        lines.append("## 简介")
        lines.append(exp.description)
        lines.append("")

    if exp.hypothesis:
        lines.append("## 假设")
        lines.append(exp.hypothesis)
        lines.append("")

    if exp.config_md:
        lines.append("## 配置 / 超参数")
        lines.append(exp.config_md)
        lines.append("")

    if exp.design_notes:
        lines.append("## 设计备注")
        lines.append(exp.design_notes)
        lines.append("")

    if exp.result_summary:
        lines.append("## 结果小结")
        lines.append(exp.result_summary)
        lines.append("")

    if metrics:
        lines.append("## 指标")
        lines.append("")
        lines.append("| key | value | step | note | 时间 |")
        lines.append("|---|---|---|---|---|")
        for m in metrics:
            lines.append(
                f"| {m.key} | {m.value} | {m.step if m.step is not None else ''} "
                f"| {m.note} | {m.timestamp.isoformat(timespec='seconds')} |"
            )
        lines.append("")

    if notes:
        lines.append("## 笔记")
        for n in notes:
            lines.append("")
            lines.append(f"### {n.created_at.isoformat(timespec='seconds')}")
            lines.append(n.content)

    if artifacts:
        lines.append("")
        lines.append("## 文件与成果")
        for a in artifacts:
            lines.append(
                f"- {a.kind}: `{a.original_name}` ({a.size_bytes} bytes)"
            )

    return "\n".join(lines)


def _metrics_csv(metrics) -> bytes:
    """UTF-8 BOM + CSV,Excel 直接打开不乱码。"""
    buf = io.StringIO()
    buf.write("﻿")  # BOM
    writer = csv.writer(buf)
    writer.writerow(["key", "value", "step", "note", "timestamp"])
    for m in metrics:
        writer.writerow([
            m.key,
            m.value,
            m.step if m.step is not None else "",
            m.note,
            m.timestamp.isoformat(timespec="seconds"),
        ])
    return buf.getvalue().encode("utf-8")


@router.get("/experiments/{experiment_id}/export")
def api_export_experiment(experiment_id: int, db: Session = Depends(get_db)):
    """把单个实验的全部内容打包成 ZIP 下载。

    目录结构:
        experiment.md      整篇可读的实验 Markdown
        metrics.csv        所有指标
        notes/
            0001.md ...    每条笔记一篇(可选,如果只有一条就走 notes.md)
        artifacts/
            xxx.png        图片和 .pt 都在这
    """
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    project = crud.get_project(db, exp.project_id)
    goal = crud.get_goal(db, exp.goal_id) if exp.goal_id else None
    metrics = crud.list_metrics(db, exp.id)
    notes = crud.list_notes(db, exp.id)
    artifacts = crud.list_artifacts(db, experiment_id=exp.id)

    md_text = _experiment_markdown(exp, project, goal, metrics, notes, artifacts)
    csv_bytes = _metrics_csv(metrics)

    artifact_root = get_artifact_dir()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("experiment.md", md_text)
        zf.writestr("metrics.csv", csv_bytes)
        if notes:
            # 一条笔记就 notes.md,多条就 notes/0001.md ... 风格
            if len(notes) == 1:
                zf.writestr(f"notes.md", notes[0].content)
            else:
                for i, n in enumerate(notes, start=1):
                    fname = f"notes/{i:04d}.md"
                    zf.writestr(fname, n.content)
        for a in artifacts:
            src = artifact_root / a.stored_path
            if src.is_file():
                zf.write(src, arcname=f"artifacts/{a.original_name}")
            else:
                # 文件已丢,塞个占位说明
                zf.writestr(
                    f"artifacts/_missing_{a.id}.txt",
                    f"文件 {a.original_name} 在磁盘上已找不到。",
                )

    zip_buf.seek(0)
    # 文件名走 RFC 5987 编码:英文字段填 ASCII 兜底,中文走 filename* (UTF-8)
    # 否则浏览器收到 Content-Disposition: attachment; filename="中文.zip" 会乱码
    # 或被部分 HTTP 库直接拒绝(latin-1 编不了中文)。
    safe_name = exp.name.replace("/", "_").replace("\\", "_")
    ascii_fallback = "".join(c if ord(c) < 128 else "_" for c in safe_name)
    fname = f"{safe_name}-export-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    fallback = f"{ascii_fallback or 'experiment'}-export-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    import urllib.parse
    quoted = urllib.parse.quote(fname)
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{fallback}"; '
                f"filename*=UTF-8''{quoted}"
            )
        },
    )

# ---------------------------------------------------------------------------
# 元信息：数据库健康 / 备份（设置模块）
# ---------------------------------------------------------------------------


@router.get("/db-health", tags=["meta"])
def api_db_health():
    """状态栏轮询：PRAGMA integrity_check 真实结果 + 数据库文件大小。"""
    return settings_service.get_db_health()


@router.get("/settings/backup", tags=["meta"])
def api_backup_db(background: BackgroundTasks):
    """下载完整备份 zip：数据库 + 上传文件 + 配置（跨电脑迁移用）。"""
    try:
        tmp = settings_service.create_full_backup()
    except settings_service.SettingsError as exc:
        raise HTTPException(500, str(exc))
    fname = f"workbench-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"
    background.add_task(tmp.unlink, missing_ok=True)
    return FileResponse(
        str(tmp),
        filename=fname,
        media_type="application/zip",
    )


# ---------------------------------------------------------------------------
# 项目思维导图
# ---------------------------------------------------------------------------


def _node_to_read(n) -> schemas.MindmapNodeRead:
    return schemas.MindmapNodeRead.model_validate(n)


def _edge_to_read(e) -> schemas.MindmapEdgeRead:
    return schemas.MindmapEdgeRead.model_validate(e)


@router.get("/projects/{project_id}/mindmap", response_model=schemas.MindmapRead)
def api_get_mindmap(project_id: int, db: Session = Depends(get_db)):
    """取该项目导图全部节点 + 手动连线。GET 即触发增量 sync。"""
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    mm = crud.get_or_create_mindmap(db, project_id)
    diff = crud.sync_auto_tree(db, project_id)
    nodes = crud.list_nodes(db, mm.id)
    edges = crud.list_edges(db, mm.id)
    return schemas.MindmapRead(
        id=mm.id,
        project_id=mm.project_id,
        nodes=[_node_to_read(n) for n in nodes],
        edges=[_edge_to_read(e) for e in edges],
        sync_diff=diff,
    )


@router.post("/projects/{project_id}/mindmap/sync")
def api_sync_mindmap(project_id: int, db: Session = Depends(get_db)):
    """手动触发 sync。返回 diff {added, removed, updated}。"""
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    diff = crud.sync_auto_tree(db, project_id)
    return diff


def _validate_container(db: Session, node: models.MindmapNode, container_id: int | None) -> None:
    """A1 容器关系校验: 容器必须存在、属于同一导图、不能是自己、不能是容器 (防止嵌套循环)。"""
    if container_id is None:
        return
    if container_id == node.id:
        raise HTTPException(400, "节点不能成为自己的容器")
    container = db.get(models.MindmapNode, container_id)
    if container is None:
        raise HTTPException(404, "容器节点不存在")
    if container.mindmap_id != node.mindmap_id:
        raise HTTPException(400, "容器必须属于同一导图")
    if container.shape_type != "container":
        raise HTTPException(400, "目标不是容器节点 (shape_type != 'container')")
    if container.container_id is not None:
        raise HTTPException(400, "容器不能嵌套其他容器 (扁平结构)")


@router.post(
    "/projects/{project_id}/mindmap/nodes",
    response_model=schemas.MindmapNodeRead,
    status_code=201,
)
def api_create_mindmap_node(
    project_id: int,
    data: schemas.MindmapNodeCreate,
    db: Session = Depends(get_db),
):
    """新建一个 manual 节点（工具栏按钮 / 复制粘贴走这里）。"""
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    mm = crud.get_or_create_mindmap(db, project_id)
    if data.shape_type not in schemas.SHAPE_TYPES:
        raise HTTPException(400, f"不支持的 shape_type: {data.shape_type}")
    # 容器关系校验: 此时 node.id 还没生成, 用哨兵 -1 防自环即可
    tmp = models.MindmapNode(id=-1, mindmap_id=mm.id, kind="manual", shape_type=data.shape_type)
    _validate_container(db, tmp, data.container_id)
    node = crud.create_node(db, mm.id, data)
    return _node_to_read(node)


@router.patch(
    "/mindmap/nodes/{node_id}",
    response_model=schemas.MindmapNodeRead,
)
def api_update_mindmap_node(
    node_id: int,
    data: schemas.MindmapNodeUpdate,
    db: Session = Depends(get_db),
):
    """更新单个节点（编辑文字/调大小/改 shape_type/置顶置底都用）。"""
    node = crud.get_node(db, node_id)
    if node is None:
        raise HTTPException(404, "MindmapNode not found")
    if data.shape_type is not None and data.shape_type not in schemas.SHAPE_TYPES:
        raise HTTPException(400, f"不支持的 shape_type: {data.shape_type}")
    if "container_id" in data.model_dump(exclude_unset=True):
        _validate_container(db, node, data.container_id)
    try:
        crud.update_node(db, node, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _node_to_read(node)


@router.delete("/mindmap/nodes/{node_id}", status_code=204)
def api_delete_mindmap_node(node_id: int, db: Session = Depends(get_db)):
    node = crud.get_node(db, node_id)
    if node is None:
        raise HTTPException(404, "MindmapNode not found")
    crud.delete_node(db, node)


@router.post("/mindmap/nodes/bulk-position")
def api_bulk_update_positions(
    data: schemas.MindmapBulkPositionUpdate,
    db: Session = Depends(get_db),
):
    """拖拽结束批量保存位置。一次请求多节点，少 IO。"""
    updated = crud.bulk_update_positions(db, data.positions)
    return {"updated": updated}


# ---------------------------------------------------------------------------
# 手动连线 (mindmap_edges) endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/mindmap/edges",
    response_model=schemas.MindmapEdgeRead,
    status_code=201,
)
def api_create_mindmap_edge(
    project_id: int,
    data: schemas.MindmapEdgeCreate,
    db: Session = Depends(get_db),
):
    """创建一条手动连线（source→target）。"""
    if crud.get_project(db, project_id) is None:
        raise HTTPException(404, "Project not found")
    mm = crud.get_or_create_mindmap(db, project_id)
    try:
        edge = crud.create_edge(db, mm.id, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _edge_to_read(edge)


@router.patch(
    "/mindmap/edges/{edge_id}",
    response_model=schemas.MindmapEdgeRead,
)
def api_update_mindmap_edge(
    edge_id: int,
    data: schemas.MindmapEdgeUpdate,
    db: Session = Depends(get_db),
):
    """更新连线（目前仅支持切箭头）。"""
    edge = crud.get_edge(db, edge_id)
    if edge is None:
        raise HTTPException(404, "MindmapEdge not found")
    crud.update_edge(db, edge, data)
    return _edge_to_read(edge)


@router.delete("/mindmap/edges/{edge_id}", status_code=204)
def api_delete_mindmap_edge(edge_id: int, db: Session = Depends(get_db)):
    edge = crud.get_edge(db, edge_id)
    if edge is None:
        raise HTTPException(404, "MindmapEdge not found")
    crud.delete_edge(db, edge)
