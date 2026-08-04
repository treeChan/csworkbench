"""FastAPI 应用入口。

启动：
    uvicorn app.main:app --reload --port 8000

或运行根目录的 run.sh。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.database import init_db
from app.routes import api as api_routes
from app.routes import pages as page_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时初始化数据库。"""
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    description="个人实验 / 项目跟踪工作台。",
    version="0.1.0",
    lifespan=lifespan,
)

# 静态资源
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "app" / "static"),
    name="static",
)

# 页面路由（HTML）
app.include_router(page_routes.router)

# JSON API（供程序化调用）
app.include_router(api_routes.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok", "app": settings.app_name}