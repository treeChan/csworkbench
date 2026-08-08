"""HTML 页面路由（服务器渲染）。

URL 设计：
    GET  /                           → 仪表盘（项目列表）
    GET  /projects/new               → 创建项目表单
    POST /projects                   → 提交创建
    GET  /projects/{id}              → 项目详情（含实验列表）
    GET  /projects/{id}/edit         → 编辑项目表单
    POST /projects/{id}              → 提交编辑
    POST /projects/{id}/delete       → 删除项目
    GET  /projects/{id}/experiments/new       → 创建实验表单
    POST /projects/{id}/experiments          → 提交创建实验
    GET  /experiments/{id}                    → 实验详情
    GET  /experiments/{id}/edit               → 编辑实验
    POST /experiments/{id}                    → 提交编辑
    POST /experiments/{id}/delete             → 删除实验
    POST /experiments/{id}/metrics            → 新增一个指标（重定向回实验页）
    POST /experiments/{id}/notes              → 新增一条笔记
    POST /metrics/{id}/delete                 → 删除一个指标
    POST /notes/{id}/delete                   → 删除一条笔记
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.config import APP_LICENSE, APP_VERSION, save_setting, settings
from app.database import get_db
from app.services import settings_service

# ---------------------------------------------------------------------------
# 模板 & 自定义过滤器
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# 注入到所有模板的全局变量
# 注意：不要把 datetime.utcnow 这种未绑定方法直接放进去，
# Jinja 会拿到方法对象本身而不是它的结果。用一个 lambda 包一下。
templates.env.globals["now"] = lambda: datetime.utcnow()


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


def _format_config(value: dict | None) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2)


templates.env.filters["fmt_dt"] = _format_dt
templates.env.filters["fmt_config"] = _format_config

# Markdown 渲染（python-markdown 已装的话）
try:
    import markdown as _md

    def _md_render(text: str) -> str:
        return _md.markdown(
            text,
            extensions=["fenced_code", "tables", "codehilite", "sane_lists"],
        )

    templates.env.filters["markdown"] = _md_render
except ImportError:  # 兜底
    def _md_render(text: str) -> str:  # type: ignore[no-redef]
        return f"<pre>{text}</pre>"

    templates.env.filters["markdown"] = _md_render


router = APIRouter()


# ---------------------------------------------------------------------------
# 流水线阶段常量（5 阶段：大目标 → 实验设计 → 执行 → 结果分析 → 总结）
# ---------------------------------------------------------------------------

PIPELINE_STAGES: list[tuple[int, str]] = [
    (1, "目标定义"),
    (2, "实验设计"),
    (3, "实验执行"),
    (4, "结果分析"),
    (5, "总结输出"),
]


# 一个便捷函数：渲染模板（统一 request 注入 + 全局上下文）
def render(
    request: Request, name: str, context: dict | None = None,
    *, status_code: int = 200, **kwargs,
) -> HTMLResponse:
    """统一封装 TemplateResponse，注入全局上下文。

    自动从 URL 推断 active_project，让右侧栏快速记录知道写到哪个项目。
    优先级：上下文里的 active_project > URL 中的 /projects/{id} > 第一个 active 项目。
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        projects_for_switcher = crud.list_projects(db)
        rightbar_decisions = crud.list_open_decisions(db, limit=8)
        ctx_in = context or {}
        if "active_project" not in ctx_in:
            ctx_in["active_project"] = _infer_active_project(request, db)
    finally:
        db.close()

    ctx = {
        "request": request,
        "projects_for_switcher": projects_for_switcher,
        "rightbar_decisions": rightbar_decisions,
        "app_name": settings.app_name,
        "pipeline_stages": PIPELINE_STAGES,
        **ctx_in,
        **kwargs,
    }
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _infer_active_project(request: Request, db) -> models.Project | None:
    """从 URL 路径推断当前打开的项目。

    /projects/{id}/...      → 取 id
    /projects               → 不推断（让用户主动选）
    其他路径                → 最近更新的 active 项目
    """
    path: str = request.url.path
    parts = path.strip("/").split("/")

    # /projects/{id}/...
    if len(parts) >= 2 and parts[0] == "projects" and parts[1].isdigit():
        return crud.get_project(db, int(parts[1]))

    # /projects 列表页 → 不推断
    if len(parts) >= 1 and parts[0] == "projects":
        return None

    # 其它页面（首页 / 等）→ 最近更新的 active 项目
    for p in crud.list_projects(db):
        if p.status == "active":
            return p
    return crud.list_projects(db)[0] if crud.list_projects(db) else None


