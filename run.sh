#!/usr/bin/env bash
# Workbench 启动脚本(Mac / Linux)
#
# 用法:
#   ./run.sh                      默认端口 8000
#   ./run.sh --port 9000          指定端口
#   ./run.sh --data ~/科研数据     指定数据文件夹
#
# 真正的逻辑全在 start.py 里,这里只负责找一个可用的 Python 把它拉起来。
# 这样 Mac/Linux 和 Windows 共用同一份逻辑,不会两边跑偏。

set -e
cd "$(dirname "$0")"

# 找一个可用的 python3。不假设 pyenv / conda / homebrew 里的任何一种,
# 版本够不够由 start.py 自己判断并给出中文提示。
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo ""
    echo "  ✗ 没有找到 Python"
    echo ""
    echo "    请先安装 Python 3.10 或更高版本:"
    echo "        https://www.python.org/downloads/"
    echo ""
    echo "    Mac 上也可以用 Homebrew:  brew install python@3.12"
    echo ""
    exit 1
fi

exec "$PY" start.py "$@"
