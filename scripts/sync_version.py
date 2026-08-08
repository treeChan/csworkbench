#!/usr/bin/env python3
"""版本号单一源同步脚本。

唯一真源：仓库根目录 VERSION 文件（一行 semver，如 0.4.4）。全项目只有这一处版本号，
其余位置要么运行时读它、要么是它的推导值，不存在第二处人工维护的副本：

  * app/config.py 的 APP_VERSION      → 运行时直接读 VERSION（不设兜底，读不到即报错）
  * tauri.conf.json 的 version        → 已删字段，Tauri 构建时回退 Cargo.toml 的 version
  * Cargo.toml 的 [package] version   → 本脚本从 VERSION 同步（cargo 无法运行时读外部文件）
  * desktop/package.json 的 version   → 本脚本从 VERSION 同步（npm 元数据一致性）

所以真正需要「写」的只有 Cargo.toml 和 package.json 两处，本脚本只改这两个文件。

用法：
    python scripts/sync_version.py          # 用 VERSION 文件的值同步
    python scripts/sync_version.py 0.4.5    # 先写 VERSION 再同步（等价）

桌面端构建会自动触发（package.json 的 pretauri hook；CI 里也在构建前显式执行）。

版本号命名规则（主/次/修订 + 预览版 MMDDHHNNN）与发布流程见 docs/versioning.md。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"


def read_version() -> str:
    if len(sys.argv) > 1:
        ver = sys.argv[1].strip()
        if ver:
            VERSION_FILE.write_text(ver + "\n", encoding="utf-8")
            return ver
    ver = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not ver:
        raise SystemExit("VERSION 文件为空，无法同步")
    return ver


def sync_cargo_toml(version: str) -> None:
    path = ROOT / "desktop" / "src-tauri" / "Cargo.toml"
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(
        r'^version = "[^"]*"',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise SystemExit("未在 Cargo.toml 找到 [package] version，请检查格式")
    path.write_text(new, encoding="utf-8")


def sync_package_json(version: str) -> None:
    path = ROOT / "desktop" / "package.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = version
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    version = read_version()
    sync_cargo_toml(version)
    sync_package_json(version)
    print(f"✅ 版本号已同步为 {version}")
    print("   VERSION（唯一源）→ Cargo.toml + package.json")


if __name__ == "__main__":
    main()
