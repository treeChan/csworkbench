#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 GitHub Release 资产重建 Tauri updater 的 latest.json（消除并发竞态）。

背景（详见 docs/current-status.md「CI 并发坑」）：
tauri-action 的 includeUpdaterJson 在三平台并行矩阵下，每个平台 job 结束时都会
「下载现有 latest.json → 合并自己平台 → 上传覆盖」。读-改-写不是原子的，并发时
后写者会用基于旧版的合并结果覆盖先写者的条目 → 清单随机丢平台
（曾发生 preview 清单只剩 linux，Windows/Mac 预览用户检查更新即报
「platforms 里找不到 windows-x86_64-nsis」）。

本脚本由 build-desktop.yml 的 finalize job 调用：等三平台构建全部成功、安装包与
.sig 都已上传 release 后，**串行**只读这些资产重建完整 latest.json，彻底消除竞态。
（tauri-action 因此不再需要 includeUpdaterJson——每个平台只负责上传自己的安装包。）

产物命名规约（与 bundler 一致，见 workflow matrix 实际产物）：
  macOS   csworkbench_<arch>.app.tar.gz        → darwin-<arch>、darwin-<arch>-app
  Linux   csworkbench_<ver>_<arch>.AppImage    → linux-<triple>、linux-<triple>-appimage
          csworkbench_<ver>_<arch>.deb         → linux-<triple>-deb
  Windows csworkbench_<ver>_x64-setup.exe      → windows-x86_64、windows-x86_64-nsis
每个安装包必须带同名 .sig（minisign 对文件签名），其内容即该 platform 的 signature 字段。
.dmg 只是分发安装介质，不参与 updater，跳过。

环境变量：
  GITHUB_REPOSITORY  owner/repo（GitHub Actions 自带）
  INPUT_TAG          目标 release 的 tag（如 v0.4.6 / preview）
  INPUT_VERSION      版本号（缺省读仓库根 VERSION，CI 里两者一致）
  INPUT_NOTES_FILE   release notes markdown 路径（缺省 docs/release-notes.md；文件不存在则不写 notes 字段）
  GITHUB_TOKEN       GitHub API token（只读 release 资产列表，无需写权限）

用法：
  GITHUB_REPOSITORY=owner/repo INPUT_TAG=v0.4.6 GITHUB_TOKEN=... python3 scripts/build_updater_json.py
输出 JSON 到 stdout（workflow 里重定向成 latest.json 再上传）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"


def api_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "csworkbench-build-updater-json",
    })
    with urllib.request.urlopen(req) as r:
        return json.load(r)


# 安装包后缀 → (updater target 种类, darwin 架构名用哪段)
# updater 里 platform key 的形状由 tauri target triple + bundle variant 决定，
# 与 tauri-action 生成的结果保持一致（对照 v0.4.6 实际 latest.json 验证过）。

def _darwin_keys(arch: str) -> list[str]:
    # macOS .app.tar.gz：Tauri updater 用 .app 包（不是 dmg）。同文件同时挂
    # 裸 triple key 与带 -app 变体 key，供客户端按安装器类型回退。
    return [f"darwin-{arch}", f"darwin-{arch}-app"]


def _linux_keys(bundle: str) -> list[str]:
    # Linux x86_64 由 bundler 固定产出（runner 是 amd64）。AppImage 是 Tauri
    # 默认 target，同时挂裸 triple 与 -appimage；deb 单独一个 -deb key。
    if bundle == "appimage":
        return ["linux-x86_64", "linux-x86_64-appimage"]
    return ["linux-x86_64-deb"]  # deb


def _windows_keys() -> list[str]:
    # Windows NSIS 安装器：裸 triple 与 -nsis 变体都指向同一个 exe。
    return ["windows-x86_64", "windows-x86_64-nsis"]


# 文件名里出现的架构 token → Tauri triple 的 arch 段
_ARCH_TO_TRIPLE = {"aarch64": "aarch64", "arm64": "aarch64",
                   "amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64"}


def _classify(name: str):
    """识别 updater 安装包。返回 (keys, 目标架构 arch) 或 None（非 updater 资产）。"""
    m = re.search(r"(aarch64|arm64|amd64|x86_64|x64)", name)
    arch_token = m.group(1) if m else None
    if name.endswith(".app.tar.gz") and arch_token:
        return _darwin_keys(_ARCH_TO_TRIPLE[arch_token])
    if name.endswith(".AppImage"):
        return _linux_keys("appimage")
    if name.endswith(".deb"):
        return _linux_keys("deb")
    if name.endswith("-setup.exe"):
        return _windows_keys()
    return None  # dmg / sidecar 二进制 / .sig / updater 清单本身等


def _now_pub_date() -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    # 与 tauri-action 一致：RFC3339 毫秒 + Z
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY")
    tag = os.environ.get("INPUT_TAG")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not tag or not token:
        print("缺少环境变量：需要 GITHUB_REPOSITORY / INPUT_TAG / GITHUB_TOKEN", file=sys.stderr)
        return 2

    version = os.environ.get("INPUT_VERSION")
    if not version:
        version = (ROOT / "VERSION").read_text().strip()

    notes = None
    notes_file = Path(os.environ.get("INPUT_NOTES_FILE", ROOT / "docs" / "release-notes.md"))
    if notes_file.is_file():
        notes = notes_file.read_text().strip()

    release = api_get(f"{API}/repos/{repo}/releases/tags/{tag}", token)
    assets = release.get("assets", [])
    by_name = {a["name"]: a for a in assets}

    platforms: dict[str, dict] = {}
    used_bundles = set()
    for name, asset in sorted(by_name.items()):
        keys = _classify(name)
        if not keys:
            continue
        sig_name = name + ".sig"
        sig_asset = by_name.get(sig_name)
        if not sig_asset:
            print(f"WARN: {name} 缺少同名 {sig_name}，跳过（该平台 updater 不可用）", file=sys.stderr)
            continue
        # signature = minisign .sig 文件全文（base64），下载比对过与 tauri-action 一致
        url = asset["browser_download_url"]
        sig_req = urllib.request.Request(sig_asset["browser_download_url"],
                                         headers={"User-Agent": "csworkbench-build-updater-json"})
        with urllib.request.urlopen(sig_req) as r:
            signature = r.read().decode().strip()
        for k in keys:
            if k in platforms:
                continue  # 同 target 多 bundle 时（如 linux AppImage 重复挂），先到先得
            platforms[k] = {"url": url, "signature": signature}
            used_bundles.add(name)

    missing = [a["name"] for a in assets
               if _classify(a["name"]) and a["name"] not in used_bundles]
    if missing:
        print(f"WARN: 以下 updater 安装包因缺 .sig 未被收录: {missing}", file=sys.stderr)
    if not platforms:
        print(f"ERROR: release {tag} 里找不到任何带 .sig 的 updater 安装包", file=sys.stderr)
        return 1

    out = {"version": version, "pub_date": _now_pub_date(), "platforms": platforms}
    if notes is not None:
        out["notes"] = notes
    json.dump(out, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
