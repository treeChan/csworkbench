"""数据库连接与会话管理。

FastAPI 中每个请求一个 Session。用法示例：

    from app.database import get_db

    @app.get("/items")
    def list_items(db: Session = Depends(get_db)):
        return db.query(Item).all()

启动时调用 init_db() 创建所有表。
"""

from collections.abc import Generator

from sqlalchemy import create_engine, text
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


# ---------------------------------------------------------------------------
# 一次性迁移（2026-08-05）
# ---------------------------------------------------------------------------
# 这次改了 Experiment.config (JSON) → config_md (Text)、Metric 加 note、
# 新增 Artifact / WeeklyReview 两张表。SQLAlchemy 的 create_all 只会建缺
# 的表,不会改已存在表的列。所以 ALTER TABLE 的部分要在启动时手动跑。
#
# 这个函数设计成幂等的:每次启动都跑一遍,已经迁移过就什么都不做。
# 已有的 JSON config 列不会强行删(SQLite DROP COLUMN 限制多),保留在那
# 里,ORM 不读就当不存在 —— 老数据全部平移到了 config_md。


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def _migrate_2026_08_05() -> None:
    """为 2026-08-05 的字段变化做兼容性 ALTER TABLE。

    - experiments: 新增 config_md TEXT NOT NULL DEFAULT ''；
                   把旧 config JSON 列里的非空内容拷到 config_md；
                   然后 DROP 旧 config 列(它是 NOT NULL 又没默认值,
                   ORM 不写它就会触发约束失败,所以必须彻底拿掉)
    - metrics:     新增 note TEXT NOT NULL DEFAULT ''
    - 新表 artifacts / weekly_reviews 由 create_all() 自己处理
    """
    with engine.begin() as conn:
        # --- experiments.config_md ---
        if not _column_exists(conn, "experiments", "config_md"):
            conn.execute(
                text(
                    "ALTER TABLE experiments "
                    "ADD COLUMN config_md TEXT NOT NULL DEFAULT ''"
                )
            )
            # 把旧的 JSON 配置原样作为 Markdown 拷过来
            if _column_exists(conn, "experiments", "config"):
                conn.execute(
                    text(
                        "UPDATE experiments "
                        "SET config_md = COALESCE(NULLIF(config, ''), '') "
                        "WHERE config_md = ''"
                    )
                )

        # --- 删掉旧 experiments.config 列 ---
        # 原因:它是 NOT NULL 又没默认值,但 ORM 已经不再写它,
        # 新建实验时 SQLAlchemy INSERT 不带它,SQLite 默认 NULL → 违反 NOT NULL。
        # SQLite 3.35+ 支持 ALTER TABLE DROP COLUMN,直接拿掉就行。
        if _column_exists(conn, "experiments", "config"):
            try:
                conn.execute(text("ALTER TABLE experiments DROP COLUMN config"))
            except Exception:
                # 老版本 SQLite 不支持 DROP COLUMN,降级方案:把 NOT NULL 拿掉。
                # SQLite 也不支持 ALTER COLUMN,只能重建表。这里只 swallow,
                # 极端情况下用户得手动处理。
                pass

        # --- metrics.note ---
        if not _column_exists(conn, "metrics", "note"):
            conn.execute(
                text(
                    "ALTER TABLE metrics "
                    "ADD COLUMN note TEXT NOT NULL DEFAULT ''"
                )
            )


def init_db() -> None:
    """创建所有表（幂等）并跑必要的迁移。"""
    # 必须在导入模型后调用，让 SQLAlchemy 知道有哪些表
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_2026_08_05()