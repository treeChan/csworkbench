"""应用配置。

从环境变量（或 .env 文件）读取设置。所有可配置项都集中在这里，
方便调试时一目了然。

典型用法：
    from app.config import settings
    print(settings.db_path)
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录 = workbench/（app/ 的父目录）
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用配置项。

    字段都可以通过同名环境变量覆盖，例如：
        WORKBENCH_DB_PATH=~/data/wb.db uvicorn app.main:app
    """

    # 数据库文件路径（相对 BASE_DIR）
    db_path: str = "data/workbench.db"

    # 应用名称（用于标题、文档等）
    app_name: str = "Workbench"

    # 默认每页显示数量
    page_size: int = 20

    # 调试模式
    debug: bool = True

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_",  # 环境变量前缀
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# 全局单例
settings = Settings()


# 派生路径：数据库绝对路径
def get_db_url() -> str:
    """返回 SQLite 的连接 URL。

    db_path 支持三种写法:
        data/workbench.db          相对项目根目录
        /Users/xx/科研/wb.db        绝对路径
        ~/Documents/科研/wb.db      家目录(必须先 expanduser,见下)

    注意 expanduser() 不能省:Python 不会自动展开 `~`。
    少了它,`~/Documents/wb.db` 会被当成相对路径,
    在项目里建出一个名字真叫 `~` 的文件夹。
    """
    raw = Path(settings.db_path).expanduser()
    abs_path = (BASE_DIR / raw).resolve()
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{abs_path}"