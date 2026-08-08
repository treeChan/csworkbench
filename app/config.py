"""应用配置。

从环境变量（或 .env 文件）读取设置。所有可配置项都集中在这里，
方便调试时一目了然。

典型用法：
    from app.config import settings
    print(settings.db_path)
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# 项目根目录 = workbench/（app/ 的父目录）
BASE_DIR = Path(__file__).resolve().parent.parent

# 当前应用版本（main.py 的 FastAPI version 与桌面端 tauri/Cargo 与此保持一致）
APP_VERSION = "0.4.1"

# 项目开源协议（SPDX 标识；根目录 LICENSE 文件为 MPL 2.0 全文）
APP_LICENSE = "MPL-2.0"

# 用户设置持久化文件（设置页写回这里；pydantic-settings 启动时读取）
#
# 网页版：项目根 /.env
# 桌面版：sidecar 由 Rust 宿主通过 WORKBENCH_APP_DATA_DIR 环境变量传入 appdata 目录，
#         用户设置写到 <appdata>/.env。原因：PyInstaller 里 BASE_DIR 是临时解压目录，
#         .env 写那里每次重启就丢；且 WORKBENCH_DB_PATH 环境变量优先级高于 .env，
#         必须让 appdata/.env 成为新的持久层，才能让「设置页改路径」跨重启生效。
USER_ENV_FILE = Path(os.environ.get("WORKBENCH_APP_DATA_DIR") or BASE_DIR) / ".env"

# 兼容旧引用（settings_service 直接用它做恢复时的快照 / 回滚）
ENV_FILE = USER_ENV_FILE


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

    # 上传文件存到哪里（相对 BASE_DIR）。图片、模型权重、附件都落在这里。
    artifact_dir: str = "data/artifacts"

    # 单个上传文件最大多少 MB（防止误传几个 G 的数据集）
    max_upload_size_mb: int = 100

    model_config = SettingsConfigDict(
        env_prefix="WORKBENCH_",  # 环境变量前缀
        env_file=str(USER_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


# 全局单例
settings = Settings()


def resolve_user_path(raw: str) -> Path:
    """统一路径解析：相对项目根 / 绝对路径 / `~` 家目录。

    注意 expanduser() 不能省:Python 不会自动展开 `~`。
    少了它,`~/Documents/wb.db` 会被当成相对路径,
    在项目里建出一个名字真叫 `~` 的文件夹。
    只解析不创建目录（目录由调用方按需创建）。
    """
    return (BASE_DIR / Path(raw).expanduser()).resolve()


def portable_path_for_backup(abs_path: Path) -> str:
    """把绝对路径转成备份包 config.env 里可移植的形式。

    项目内的路径 → 相对 BASE_DIR 的 posix 字符串（如 data/workbench.db）。
    相对路径不依赖具体机器，恢复时在另一台机器上自动落到它的项目目录。
    项目外的绝对路径 → 原样保留（恢复时预检会拦截不可达情况）。
    """
    p = abs_path.resolve()
    try:
        return p.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(p)


def resolve_portable_path(raw: str) -> Path:
    """恢复时把备份包里的路径解析到本机。

    相对路径（data/workbench.db）落到本机 BASE_DIR 下；
    绝对路径 / `~` 走 resolve_user_path 原逻辑。
    """
    return resolve_user_path(raw.strip())


# 派生路径：数据库绝对路径
def get_db_path() -> Path:
    """当前配置的数据库文件绝对路径（不创建目录）。"""
    return resolve_user_path(settings.db_path)


def get_db_url() -> str:
    """返回 SQLite 的连接 URL，并确保目录存在。

    db_path 支持三种写法:
        data/workbench.db          相对项目根目录
        /Users/xx/科研/wb.db        绝对路径
        ~/Documents/科研/wb.db      家目录
    """
    p = get_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p}"


def get_artifact_dir() -> Path:
    """返回上传文件的根目录绝对路径,并确保目录存在。

    跟 db_path 一样的规则:支持相对、绝对、~ 三种写法。
    """
    p = resolve_user_path(settings.artifact_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_setting(key: str, value) -> None:
    """写回 .env 并热更新全局 settings 单例。

    key 是 Settings 的字段名（如 db_path），env 键为 WORKBENCH_{KEY.upper()}。
    先 set_key 持久化、再 setattr 热更新；若写文件失败则不动 settings，
    调用方与引擎保持旧值，天然一致（迁移失败可安全回滚）。
    """
    from dotenv import set_key  # 惰性导入，桌面端缺依赖时仅设置页报错

    # 桌面端持久层在 appdata 下，目录未必存在，先建好（dotenv 要在同目录写临时文件）
    USER_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    set_key(USER_ENV_FILE, f"WORKBENCH_{key.upper()}", str(value))
    setattr(settings, key, value)