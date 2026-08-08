# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec:把 FastAPI 服务打成单文件 sidecar。

在对应平台(CI runner)上运行:
    python -m PyInstaller --clean --noconfirm workbench-server.spec
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve()   # desktop/sidecar
REPO = ROOT.parent.parent         # 仓库根(其下有 app/)
APP = REPO / "app"

datas, binaries, hiddenimports = [], [], []

# 依赖打包:collect_all 收集数据文件/二进制/隐藏导入,copy_metadata 保留包元数据
PKGS = [
    "fastapi", "starlette", "uvicorn", "pydantic", "pydantic_settings",
    "pydantic_core", "sqlalchemy", "jinja2", "markdown", "h11",
    "multipart", "greenlet", "anyio", "click", "typing_extensions",
]
for p in PKGS:
    try:
        d, b, h = collect_all(p)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
    try:
        datas += copy_metadata(p)
    except Exception:
        pass

# 动态导入兜底(sqlite 方言、uvicorn 子模块等)
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("sqlalchemy")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("jinja2")
hiddenimports += collect_submodules("markdown")
hiddenimports += ["greenlet"]

# 模板 / 静态资源:打进 sys._MEIPASS/app/ 下,精确匹配现有 BASE_DIR 相对路径计算
# (app/config.py 的 BASE_DIR = Path(__file__).parent.parent -> _MEIPASS)
datas.append((str(APP / "templates"), "app/templates"))
datas.append((str(APP / "static"), "app/static"))

# 版本号唯一源:仓库根 VERSION 打进 _MEIPASS/VERSION,config.py 运行时直接读它
# (web 版读项目根 VERSION,桌面版读打包进来的这份,两端始终一致)
datas.append((str(REPO / "VERSION"), "."))

a = Analysis(
    [str(ROOT / "entry.py")],
    pathex=[str(REPO)],  # 让 `import app` 可解析
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "numpy", "pandas", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="workbench-server",
    debug=False,
    strip=False,
    upx=False,
    # onefile + console:Windows 上 shell 插件用 CREATE_NO_WINDOW 压住黑框,
    # stdout 走管道给 Rust 读握手。
    console=True,
    disable_windowed_traceback=False,
)