# ---------------------------------------------------------------------------
# 仪表盘
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """跨项目战情室：所有项目网格 + 顶部全局统计。"""
    projects = crud.list_projects(db)

    # 全局统计
    total_projects = crud.count_all_projects(db)
    total_experiments = crud.count_all_experiments(db)
    total_results = crud.count_results_uploaded(db)
    total_pending = total_experiments - total_results
    total_open_decisions = crud.count_open_decisions(db)

    # 每张项目卡的统计
    project_cards = []
    for p in projects:
        stats = crud.get_project_stats(db, p.id)
        # 当前阶段名（1-5 → 中文）
        stage_idx = max(0, min(4, (p.current_stage or 1) - 1))
        stage_name = PIPELINE_STAGES[stage_idx][1]
        project_cards.append(
            {
                "project": p,
                "stats": stats,
                "stage_name": stage_name,
            }
        )

    return render(
        request, "dashboard.html",
        {
            "active_nav": "today",
            "total_projects": total_projects,
            "total_experiments": total_experiments,
            "total_results": total_results,
            "total_pending": total_pending,
            "total_open_decisions": total_open_decisions,
            "project_cards": project_cards,
        },
    )


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.get("/projects", response_class=HTMLResponse)
def project_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """项目列表（卡片式）。"""
    projects = crud.list_projects(db)
    experiment_counts = {p.id: crud.count_experiments(db, p.id) for p in projects}
    return render(
        request, "project_list.html",
        {
            "active_nav": "projects",
            "projects": projects,
            "experiment_counts": experiment_counts,
        },
    )


@router.get("/projects/new", response_class=HTMLResponse)
def new_project_form(request: Request) -> HTMLResponse:
    return render(
        request, "project_form.html",
        {"project": None, "action": "Create"},
    )


