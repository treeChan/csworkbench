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

    for step, loss in enumerate(losses):
        requests.post(
            f"http://localhost:8000/api/experiments/{eid}/metrics",
            json={"key": "loss", "value": loss, "step": step},
        )

所有路由都返回标准 JSON。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, schemas
from app.database import get_db

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=list[schemas.ProjectRead])
def api_list_projects(db: Session = Depends(get_db)):
    projects = crud.list_projects(db)
    # 加上 experiment_count
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


def _exp_to_read(exp, metric_count: int = 0, note_count: int = 0) -> schemas.ExperimentRead:
    return schemas.ExperimentRead(
        id=exp.id,
        project_id=exp.project_id,
        goal_id=exp.goal_id or 0,
        name=exp.name,
        description=exp.description,
        hypothesis=exp.hypothesis,
        design_notes=exp.design_notes,
        result_summary=exp.result_summary,
        config=exp.config,
        status=exp.status,
        priority=exp.priority,
        due_date=exp.due_date.isoformat() if exp.due_date else None,
        git_commit=exp.git_commit,
        created_at=exp.created_at,
        updated_at=exp.updated_at,
        metric_count=metric_count,
        note_count=note_count,
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