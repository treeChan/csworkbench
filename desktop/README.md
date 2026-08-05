# Workbench 桌面 App（Tauri v2 套壳）

把浏览器版 Workbench（FastAPI + Jinja2 + SQLite）包装成桌面软件：**双击图标即用，
关窗即退出，不需要手动开浏览器，也不需要安装 Python**（运行时已打包进安装包）。

## 目录结构

```
desktop/
├── package.json                  # @tauri-apps/cli（构建工具）
├── icon-source.png               # 图标源图（改图标后重新生成 icons/）
├── make_icon.py                  # 图标生成脚本（Pillow）
├── ui/
│   └── index.html                # 启动占位页（"正在启动本地服务…"）
├── sidecar/
│   ├── entry.py                  # sidecar 入口（FastAPI 服务，见下"契约"）
│   ├── workbench-server.spec     # PyInstaller 打包配置
│   └── requirements-desktop.txt  # sidecar 依赖（刻意无 uvicorn[standard]）
└── src-tauri/
    ├── Cargo.toml                # Rust 依赖
    ├── tauri.conf.json           # 窗口 / bundle / externalBin 配置
    ├── capabilities/default.json # sidecar 启动权限
    ├── icons/                    # 各平台图标集（由 icon-source.png 生成）
    └── src/
        ├── main.rs               # 入口
        └── lib.rs                # 拉起 sidecar → 读端口 → 导航 → 退出清理
```

## 工作原理

```
Tauri 窗口（先显示 ui/index.html 占位页）
   │  ① setup() 用 shell 插件拉起 sidecar：binaries/workbench-server
   │  ② 异步读 sidecar 的 stdout，等 {"status":"ready","port":N} 握手行
   │  ③ 拿到端口后 navigate 到 http://127.0.0.1:PORT/
   ▼
PyInstaller onefile sidecar（FastAPI/uvicorn/h11，纯 Python）
   └── 设置 WORKBENCH_DB_PATH → 用户数据目录 → 绑定 127.0.0.1 → 起 uvicorn
```

退出清理三保险：① Rust 在 `RunEvent::Exit` 时 `kill()` 子进程；② sidecar 读
stdin 到 EOF 就自杀（宿主退出 → 管道关闭）；③ 单实例插件防止同时开两个实例
（避免 SQLite 写冲突）。

## sidecar 契约

入口 `sidecar/entry.py`，由 Tauri 壳以子进程方式启动：

| 项 | 约定 |
|---|---|
| 参数 | `--db <workbench.db 绝对路径>` `--port <首选端口，默认 8750>` `--port-file <握手文件>` `--artifacts <上传文件根目录>` |
| stdout | 就绪后只输出一行 `{"status":"ready","port":N}`（uvicorn 日志走 stderr） |
| stdin | 读到 EOF 即退出（宿主导出兜底） |
| 数据路径 | 通过环境变量 `WORKBENCH_DB_PATH` 传给 FastAPI；上传文件目录走 `WORKBENCH_ARTIFACT_DIR`（都在 `import app` 之前设置，必须指向持久化目录——PyInstaller 的 BASE_DIR 是临时解压目录） |

手工冒烟（不需要 Rust 环境）：

```bash
python desktop/sidecar/entry.py --db /tmp/wb.db --port 0
# 另开终端:
curl 127.0.0.1:<打印的端口>/health
```

## 构建

### 方式一：GitHub Actions（推荐，三平台）

1. 在仓库根打 tag 并推送：`git tag v0.1.0 && git push origin v0.1.0`
2. `.github/workflows/build-desktop.yml` 的矩阵会在 macOS / Windows / Linux
   三个 runner 上各自构建 sidecar + 安装包
3. 产物出现在 **draft Release** 和 workflow artifacts 里

### 方式二：本地构建（以 Linux 为例）

前置依赖：Rust（`rustup`）、Node.js、Python 3.11+、Tauri 系统库（Ubuntu）：