@router.post("/projects")
def create_project(
    request: Request,
    name: str = Form(..., min_length=1, max_length=100),
    description: str = Form(""),
    tags: str = Form(""),
    current_stage: int = Form(1),
    db: Session = Depends(get_db),
):
    data = schemas.ProjectCreate(
        name=name, description=description, tags=tags,
        current_stage=current_stage,
    )
    if crud.get_project_by_name(db, data.name):
        return render(
            request, "project_form.html",
            {"project": data, "action": "Create",
             "error": f"项目名 '{data.name}' 已存在"},
            status_code=400,
        )
    project = crud.create_project(db, data)
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(
    project_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")

    # 大目标列表（每个目标下挂实验 + 指标/笔记计数）
    goals = crud.list_goals(db, project_id)
    goal_cards = []
    for g in goals:
        exps = [
            {
                "experiment": exp,
                "metric_count": crud.count_metrics(db, exp.id),
                "note_count": crud.count_notes(db, exp.id),
            }
            for exp in g.experiments
        ]
        goal_cards.append(
            {
                "goal": g,
                "experiments": exps,
                "completed_count": sum(
                    1 for e in exps if e["experiment"].status == "completed"
                ),
            }
        )

    decisions = crud.list_decisions(db, project_id)

    return render(
        request, "project_detail.html",
        {
            "active_nav": "projects",
            "project": project,
            "goal_cards": goal_cards,
            "decisions": decisions,
        },
    )


@router.get("/projects/{project_id}/edit", response_class=HTMLResponse)
def edit_project_form(
    project_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return render(
        request, "project_form.html",
        {"project": project, "action": "Edit"},
    )


@router.post("/projects/{project_id}")
def update_project(
    project_id: int,
    name: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    current_stage: int = Form(1),
    db: Session = Depends(get_db),
):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    data = schemas.ProjectUpdate(
        name=name, description=description, tags=tags,
        current_stage=current_stage,
    )
    crud.update_project(db, project, data)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/delete")
def delete_project(
    project_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    crud.delete_project(db, project)
    return RedirectResponse("/", status_code=303)


@router.post("/projects/{project_id}/stage")
def set_project_stage(
    project_id: int,
    stage: int = Form(..., ge=1, le=5),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """点击流水线 stepper 切换项目当前阶段。"""
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    project.current_stage = stage
    db.commit()
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# 类型标签映射（右侧栏快速记录 → 决策内容前缀）
QUICK_TYPE_PREFIX = {
    "task": "📋 任务",
    "idea": "💡 科研想法",
    "simulation": "⚠️ 仿真问题",
    "revision": "📝 论文修改点",
}


@router.post("/projects/{project_id}/quick-records")
def add_quick_record(
    project_id: int,
    content: str = Form(..., min_length=1),
    record_type: str = Form("task"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """右侧栏快速记录：以决策形式入库。"""
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    prefix = QUICK_TYPE_PREFIX.get(record_type, "📌")
    crud.create_decision(
        db, project_id,
        schemas.DecisionCreate(content=f"{prefix} {content}"),
    )
    # 回到发起页（Referer），否则回到项目详情
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/decisions")
def add_decision(
    project_id: int,
    content: str = Form(..., min_length=1),
    db: Session = Depends(get_db),
):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    crud.create_decision(db, project_id, schemas.DecisionCreate(content=content))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/decisions/{decision_id}/resolve")
def resolve_decision(
    decision_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    dec = crud.get_decision(db, decision_id)
    if dec is None:
        raise HTTPException(404, "Decision not found")
    crud.resolve_decision(db, dec)
    return RedirectResponse(f"/projects/{dec.project_id}", status_code=303)


@router.post("/decisions/{decision_id}/delete")
def delete_decision(
    decision_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    dec = crud.get_decision(db, decision_id)
    if dec is None:
        raise HTTPException(404, "Decision not found")
    project_id = dec.project_id
    crud.delete_decision(db, dec)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# ---------------------------------------------------------------------------
# Goal (大目标) CRUD
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/goals/new", response_class=HTMLResponse)
def new_goal_form(
    project_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return render(
        request, "goal_form.html",
        {"project": project, "goal": None, "action": "Create"},
    )


@router.post("/projects/{project_id}/goals")
def create_goal(
    project_id: int,
    name: str = Form(..., min_length=1, max_length=200),
    description: str = Form(""),
    order: int | None = Form(None),
    db: Session = Depends(get_db),
):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    data = schemas.GoalCreate(
        name=name, description=description, order=order,
    )
    goal = crud.create_goal(db, project_id, data)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.get("/goals/{goal_id}/edit", response_class=HTMLResponse)
def edit_goal_form(
    goal_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    goal = crud.get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    project = crud.get_project(db, goal.project_id)
    return render(
        request, "goal_form.html",
        {"project": project, "goal": goal, "action": "Edit"},
    )


@router.post("/goals/{goal_id}")
def update_goal(
    goal_id: int,
    name: str = Form(...),
    description: str = Form(""),
    order: int | None = Form(None),
    db: Session = Depends(get_db),
):
    goal = crud.get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    data = schemas.GoalUpdate(
        name=name, description=description, order=order,
    )
    crud.update_goal(db, goal, data)
    return RedirectResponse(f"/projects/{goal.project_id}", status_code=303)


@router.post("/goals/{goal_id}/delete")
def delete_goal(
    goal_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    goal = crud.get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    project_id = goal.project_id
    crud.delete_goal(db, goal)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# ---------------------------------------------------------------------------
# Experiment CRUD
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/experiments/new", response_class=HTMLResponse)
def new_experiment_form(
    project_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    goals = crud.list_goals(db, project_id)
    return render(
        request, "experiment_form.html",
        {"project": project, "goals": goals,
         "experiment": None, "config_md": "", "action": "Create"},
    )


@router.post("/projects/{project_id}/experiments")
def create_experiment(
    project_id: int,
    name: str = Form(..., min_length=1, max_length=200),
    description: str = Form(""),
    hypothesis: str = Form(""),
    design_notes: str = Form(""),
    due_date: str = Form(""),
    git_commit: str = Form(""),
    config_md: str = Form(""),
    goal_id: int = Form(...),
    db: Session = Depends(get_db),
):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")

    # 校验大目标属于该项目
    goal = crud.get_goal(db, goal_id)
    if goal is None or goal.project_id != project_id:
        return render(
            request, "experiment_form.html",
            {"project": project, "goals": crud.list_goals(db, project_id),
             "experiment": None, "config_md": config_md,
             "action": "Create", "error": f"所选大目标不存在或不属于该项目"},
            status_code=400,
        )

    data = schemas.ExperimentCreate(
        name=name,
        description=description,
        hypothesis=hypothesis,
        design_notes=design_notes,
        due_date=due_date or None,
        git_commit=git_commit or None,
        config_md=config_md,
        goal_id=goal_id,
    )
    exp = crud.create_experiment(db, project_id, data)
    return RedirectResponse(f"/experiments/{exp.id}", status_code=303)


@router.get("/experiments/{experiment_id}", response_class=HTMLResponse)
def experiment_detail(
    experiment_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    project = crud.get_project(db, exp.project_id)
    goal = crud.get_goal(db, exp.goal_id) if exp.goal_id else None

    metrics = crud.list_metrics(db, experiment_id)
    notes = crud.list_notes(db, experiment_id)
    artifacts = crud.list_artifacts(db, experiment_id=experiment_id)

    return render(
        request, "experiment_detail.html",
        {
            "project": project,
            "goal": goal,
            "experiment": exp,
            "metrics": metrics,
            "notes": notes,
            "artifacts": artifacts,
        },
    )


# ---------------------------------------------------------------------------
# 实验结果上传（独立页面，与编辑设计分离）
# ---------------------------------------------------------------------------


@router.get("/experiments/{experiment_id}/results", response_class=HTMLResponse)
def upload_results_form(
    experiment_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    project = crud.get_project(db, exp.project_id)
    goal = crud.get_goal(db, exp.goal_id) if exp.goal_id else None
    metrics = crud.list_metrics(db, experiment_id)
    notes = crud.list_notes(db, experiment_id)
    artifacts = crud.list_artifacts(db, experiment_id=experiment_id)
    return render(
        request, "experiment_results.html",
        {
            "project": project,
            "goal": goal,
            "experiment": exp,
            "metrics": metrics,
            "notes": notes,
            "artifacts": artifacts,
        },
    )


@router.post("/experiments/{experiment_id}/results")
def save_results(
    experiment_id: int,
    result_summary: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    exp.result_summary = result_summary
    db.commit()
    return RedirectResponse(f"/experiments/{experiment_id}/results", status_code=303)


@router.get("/experiments/{experiment_id}/edit", response_class=HTMLResponse)
def edit_experiment_form(
    experiment_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    project = crud.get_project(db, exp.project_id)
    goals = crud.list_goals(db, exp.project_id)
    return render(
        request, "experiment_form.html",
        {"project": project, "goals": goals, "experiment": exp,
         "config_md": exp.config_md,
         "action": "Edit"},
    )


@router.post("/experiments/{experiment_id}")
def update_experiment(
    experiment_id: int,
    name: str = Form(...),
    description: str = Form(""),
    hypothesis: str = Form(""),
    design_notes: str = Form(""),
    due_date: str = Form(""),
    git_commit: str = Form(""),
    config_md: str = Form(""),
    goal_id: int = Form(...),
    db: Session = Depends(get_db),
):
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")

    # 校验大目标属于该项目
    goal = crud.get_goal(db, goal_id)
    if goal is None or goal.project_id != exp.project_id:
        project = crud.get_project(db, exp.project_id)
        return render(
            request, "experiment_form.html",
            {"project": project, "goals": crud.list_goals(db, exp.project_id),
             "experiment": exp, "config_md": config_md,
             "action": "Edit", "error": "所选大目标不存在或不属于该项目"},
            status_code=400,
        )

    data = schemas.ExperimentUpdate(
        name=name,
        description=description,
        hypothesis=hypothesis,
        design_notes=design_notes,
        due_date=due_date or None,
        git_commit=git_commit or None,
        config_md=config_md,
        goal_id=goal_id,
    )
    crud.update_experiment(db, exp, data)
    return RedirectResponse(f"/experiments/{experiment_id}", status_code=303)


@router.post("/experiments/{experiment_id}/delete")
def delete_experiment(
    experiment_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    project_id = exp.project_id
    crud.delete_experiment(db, exp)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# ---------------------------------------------------------------------------
# Metric & Note（页面内嵌表单提交）
# ---------------------------------------------------------------------------


@router.post("/experiments/{experiment_id}/metrics")
def add_metric(
    experiment_id: int,
    key: str = Form(...),
    value: float = Form(...),
    note: str = Form(""),
    step: int | None = Form(None),
    db: Session = Depends(get_db),
):
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    crud.create_metric(
        db, experiment_id,
        schemas.MetricCreate(key=key, value=value, note=note, step=step),
    )
    # 提交后回到「上传结果」页(用户加指标就是在这一页)
    return RedirectResponse(f"/experiments/{experiment_id}/results", status_code=303)


@router.post("/metrics/{metric_id}/delete")
def remove_metric(
    metric_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    metric = db.get(models.Metric, metric_id)
    if metric is None:
        raise HTTPException(404, "Metric not found")
    exp_id = metric.experiment_id
    crud.delete_metric(db, metric)
    return RedirectResponse(f"/experiments/{exp_id}", status_code=303)


@router.post("/experiments/{experiment_id}/notes")
def add_note(
    experiment_id: int,
    content: str = Form(..., min_length=1),
    db: Session = Depends(get_db),
):
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    crud.create_note(db, experiment_id, schemas.NoteCreate(content=content))
    return RedirectResponse(f"/experiments/{experiment_id}", status_code=303)


@router.post("/notes/{note_id}/delete")
def remove_note(
    note_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    note = db.get(models.Note, note_id)
    if note is None:
        raise HTTPException(404, "Note not found")
    exp_id = note.experiment_id
    crud.delete_note(db, note)
    return RedirectResponse(f"/experiments/{exp_id}", status_code=303)


# ---------------------------------------------------------------------------
# Artifact (文件与成果) - 表单上传 + 文件下载代理
# ---------------------------------------------------------------------------


@router.post("/experiments/{experiment_id}/artifacts")
async def upload_experiment_artifact(
    experiment_id: int,
    request: Request,
    file: "UploadFile" = Form(...),  # type: ignore[name-defined]
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    """实验页 / 结果页里上传文件。"""
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    # 大小限制
    from app.config import settings as _settings
    blob = await file.read()
    if len(blob) > _settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {_settings.max_upload_size_mb} MB 上限")
    # 把字节塞回去给 crud
    import io as _io
    file.file = _io.BytesIO(blob)
    crud.create_artifact(
        db, file,
        owner_kind="experiment",
        owner_id=experiment_id,
        description=description,
    )
    # 回上传发起页(Referer 优先,失败回结果页)
    referer = request.headers.get("referer")
    if referer and "/results" in referer:
        target = f"/experiments/{experiment_id}/results"
    else:
        target = f"/experiments/{experiment_id}"
    return RedirectResponse(target, status_code=303)


@router.post("/projects/{project_id}/artifacts")
async def upload_project_artifact(
    project_id: int,
    file: "UploadFile" = Form(...),  # type: ignore[name-defined]
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    from app.config import settings as _settings
    blob = await file.read()
    if len(blob) > _settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {_settings.max_upload_size_mb} MB 上限")
    import io as _io
    file.file = _io.BytesIO(blob)
    crud.create_artifact(
        db, file,
        owner_kind="project",
        owner_id=project_id,
        description=description,
    )
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/goals/{goal_id}/artifacts")
async def upload_goal_artifact(
    goal_id: int,
    file: "UploadFile" = Form(...),  # type: ignore[name-defined]
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    goal = crud.get_goal(db, goal_id)
    if goal is None:
        raise HTTPException(404, "Goal not found")
    from app.config import settings as _settings
    blob = await file.read()
    if len(blob) > _settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {_settings.max_upload_size_mb} MB 上限")
    import io as _io
    file.file = _io.BytesIO(blob)
    crud.create_artifact(
        db, file,
        owner_kind="goal",
        owner_id=goal_id,
        description=description,
    )
    return RedirectResponse(f"/projects/{goal.project_id}", status_code=303)


@router.post("/artifacts/{artifact_id}/delete")
def delete_artifact_page(
    artifact_id: int, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    art = crud.get_artifact(db, artifact_id)
    if art is None:
        raise HTTPException(404, "Artifact not found")
    # 记录下归属,删完跳回去
    if art.experiment_id:
        target = f"/experiments/{art.experiment_id}/results"
    elif art.goal_id:
        goal = crud.get_goal(db, art.goal_id)
        target = f"/projects/{goal.project_id}" if goal else "/"
    elif art.project_id:
        target = f"/projects/{art.project_id}"
    else:
        target = "/"
    crud.delete_artifact(db, art)
    return RedirectResponse(target, status_code=303)


# ---------------------------------------------------------------------------
# 周复盘 /review
# ---------------------------------------------------------------------------


def _monday_of(d: datetime) -> datetime:
    """返回给定日期所在周的周一。"""
    from datetime import timedelta
    return d - timedelta(days=d.weekday())


@router.get("/review", response_class=HTMLResponse)
def review_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    reviews = crud.list_weekly_reviews(db, limit=100)
    # 给新建按钮算一个默认的本周一
    today = datetime.utcnow()
    this_monday = _monday_of(today).date()
    return render(
        request, "review.html",
        {
            "active_nav": "review",
            "reviews": reviews,
            "this_monday": this_monday.isoformat(),
        },
    )


@router.get("/review/new", response_class=HTMLResponse)
def new_review_form(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    today = datetime.utcnow()
    this_monday = _monday_of(today).date()
    return render(
        request, "weekly_review_form.html",
        {
            "active_nav": "review",
            "review": None,
            "week_start_default": this_monday.isoformat(),
            "action": "Create",
        },
    )


@router.post("/review/new")
def create_review(
    request: Request,
    week_start_date: str = Form(...),
    title: str = Form(""),
    content: str = Form(""),
    highlights: str = Form(""),
    blockers: str = Form(""),
    next_focus: str = Form(""),
    db: Session = Depends(get_db),
):
    from datetime import date
    try:
        d = date.fromisoformat(week_start_date)
    except ValueError:
        return render(
            request, "weekly_review_form.html",
            {
                "active_nav": "review",
                "review": None,
                "week_start_default": week_start_date,
                "action": "Create",
                "error": "起始日期格式不对（应为 YYYY-MM-DD）",
            },
            status_code=400,
        )
    # 默认标题
    if not title:
        from datetime import timedelta
        title = f"第 {d.isocalendar()[1]} 周 ({d.isoformat()} ~ {(d + timedelta(days=6)).isoformat()})"
    review = crud.create_weekly_review(
        db,
        schemas.WeeklyReviewCreate(
            week_start_date=d,
            title=title,
            content=content,
            highlights=highlights,
            blockers=blockers,
            next_focus=next_focus,
        ),
    )
    return RedirectResponse(f"/review/{review.id}", status_code=303)


@router.get("/review/{review_id}", response_class=HTMLResponse)
def review_detail(
    review_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    review = crud.get_weekly_review(db, review_id)
    if review is None:
        raise HTTPException(404, "WeeklyReview not found")
    return render(
        request, "weekly_review_detail.html",
        {"active_nav": "review", "review": review},
    )


@router.get("/review/{review_id}/edit", response_class=HTMLResponse)
def edit_review_form(
    review_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    review = crud.get_weekly_review(db, review_id)
    if review is None:
        raise HTTPException(404, "WeeklyReview not found")
    return render(
        request, "weekly_review_form.html",
        {
            "active_nav": "review",
            "review": review,
            "week_start_default": review.week_start_date.isoformat(),
            "action": "Edit",
        },
    )


@router.post("/review/{review_id}")
def update_review(
    review_id: int,
    week_start_date: str = Form(...),
    title: str = Form(""),
    content: str = Form(""),
    highlights: str = Form(""),
    blockers: str = Form(""),
    next_focus: str = Form(""),
    db: Session = Depends(get_db),
):
    review = crud.get_weekly_review(db, review_id)
    if review is None:
        raise HTTPException(404, "WeeklyReview not found")
    from datetime import date
    try:
        d = date.fromisoformat(week_start_date)
    except ValueError:
        return render(
            request, "weekly_review_form.html",
            {
                "active_nav": "review",
                "review": review,
                "week_start_default": week_start_date,
                "action": "Edit",
                "error": "起始日期格式不对（应为 YYYY-MM-DD）",
            },
            status_code=400,
        )
    crud.update_weekly_review(
        db, review,
        schemas.WeeklyReviewUpdate(
            week_start_date=d,
            title=title,
            content=content,
            highlights=highlights,
            blockers=blockers,
            next_focus=next_focus,
        ),
    )
    return RedirectResponse(f"/review/{review_id}", status_code=303)


@router.post("/review/{review_id}/delete")
def delete_review(
    review_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    review = crud.get_weekly_review(db, review_id)
    if review is None:
        raise HTTPException(404, "WeeklyReview not found")
    crud.delete_weekly_review(db, review)
    return RedirectResponse("/review", status_code=303)


# ---------------------------------------------------------------------------
# 占位导航页面（避免侧栏点击 404）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 搜索（顶栏搜索框 → /search?q=）
# ---------------------------------------------------------------------------


@router.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = "",
    fulltext: int = 0,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """全库搜索页。

    默认「快速搜索」只匹配标题/名称类字段；?fulltext=1 切「全文搜索」，
    追加正文 LIKE 扫全表。个人数据量小，毫秒级返回。
    """
    query = q.strip()
    groups = (
        crud.search_all(db, query, fulltext=bool(fulltext), limit=settings.page_size)
        if query
        else []
    )
    total = sum(len(g["items"]) for g in groups)
    return render(
        request, "search.html",
        {
            "active_nav": "",  # 侧栏任何一项都不点亮
            "q": query,
            "fulltext": bool(fulltext),
            "groups": groups,
            "total": total,
        },
    )


# 设置模块已是真实页面（见下），占位页注册列表置空。
# placeholder.html 保留供未来可能的新占位页使用。
_PLACEHOLDER_PAGES: list = []


for _key, _icon, _title, _desc in _PLACEHOLDER_PAGES:

    def _make_page(key: str = _key, icon: str = _icon,
                   title: str = _title, desc: str = _desc):
        def _page(request: Request) -> HTMLResponse:
            return render(
                request, "placeholder.html",
                {"active_nav": key, "page_icon": icon,
                 "page_title": title, "page_desc": desc},
            )
        return _page

    router.add_api_route(
        f"/{_key}", _make_page(), methods=["GET"],
        response_class=HTMLResponse,
    )


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    """设置页：外观 / 数据与存储 / 常规 / 关于。

    saved / error 用 query param 传递（POST 后 303 重定向回来），
    是项目无 flash 机制下的轻量替代。
    """
    health = settings_service.get_db_health()
    return render(
        request, "settings.html",
        {
            "active_nav": "settings",
            "saved": request.query_params.get("saved"),
            "error": request.query_params.get("error"),
            "db_path": settings.db_path,
            # 输入框按「文件夹」语义展示：db 文件所在目录，保存时自动补 workbench.db
            "db_dir": str(Path(settings.db_path).parent),
            "artifact_dir": settings.artifact_dir,
            "max_upload_size_mb": settings.max_upload_size_mb,
            "app_name": settings.app_name,
            "page_size": settings.page_size,
            "version": APP_VERSION,
            "license": APP_LICENSE,
            "db_health": health,
        },
    )


@router.post("/settings/storage")
def update_storage(
    db_path: str = Form(...),
    artifact_dir: str = Form(...),
    max_upload_size_mb: int = Form(100, ge=1, le=2048),
):
    """保存存储设置；路径变更时自动迁移（先复制后删除，失败回滚）。

    db_path 按「文件夹」理解：用户填的是存放数据库的目录（不必记得文件名），
    自动在该目录下使用固定文件名 workbench.db；也兼容直接填完整 .db 路径。
    先对 db + artifact 两项目标统一预检（可写/已存在/嵌套），
    全部通过才执行迁移，避免 db 已迁而 artifact 目标不可写的半迁移。
    """
    db_path = db_path.strip()
    if not db_path.lower().endswith(".db"):
        db_path = str(Path(db_path) / "workbench.db")
    artifact_dir = artifact_dir.strip()
    try:
        settings_service.preflight_migrations(db_path, artifact_dir)
        if settings_service.get_db_path() != settings_service.resolve_user_path(db_path):
            settings_service.migrate_db(db_path)
        if settings_service.resolve_user_path(settings.artifact_dir) != settings_service.resolve_user_path(artifact_dir):
            settings_service.migrate_artifact_dir(artifact_dir)
        if max_upload_size_mb != settings.max_upload_size_mb:
            save_setting("max_upload_size_mb", max_upload_size_mb)
    except settings_service.SettingsError as exc:
        return RedirectResponse(f"/settings?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/settings?saved=storage", status_code=303)


@router.post("/settings/general")
def update_general(
    page_size: int = Form(20, ge=5, le=200),
):
    # 应用名称是固定属性（经 .env 的 WORKBENCH_APP_NAME 启动时设置），不在 UI 编辑
    save_setting("page_size", page_size)
    return RedirectResponse("/settings?saved=general", status_code=303)


@router.post("/settings/restore")
def restore_settings(backup: UploadFile = File(...)):
    """从备份 zip 一键恢复全部数据（数据库 + 上传文件 + 配置）。

    不加 Depends(get_db)：restore 会替换数据库文件，持有旧 engine 的 session
    在 Windows 上会导致文件被占用而替换失败。
    """
    if not backup.filename or not backup.filename.lower().endswith(".zip"):
        return RedirectResponse(
            f"/settings?error={quote('请选择 .zip 备份包')}", status_code=303
        )
    fd, tmp = tempfile.mkstemp(prefix="wb-upload-", suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(backup.file, out)  # 流式落盘，不整包进内存
        settings_service.restore_backup(Path(tmp))
    except settings_service.SettingsError as exc:
        return RedirectResponse(
            f"/settings?error={quote(str(exc))}", status_code=303
        )
    except OSError as exc:
        return RedirectResponse(
            f"/settings?error={quote(f'上传保存失败：{exc}')}", status_code=303
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    return RedirectResponse("/settings?saved=restore", status_code=303)


# ---------------------------------------------------------------------------
# 思维导图（Mind Map）
# ---------------------------------------------------------------------------


@router.get("/mindmap", response_class=HTMLResponse)
def mindmap_list(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """思维导图项目选择页：列出所有项目，每个项目一张导图。"""
    projects = crud.list_projects(db)
    experiment_counts = {p.id: crud.count_experiments(db, p.id) for p in projects}
    return render(
        request, "mindmap_list.html",
        {
            "active_nav": "mindmap",
            "projects": projects,
            "experiment_counts": experiment_counts,
        },
    )


def _render_mindmap_node(node) -> str:
    """把一个节点序列化成 SVG <g> 字符串。"""
    # 转义 XML 特殊字符
    from xml.sax.saxutils import escape as xml_escape
    safe_label = xml_escape(node.label or "")
    safe_id = xml_escape(str(node.id))
    kind_cls = f"kind-{node.kind}"
    shape_cls = f"shape-{node.shape_type}"
    x = float(node.x)
    y = float(node.y)
    w = float(node.w)
    h = float(node.h)

    # z_index / parent_id 也塞进 data 属性，方便前端拖拽重绘连线 + 置顶置底
    parent_attr = f' data-parent="{node.parent_id}"' if node.parent_id else ""
    z_attr = f' data-z="{node.z_index}"'

    g_open = (
        f'<g class="mm-node {kind_cls} {shape_cls}" '
        f'data-id="{safe_id}" '
        f'data-kind="{xml_escape(node.kind)}" '
        f'data-shape="{xml_escape(node.shape_type)}"'
        f'{parent_attr}{z_attr} '
        f'data-w="{w}" data-h="{h}" '
        f'transform="translate({x},{y})">'
    )

    # 形状部分
    shape_svg = ""
    if node.shape_type == "ellipse":
        rx, ry = w / 2, h / 2
        shape_svg = f'<ellipse cx="{rx}" cy="{ry}" rx="{rx}" ry="{ry}" class="mm-shape"/>'
    elif node.shape_type == "diamond":
        shape_svg = (
            f'<polygon points="{w/2},0 {w},{h/2} {w/2},{h} 0,{h/2}" class="mm-shape"/>'
        )
    elif node.shape_type == "hexagon":
        # 六边形（左右各切一个角）
        cut = min(20.0, w / 4)
        shape_svg = (
            f'<polygon points="{cut},0 {w-cut},0 {w},{h/2} {w-cut},{h} {cut},{h} 0,{h/2}" '
            f'class="mm-shape"/>'
        )
    elif node.shape_type == "arrow":
        # 箭头形（五边形）
        shape_svg = (
            f'<polygon points="0,{h*0.3} {w*0.7},{h*0.3} {w*0.7},0 {w},{h/2} '
            f'{w*0.7},{h} {w*0.7},{h*0.7} 0,{h*0.7}" class="mm-shape"/>'
        )
    elif node.shape_type == "text":
        # 纯文本框：无背景
        shape_svg = ""
    else:
        # rect / rounded / sticky-*：默认矩形 + rx
        rx = "12" if node.shape_type == "rounded" else "4"
        if node.shape_type.startswith("sticky-"):
            rx = "6"
        shape_svg = f'<rect width="{w}" height="{h}" rx="{rx}" class="mm-shape"/>'

    # 文字部分（foreignObject 支持多行 + 居中）
    font_size = int(getattr(node, "font_size", 13) or 13)
    # 防止有人手填了 0/负值/超大值
    if font_size < 8:
        font_size = 8
    elif font_size > 96:
        font_size = 96
    font_family = getattr(node, "font_family", None) or "system"
    # 容错：未知值回退到 system（前端 CSS 也有兜底）
    from app.schemas import FONT_FAMILIES
    family_stack = FONT_FAMILIES.get(font_family, FONT_FAMILIES["system"])
    text_svg = (
        f'<foreignObject x="0" y="0" width="{w}" height="{h}">'
        f'<div xmlns="http://www.w3.org/1999/xhtml" '
        f'class="mm-label" '
        f'style="font-size:{font_size}px;font-family:{family_stack}">{safe_label}</div>'
        f'</foreignObject>'
    )

    g_close = "</g>"
    return g_open + shape_svg + text_svg + g_close


@router.get("/projects/{project_id}/mindmap", response_class=HTMLResponse)
def mindmap_editor(
    project_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """画布编辑器：服务端渲染首屏 SVG，JS 接管后续交互。"""
    project = crud.get_project(db, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")

    # 进入即触发同步（GET 即 sync，首次访问自动建树）
    mm = crud.get_or_create_mindmap(db, project_id)
    diff = crud.sync_auto_tree(db, project_id)
    nodes = crud.list_nodes(db, mm.id)
    edges = crud.list_edges(db, mm.id)

    # 服务端渲染 SVG 节点（首屏不空白）
    nodes_svg = "\n".join(_render_mindmap_node(n) for n in nodes)

    # 自动树连线（父→子）+ 手动连线（source→target）
    by_id = {n.id: n for n in nodes}

    def _bezier_path(x1: float, y1: float, x2: float, y2: float) -> str:
        mid_x = (x1 + x2) / 2
        return f"M {x1},{y1} C {mid_x},{y1} {mid_x},{y2} {x2},{y2}"

    edges_svg_parts: list[str] = []

    # 1) 自动树连线
    for n in nodes:
        if n.parent_id and n.parent_id in by_id:
            p = by_id[n.parent_id]
            px, py = float(p.x), float(p.y)
            pw, ph = float(p.w), float(p.h)
            cx, cy = float(n.x), float(n.y)
            ch = float(n.h)
            x1 = px + pw
            y1 = py + ph / 2
            x2 = cx
            y2 = cy + ch / 2
            edges_svg_parts.append(
                f'<path class="mm-edge mm-edge-auto" '
                f'd="{_bezier_path(x1, y1, x2, y2)}" '
                f'data-source="{p.id}" data-target="{n.id}"/>'
            )

    # 2) 手动连线（source → target）
    for e in edges:
        src = by_id.get(e.source_id)
        tgt = by_id.get(e.target_id)
        if src is None or tgt is None:
            continue  # 节点已被删但 FK CASCADE 漏的边，兜底跳过
        sx, sy = float(src.x), float(src.y)
        sw, sh = float(src.w), float(src.h)
        tx, ty = float(tgt.x), float(tgt.y)
        th = float(tgt.h)
        # 源右侧中点 → 目标左侧中点
        x1, y1 = sx + sw, sy + sh / 2
        x2, y2 = tx, ty + th / 2
        marker = ' marker-end="url(#mm-arrow)"' if e.arrow else ""
        edges_svg_parts.append(
            f'<path class="mm-edge mm-edge-manual" '
            f'd="{_bezier_path(x1, y1, x2, y2)}" '
            f'data-id="{e.id}" '
            f'data-source="{e.source_id}" data-target="{e.target_id}" '
            f'data-arrow="{str(e.arrow).lower()}"'
            f'{marker}/>'
        )

    edges_svg = "\n".join(edges_svg_parts)

    # 简单统计：auto / manual 节点数 + 手动边数
    auto_count = sum(1 for n in nodes if n.kind == "auto")
    manual_count = sum(1 for n in nodes if n.kind == "manual")
    edge_count = len(edges)

    return render(
        request, "mindmap_editor.html",
        {
            "active_nav": "mindmap",
            "project": project,
            "nodes_svg": nodes_svg,
            "edges_svg": edges_svg,
            "auto_count": auto_count,
            "manual_count": manual_count,
            "edge_count": edge_count,
            "sync_diff": diff,
        },
    )