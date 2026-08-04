"""数据库连接与会话管理。

FastAPI 中每个请求一个 Session。用法示例：

    from app.database import get_db

    @app.get("/items")
    def list_items(db: Session = Depends(get_db)):
        return db.query(Item).all()

启动时调用 init_db() 创建所有表。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_db_url


# 引擎：SQLite 需要 check_same_thread=False 允许多线程访问
engine = create_engine(
    get_db_url(),
    echo=False,  # True 会打印所有 SQL，调试时打开
    connect_args={"check_same_thread": False},
    future=True,
)

# Session 工厂
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：提供一个 DB Session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """创建所有表。幂等：已存在则跳过。"""
    # 必须在导入模型后调用，让 SQLAlchemy 知道有哪些表
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)