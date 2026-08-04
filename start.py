#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Workbench 一键启动脚本(跨平台)。

用法:
    python start.py                     # 默认端口 8000
    python start.py --port 9000         # 指定端口
    python start.py --data ~/我的科研数据  # 把数据库放到自选文件夹
    python start.py --no-browser        # 不自动打开浏览器

它会自动完成:
    1. 检查 Python 版本
    2. 创建虚拟环境 .venv(只在第一次)
    3. 安装依赖(只在 requirements.txt 变化时)
    4. 启动服务并打开浏览器

=== 给维护者的重要提示 ===
这个文件是"引导程序",它要在使用者**升级 Python 之前**就能跑起来,
才能把"请升级到 3.10"这句话显示给对方看。

所以这里**只能用极保守的语法**:
    - 不要用 f-string(3.6+),用 .format()
    - 不要用 := 海象运算符(3.8+)
    - 不要用 X | Y 类型标注(3.10+)
    - 不要用 match 语句(3.10+)
一旦用了新语法,老解释器会在**解析阶段**就报 SyntaxError,
使用者看到的将是一串看不懂的报错,而不是我们精心写的升级指引。
"""

import os
import subprocess
import sys

# ---------------------------------------------------------------- 版本闸门
# 必须放在所有其他 import 之前执行:venv / urllib 等模块本身是安全的,
# 但我们希望版本不达标时第一时间退出,而不是在后面某处莫名其妙地失败。
#
# 为什么是 3.10:app/models.py 和 app/schemas.py 用了 `int | None` 这种写法。
# 虽然它们都有 `from __future__ import annotations`(注解变成字符串),
# 但 SQLAlchemy 的 Mapped[] 和 Pydantic v2 都会在运行时真正求值这些注解,
# 在 3.9 上会抛 TypeError。所以 3.10 是硬性下限,不是保守估计。
MIN_PYTHON = (3, 10)

if sys.version_info < MIN_PYTHON:
    sys.stderr.write(
        "\n"
        "  ✗ Workbench 需要 Python {0}.{1} 或更高版本\n"
        "\n"
        "    你当前的版本是: Python {2}\n"
        "    位置: {3}\n"
        "\n"
        "  怎么解决:\n"
        "    1. 到 https://www.python.org/downloads/ 下载 3.12(推荐)\n"
        "    2. 安装时如果是 Windows,记得勾选 “Add Python to PATH”\n"
        "    3. 装好后重新打开终端,再运行一次本脚本\n"
        "\n"
        "    如果你电脑上装了多个 Python,也可以直接指定新的那个,例如:\n"
        "        python3.12 start.py\n"
        "\n".format(
            MIN_PYTHON[0],
            MIN_PYTHON[1],
            sys.version.split()[0],
            sys.executable,
        )
    )
    sys.exit(1)


# 版本达标后,再 import 其余标准库(这些都很老,不存在兼容问题)
import argparse
import hashlib
import socket
import threading
import time
import venv
import webbrowser
from pathlib import Path
from urllib.request import urlopen

# 项目根目录 = 本文件所在目录
ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
ENV_FILE = ROOT / ".env"
# 记录上次装依赖时 requirements.txt 的指纹,内容没变就跳过安装
DEPS_STAMP = VENV_DIR / ".deps-hash"

IS_WINDOWS = sys.platform.startswith("win")


def say(msg):
    """打印一行进度信息。"""
    print("  " + msg)
    sys.stdout.flush()


def venv_python():
    """返回虚拟环境里的 python 解释器路径(区分平台)。"""
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


# ---------------------------------------------------------------- 虚拟环境
def ensure_venv():
    """确保 .venv 存在。已存在就直接复用。"""
    if venv_python().exists():
        return

    say("首次运行,正在创建虚拟环境 .venv ...")
    say("(这一步只做一次,大概十几秒)")
    try:
        # with_pip=True 保证 venv 里有 pip
        venv.EnvBuilder(with_pip=True, clear=False).create(str(VENV_DIR))
    except Exception as exc:
        # 少数 Linux 发行版把 venv 拆成单独的包
        sys.stderr.write(
            "\n  ✗ 创建虚拟环境失败: {0}\n"
            "\n"
            "    如果你用的是 Ubuntu/Debian,可能需要先装:\n"
            "        sudo apt install python3-venv\n"
            "\n".format(exc)
        )
        sys.exit(1)
    say("✓ 虚拟环境已创建")


def requirements_fingerprint():
    """返回 requirements.txt 的内容指纹。"""
    data = REQUIREMENTS.read_bytes()
    return hashlib.sha256(data).hexdigest()


def ensure_deps():
    """安装依赖。requirements.txt 没变就跳过,避免每次启动都等 pip。"""
    fingerprint = requirements_fingerprint()

    if DEPS_STAMP.exists():
        try:
            if DEPS_STAMP.read_text(encoding="utf-8").strip() == fingerprint:
                return  # 依赖没变,跳过
        except OSError:
            pass  # 指纹文件读不了就当没有,重装一次

    say("正在安装依赖(第一次会久一点,请耐心等)...")
    cmd = [
        str(venv_python()), "-m", "pip", "install",
        "--disable-pip-version-check",
        "-q",
        "-r", str(REQUIREMENTS),
    ]
    result = subprocess.call(cmd)

    if result != 0:
        sys.stderr.write(
            "\n  ✗ 依赖安装失败\n"
            "\n"
            "    最常见的原因是网络连不上 PyPI。如果你在国内,试试镜像源:\n"
            "        {0} -m pip install -r requirements.txt \\\n"
            "            -i https://pypi.tuna.tsinghua.edu.cn/simple\n"
            "\n"
            "    装完之后再重新运行本脚本即可。\n"
            "\n".format(venv_python())
        )
        sys.exit(1)

    DEPS_STAMP.write_text(fingerprint, encoding="utf-8")
    say("✓ 依赖已就绪")


# ---------------------------------------------------------------- 数据目录
def normalize_data_path(raw):
    """把用户给的路径规整成一个数据库文件路径。

    给文件夹 → 自动补上 workbench.db
    给 .db 文件 → 原样使用
    """
    path = Path(os.path.expanduser(raw.strip())).expanduser()
    if path.suffix.lower() != ".db":
        path = path / "workbench.db"
    return path


def write_env(db_path):
    """把数据库路径写进 .env。

    注意:统一用正斜杠。Windows 的反斜杠写进 .env 后,
    python-dotenv 会当成转义符处理(比如 \\n \\t),路径就废了。
    正斜杠在 Windows 的 Python 里同样能用。
    """
    text = str(db_path).replace("\\", "/")
    ENV_FILE.write_text(
        "# 由 start.py 生成。想换数据位置,改这一行即可。\n"
        "WORKBENCH_DB_PATH={0}\n".format(text),
        encoding="utf-8",
    )


def ask_data_dir():
    """首次运行时问一下数据存哪儿。直接回车就用默认位置。"""
    print("")
    print("  ── 数据存放位置 ──────────────────────────────")
    print("  你的项目、实验、笔记都会存在一个 SQLite 文件里。")
    print("")
    print("  直接回车     = 存在本项目的 data/ 文件夹(推荐)")
    print("  或输入路径   = 存到你指定的文件夹")
    print("                 例如 ~/Documents/科研数据")
    print("                 (放到网盘同步目录就能自动备份)")
    print("")
    try:
        answer = input("  数据文件夹 [直接回车用默认]: ")
    except (EOFError, KeyboardInterrupt):
        # 非交互环境(比如双击运行被重定向)下不阻塞,直接用默认
        answer = ""

    if answer.strip():
        db_path = normalize_data_path(answer)
        write_env(db_path)
        print("")
        say("✓ 数据将存放在: {0}".format(db_path))
    else:
        print("")
        say("✓ 数据将存放在默认位置: {0}".format(ROOT / "data" / "workbench.db"))
    print("")


def setup_data_location(data_arg):
    """决定数据库位置。

    --data 显式指定 → 直接写 .env
    没有 .env(首次运行)→ 交互询问
    已有 .env → 不动,尊重用户之前的选择
    """
    if data_arg:
        db_path = normalize_data_path(data_arg)
        write_env(db_path)
        say("✓ 数据位置已设为: {0}".format(db_path))
        return

    if not ENV_FILE.exists():
        ask_data_dir()


# ---------------------------------------------------------------- 端口
def port_is_free(port):
    """检测端口能否绑定。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pick_port(preferred):
    """从 preferred 开始往后找一个空闲端口。"""
    for offset in range(0, 20):
        candidate = preferred + offset
        if port_is_free(candidate):
            return candidate
    sys.stderr.write(
        "\n  ✗ {0} 到 {1} 之间没有空闲端口。\n"
        "    可能是上一次的 Workbench 还在后台跑着。\n"
        "\n".format(preferred, preferred + 19)
    )
    sys.exit(1)