```bash
sudo apt install -y libwebkit2gtk-4.1-dev build-essential libxdo-dev \
  libssl-dev libayatana-appindicator3-dev librsvg2-dev libsoup-3.0-dev patchelf

cd desktop
npm install                        # 装 @tauri-apps/cli

# 1) 打包 Python sidecar
cd sidecar
python3 -m venv .venv && .venv/bin/pip install -r requirements-desktop.txt pyinstaller
.venv/bin/python -m PyInstaller --clean --noconfirm workbench-server.spec
TRIPLE=$(rustc -vV | grep '^host:' | awk '{print $2}')
mkdir -p ../src-tauri/binaries
cp dist/workbench-server "../src-tauri/binaries/workbench-server-$TRIPLE"

# 2) 构建桌面 App
cd ..
npx tauri build                   # 产物在 src-tauri/target/release/bundle/
```

开发态（改了 Python 代码或 Rust 代码想快速看）：

```bash
python -m uvicorn app.main:app --port 8000   # 从仓库根起后端
cd desktop && npx tauri dev                  # 窗口直连 devUrl 8000
```

## 新功能发布流程（网页版开发 → 桌面版分发）

网页版和桌面版共用同一份 `app/` 后端代码：桌面版只是用 PyInstaller 把同一个
FastAPI 应用打包成 sidecar，再套一层 Tauri 壳，窗口最终 navigate 到
`http://127.0.0.1:PORT/`。所以没有两套功能要维护，发版节奏是：

1. **网页版开发调试**：`python start.py --dev`（热重载 + 看日志，成本最低）
2. **新功能开发完** → 过一遍下面的**打包检查清单**
3. **打 tag 推送** → CI 自动在 macOS / Windows / Linux 三平台出安装包
4. **到 GitHub Releases 发布 draft Release** → 用户下载安装

### 打包检查清单（每次发版前逐项过）

网页版 `python start.py` 正常不代表桌面版就绪——PyInstaller 是**静态收集**，
漏掉的东西网页版看不出来，只会在安装包里缺。发版前检查：

- [ ] **版本号对齐**：`src-tauri/tauri.conf.json` 与 `src-tauri/Cargo.toml`
      的 `version` 改成新版本，再打同名 tag（CI 用 `github.ref_name`
      命名 Release，建议 tag 与 version 一致）
- [ ] **新增第三方依赖** → 加进 `sidecar/requirements-desktop.txt`，并确认
      `workbench-server.spec` 的 `collect_all` / `collect_submodules` 列表
      覆盖到了（没覆盖就补）
- [ ] **新增静态资源 / 模板目录** → 确认 `workbench-server.spec` 的
      `--add-data` 覆盖到（目前只打了 `app/templates` 和 `app/static`）
- [ ] **本地 PyInstaller 冒烟**（几十秒，打 tag 前必跑）：
      ```bash
      cd desktop/sidecar
      .venv/bin/python -m PyInstaller --clean --noconfirm workbench-server.spec
      ./dist/workbench-server --db /tmp/wb.db --port 0
      ```
      另开终端验证 `/health` 返回 200、`/` 首页能渲染；涉及上传 / 模板渲染的
      新功能，冒烟里顺手点一遍新页面，确认路径没被 PyInstaller 解压目录搞坏。

## 数据目录

Rust 侧用 `app.path().app_data_dir()` 决定，数据自动创建：

| 平台 | 路径 |
|---|---|
| macOS | `~/Library/Application Support/com.csworkbench.desktop/` |
| Windows | `%APPDATA%\com.csworkbench.desktop\` |
| Linux | `$XDG_DATA_HOME` 或 `~/.local/share/com.csworkbench.desktop/` |

数据库文件固定为 `workbench.db`。跟浏览器版是两份独立数据；想迁移直接拷 `.db` 文件。

## 已知限制 / 注意事项

- **macOS Gatekeeper**：未签名构建首次运行需右键 → 打开；正式分发需配 `APPLE_*`
  secrets 走签名 + 公证。
- **Windows Defender**：PyInstaller 单文件二进制偶尔被误报，用代码签名证书缓解。
- **图标**：想换图标改 `icon-source.png`，重新跑 `npx tauri icon ./icon-source.png`
  生成 `src-tauri/icons/` 全套。
- **端口**：固定从 8750 起向后探测空闲端口，无冲突。
