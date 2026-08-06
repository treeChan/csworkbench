#!/usr/bin/env python3
"""Workbench 桌面版 sidecar 入口。

与 Rust 宿主(Tauri)的契约:
    argv  : --db <workbench.db 绝对路径>
            [--port <首选端口>]     默认 8750,被占用时向后探测空闲端口
            [--port-file <握手文件>] 额外把握手写进文件(兜底用)
            [--artifacts <上传文件根目录>] 必须指向持久化位置:
                  PyInstaller 里 BASE_DIR 是临时解压目录,不设的话
                  /files 上传的图片/权重会存到临时目录,重启就丢。
    stdout: 就绪后只输出一行 JSON  {"status":"ready","port":N}
            (uvicorn 的日志走 stderr,stdout 只留握手)
    stdin : 读到底(EOF)即退出 —— Rust 宿主进程退出/被杀时的兜底清理,
            避免留下孤儿 Python 进程。

关键点: WORKBENCH_DB_PATH 必须在 `import app` 之前设置
(app.database 在 import 时就会建 engine)。这里不修改 app/ 的任何代码,
SQLite 加固用 SQLAlchemy 事件监听器叠加。
"""

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path


def _load_persist_env(app_data_dir: Path) -> dict:
    """读 appdata/.env（用户设置持久层）。读不到就当空字典。"""
    try:
        from dotenv import dotenv_values

        vals = dotenv_values(app_data_dir / ".env")
        return {str(k): str(v) for k, v in (vals or {}).items() if v}
    except Exception:
        return {}


def pick_port(preferred: int) -> int:
    """从 preferred 开始向后找一个可绑定的端口。"""
    for off in range(64):
        candidate = preferred + off
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise SystemExit("no free port found")


def main() -> None:
    ap = argparse.ArgumentParser(prog="workbench-server")
    ap.add_argument("--db", required=True, help="workbench.db 的绝对路径")
    ap.add_argument("--port", type=int, default=8750, help="首选端口")
    ap.add_argument("--port-file", default=None, help="握手 JSON 的兜底输出文件")
    ap.add_argument("--artifacts", default=None, help="上传文件的持久化根目录")
    a = ap.parse_args()

    # ---- 持久化设置层 ----
    # 桌面端由 Rust 宿主通过 WORKBENCH_APP_DATA_DIR 传入 appdata 目录:
    #   1) 若 appdata/.env 已有用户设置(设置页改过的路径/配置),以它为准覆盖 --db 参数
    #   2) 首次启动时把当前生效路径写入,让设置页改路径后能跨重启保存
    # (PyInstaller 里 BASE_DIR 是临时解压目录,.env 写那里重启就丢)
    app_data_dir = os.environ.get("WORKBENCH_APP_DATA_DIR")
    persist = _load_persist_env(Path(app_data_dir)) if app_data_dir else {}

    db_raw = persist.get("WORKBENCH_DB_PATH") or a.db
    db = Path(db_raw).expanduser().resolve()
    db.parent.mkdir(parents=True, exist_ok=True)

    # ---- 必须在 import app 之前 ----
    # 项目根 = desktop/sidecar 的上级上级;直接跑本脚本时让 `import app` 可解析。
    # (PyInstaller 打包后 app 包从归档导入,此路径不存在也无害)
    _here = Path(__file__).resolve().parent        # desktop/sidecar
    _repo = _here.parent.parent                    # 项目根
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))

    os.environ["WORKBENCH_DB_PATH"] = str(db)
    os.environ.setdefault("WORKBENCH_APP_NAME", "Workbench")
    art_raw = persist.get("WORKBENCH_ARTIFACT_DIR") or a.artifacts
    if art_raw:
        art = Path(art_raw).expanduser().resolve()
        art.mkdir(parents=True, exist_ok=True)
        os.environ["WORKBENCH_ARTIFACT_DIR"] = str(art)
    else:
        art = None

    # 非路径配置(page_size 等)透传给 settings;显式环境变量 > appdata/.env
    for _k, _v in persist.items():
        if _k not in ("WORKBENCH_DB_PATH", "WORKBENCH_ARTIFACT_DIR"):
            os.environ.setdefault(_k, _v)

    # 首次启动:把当前生效路径持久化,设置页改路径后才能覆盖
    if app_data_dir and not persist.get("WORKBENCH_DB_PATH"):
        try:
            lines = [f"WORKBENCH_DB_PATH={db}"]
            if art:
                lines.append(f"WORKBENCH_ARTIFACT_DIR={art}")
            Path(app_data_dir).mkdir(parents=True, exist_ok=True)
            (Path(app_data_dir) / ".env").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
        except Exception:
            pass

    port = pick_port(a.port if a.port > 0 else 8750)
    handshake = json.dumps({"status": "ready", "port": port})

    # ---- stdin EOF 看门狗:宿主退出 -> 管道关闭 -> read 返回 b'' -> 自杀 ----
    if sys.stdin is not None and not sys.stdin.isatty():
        threading.Thread(
            target=lambda: (sys.stdin.buffer.read(), os._exit(0)),
            daemon=True,
        ).start()

    from app.main import app  # env 设置好后再导入

    # ---- SQLite 加固(WAL + busy_timeout),不碰 app/database.py ----
    try:
        from sqlalchemy import event
        from app.database import engine

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    except Exception:
        pass

    import uvicorn
    from uvicorn import Config, Server

    class ReadyServer(Server):
        """socket 真正绑定、lifespan(含 init_db)跑完后再发握手,避免导航竞态。"""

        def __init__(self, cfg, on_ready):
            super().__init__(cfg)
            self._on_ready = on_ready

        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self._on_ready:
                self._on_ready()

    def _on_ready():
        try:
            print(handshake, flush=True)  # stdout -> Rust 读取
        except Exception:
            pass
        if a.port_file:
            try:
                Path(a.port_file).write_text(handshake, encoding="utf-8")
            except Exception:
                pass

    ReadyServer(
        Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,  # 访问日志也走 stderr,避免噪声
        ),
        _on_ready,
    ).run()


if __name__ == "__main__":
    main()