# ---------------------------------------------------------------- 启动
def open_browser_when_ready(url, health_url):
    """等服务真正起来了再开浏览器。

    直接开会撞上"还没启动完"的空白页,所以轮询 /health 端点
    (app/main.py 里定义的)确认返回 200 之后再开。
    """
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            response = urlopen(health_url, timeout=1)
            if response.getcode() == 200:
                webbrowser.open(url)
                return
        except Exception:
            pass
        time.sleep(0.4)
    # 超时就算了,用户可以自己点终端里打印的地址


def run_server(port, open_browser, dev_mode):
    """启动 uvicorn,并阻塞到用户 Ctrl+C。"""
    url = "http://127.0.0.1:{0}".format(port)

    print("")
    print("  ╭────────────────────────────────────────────╮")
    print("  │  Workbench 已启动                          │")
    print("  ╰────────────────────────────────────────────╯")
    print("")
    say("在浏览器打开: {0}".format(url))
    say("停止运行:     按 Ctrl+C")
    if dev_mode:
        say("开发模式:     改动 .py 文件后会自动重启")
    print("")

    if open_browser:
        thread = threading.Thread(
            target=open_browser_when_ready,
            args=(url, url + "/health"),
        )
        thread.daemon = True
        thread.start()

    cmd = [
        str(venv_python()), "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    if dev_mode:
        cmd.append("--reload")

    process = subprocess.Popen(cmd, cwd=str(ROOT))
    try:
        process.wait()
    except KeyboardInterrupt:
        print("")
        say("正在停止 ...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
        say("已停止。数据都已保存。")


# ---------------------------------------------------------------- 入口
def main():
    parser = argparse.ArgumentParser(
        description="启动 Workbench 科研工作台",
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="端口号,默认 8000(被占用时自动往后找)",
    )
    parser.add_argument(
        "--data", default=None, metavar="路径",
        help="数据存放的文件夹,例如 ~/Documents/科研数据",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="启动后不要自动打开浏览器",
    )
    parser.add_argument(
        "--dev", action="store_true",
        help="开发模式:改动 .py 文件后自动重启(改代码时用)",
    )
    args = parser.parse_args()

    if not REQUIREMENTS.exists():
        sys.stderr.write(
            "\n  ✗ 找不到 requirements.txt\n"
            "    请确认你是在 Workbench 项目文件夹里运行这个脚本。\n\n"
        )
        sys.exit(1)

    print("")
    say("Workbench 启动中 ...")

    ensure_venv()
    ensure_deps()
    setup_data_location(args.data)

    port = pick_port(args.port)
    if port != args.port:
        say("端口 {0} 被占用,改用 {1}".format(args.port, port))

    run_server(port, not args.no_browser, args.dev)


if __name__ == "__main__":
    main()
