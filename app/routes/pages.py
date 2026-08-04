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

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import crud, models, schemas
from app.config import settings
from app.database import get_db
from sqlalchemy import select

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
# 占位导航页面（避免侧栏点击 404，后续按需扩展）
# ---------------------------------------------------------------------------


_PLACEHOLDER_PAGES = [
    ("cases", "🧪", "仿真与试验", "工况矩阵、参数扫描、试验数据汇总 — 后续接入"),
    ("files", "📦", "文件与成果", "数据、模型权重、图表、附件 — 后续接入"),
    ("review", "📅", "周复盘", "每周回顾、关键指标、卡点 — 后续接入"),
    ("settings", "⚙️", "设置", "主题、标签、导入导出 — 后续接入"),
]


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
         "experiment": None, "config_json": "", "action": "Create"},
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
    config_json: str = Form("{}"),
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
             "experiment": None, "config_json": config_json,
             "action": "Create", "error": f"所选大目标不存在或不属于该项目"},
            status_code=400,
        )

    try:
        config = json.loads(config_json) if config_json.strip() else {}
    except json.JSONDecodeError as e:
        return render(
            request, "experiment_form.html",
            {"project": project, "goals": crud.list_goals(db, project_id),
             "experiment": None, "config_json": config_json,
             "action": "Create", "error": f"Config 不是合法 JSON: {e}"},
            status_code=400,
        )

    data = schemas.ExperimentCreate(
        name=name,
        description=description,
        hypothesis=hypothesis,
        design_notes=design_notes,
        due_date=due_date or None,
        git_commit=git_commit or None,
        config=config,
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

    # 按 key 分组，供前端画图
    grouped: dict[str, list[dict]] = defaultdict(list)
    for m in metrics:
        grouped[m.key].append({"step": m.step, "value": m.value, "id": m.id})

    return render(
        request, "experiment_detail.html",
        {
            "project": project,
            "goal": goal,
            "experiment": exp,
            "metrics": metrics,
            "metrics_grouped": dict(grouped),
            "notes": notes,
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
    grouped: dict[str, list[dict]] = defaultdict(list)
    for m in metrics:
        grouped[m.key].append({"step": m.step, "value": m.value, "id": m.id})
    return render(
        request, "experiment_results.html",
        {
            "project": project,
            "goal": goal,
            "experiment": exp,
            "metrics": metrics,
            "metrics_grouped": dict(grouped),
            "notes": notes,
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
         "config_json": json.dumps(exp.config, ensure_ascii=False, indent=2),
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
    config_json: str = Form("{}"),
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
             "experiment": exp, "config_json": config_json,
             "action": "Edit", "error": "所选大目标不存在或不属于该项目"},
            status_code=400,
        )

    try:
        config = json.loads(config_json) if config_json.strip() else {}
    except json.JSONDecodeError as e:
        project = crud.get_project(db, exp.project_id)
        return render(
            request, "experiment_form.html",
            {"project": project, "goals": crud.list_goals(db, exp.project_id),
             "experiment": exp, "config_json": config_json,
             "action": "Edit", "error": f"Config 不是合法 JSON: {e}"},
            status_code=400,
        )

    data = schemas.ExperimentUpdate(
        name=name,
        description=description,
        hypothesis=hypothesis,
        design_notes=design_notes,
        due_date=due_date or None,
        git_commit=git_commit or None,
        config=config,
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
    step: int | None = Form(None),
    db: Session = Depends(get_db),
):
    exp = crud.get_experiment(db, experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    crud.create_metric(
        db, experiment_id, schemas.MetricCreate(key=key, value=value, step=step)
    )
    return RedirectResponse(f"/experiments/{experiment_id}", status_code=303)


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