# Workbench · 研究驾驶舱

一个跑在自己电脑上的轻量科研管理工具，用来同时跟踪多个研究方向的目标、实验和结论。

数据全部存在**你自己电脑的一个 SQLite 文件**里，不上传任何服务器，也不需要联网账号。
每个人跑自己的一份，互不干扰。

---

## 快速开始

> **两种打开方式，任选其一：**
>
> 1. **🖥️ 桌面 App（推荐）** —— 像普通软件一样双击打开，不需要开浏览器，
>    也不用装 Python（运行时已一起打包）。
> 2. **🌐 浏览器版** —— 在项目文件夹里跑启动脚本，浏览器自动打开本地页面。

### 第一步：把代码弄到本地

**方式 A — 用 git（推荐，以后能一条命令更新）**

```bash
git clone https://github.com/treeChan/csworkbench.git workbench
cd workbench
```

**方式 B — 直接下载（不用装 git）**

点本页面右上方绿色的 **`Code`** 按钮 → **`Download ZIP`** → 解压到任意文件夹。

> 两种方式都能用。区别是：用 git 克隆的，以后作者更新了你跑一句 `git pull` 就同步；
> 下载 ZIP 的，想更新得重新下载一次（下载前记得先备份好自己的数据文件）。

### 第二步（方式一）：桌面 App

从项目的 **GitHub Releases** 页下载对应平台的安装包（Windows / macOS / Linux）：

1. 打开 [Releases](https://github.com/treeChan/csworkbench/releases)
2. 下载最新版对应平台的安装包（.msi / .dmg / .AppImage 等）
3. 安装后双击图标打开 —— 桌面窗口就是 Workbench，关窗即退出

> 桌面 App 把 Python 运行时一起打包，**不需要安装 Python 3.10+**。
> 数据存在系统用户数据目录，与浏览器版是两份独立数据：
> - macOS：`~/Library/Application Support/com.csworkbench.desktop/workbench.db`
> - Windows：`%APPDATA%\com.csworkbench.desktop\workbench.db`
> - Linux：`~/.local/share/com.csworkbench.desktop/workbench.db`
>
> 想迁移数据：把旧的 `.db` 文件拷到上述目录即可。
> ⚠️ 未签名版本首次运行时，macOS 需要右键 → 打开（Gatekeeper）。

### 第二步（方式二）：浏览器版

**Mac / Linux** —— 在项目文件夹里打开终端：

```bash
./run.sh
```

**Windows** —— **双击 `run.bat`** 就行。

---

首次启动会自动完成三件事，大概一两分钟：

1. 创建虚拟环境 `.venv`
2. 安装依赖
3. 问你数据想存在哪儿（直接回车就用默认的 `data/` 文件夹）

装好之后浏览器会自动打开 **http://127.0.0.1:8000**。
以后再启动就是几秒钟的事。

按 `Ctrl+C` 停止。

> **需要 Python 3.10 或更高版本。**
> 没装或者版本太旧的话，启动脚本会告诉你去哪儿下载，不会报一堆看不懂的错。
> 下载地址：https://www.python.org/downloads/ （Windows 安装时记得勾选 “Add Python to PATH”）

> **这是个完全跑在你自己电脑上的工具。** 不需要注册账号，装好之后不联网也能用，
> 你记录的任何内容都不会发送到任何服务器 —— 包括作者那里。

---

## 数据存在哪儿

默认存在项目里的 `data/workbench.db`。想换个地方：

```bash
./run.sh --data ~/Documents/科研数据        # Mac / Linux
run.bat --data D:/科研数据                   # Windows
```

指定一次就会记在 `.env` 里，以后不用重复输入。

**建议把它指向网盘的同步目录**（iCloud / OneDrive / 坚果云等），这样数据就自动备份了。
想手动备份的话，直接拷贝那个 `.db` 文件就是完整备份。

> ⚠️ 换电脑或者重装时，记得先把 `.db` 文件拷出来。删掉它 = 所有记录一起没。

---

## 怎么用

### 数据是怎么组织的

```
项目 (Project)              一个研究方向，比如「声学成像」
  └── 大目标 (Goal)          这个方向要啃下来的几块硬骨头
        └── 实验 (Experiment) 为了验证某件事做的一次具体尝试
              ├── 指标 (Metric)   训练过程中的数据点，比如 loss
              ├── 笔记 (Note)     Markdown 格式的观察和结论
              └── 结果小结         实验做完后单独填写

决策 (Decision)             跨项目的待办和已定事项，随手记
```

**实验必须挂在某个大目标下面** —— 这是刻意的设计。
强迫自己先想清楚「这个实验是为了推进哪个目标」，可以少做很多白工。

### 五阶段流水线

每个项目都有一个当前阶段，在项目详情页点一下就能切换：

```
目标定义 → 实验设计 → 实验执行 → 结果分析 → 总结输出
```

它不控制任何功能，纯粹是给自己一个「现在应该在干什么」的提示。

### 顶栏：搜索 · 快速创建 · 主题 · 设置

- **搜索**：顶栏搜索框回车或点「搜索」按钮均可搜索（`Ctrl K` 聚焦）。默认「快速搜索」
  只匹配项目/实验/大目标/文件/周复盘等的标题和名称，毫秒级返回；结果页右上角
  可切「✨ 全文搜索」，额外匹配实验设计、笔记、文件描述、周复盘正文等内容。
- **快速创建**：顶栏「+ 快速创建」下拉（`Ctrl N` 开关），可直接新建项目 /
  大目标 / 实验 / 周复盘。当前在某个项目页面里时，新建大目标和实验会自动带上那个项目。
- **主题**：右上角 🌙/☀️ 快捷切换亮色 / 暗色；设置页可改为「跟随系统」，
  随系统外观自动切换。偏好保存在本机浏览器。
- **设置**：右上角 ⚙️ 齿轮进入设置页（外观 / 数据与存储 / 常规 / 关于，见下）。

### 侧栏与状态栏

- **侧栏**：左侧窄栏从上到下是 今日（战情室）/ 项目 / 周复盘，图标 + 文字上下排列。
- **状态栏**：底部固定，左侧显示品牌 Workbench，中间是**数据库健康动态检测**
  （每 60s 跑一次 `PRAGMA integrity_check`，文件损坏或丢失会变红提示，防止误删数据而不自知），
  右侧是快捷键提示（`Ctrl K` 搜索 · `Ctrl N` 新建）。
  数据只存在本机 SQLite 文件里，不上传任何服务器。

### 设置页

| 区块 | 内容 |
|---|---|
| 外观 | 主题：跟随系统 / 亮色 / 暗色 三选一 |
| 数据与存储 | 数据库路径、上传文件目录、上传大小上限；「下载完整备份」一键导出全部数据 |
| 恢复备份 | 上传之前导出的备份 zip，一键恢复到当时状态 |
| 常规 | 每页显示条数 |
| 关于 | 应用名称、版本号、数据库路径、数据库大小、健康状态 |

**改数据库路径会自动迁移**：先检测目标目录读写权限（不可写则阻止）、复制并用
`PRAGMA integrity_check` + 表行数校验完整性、切换连接、成功后删除旧库；任一步失败
自动回滚原配置，绝不留下半迁移状态。改完立即生效，无需重启。

**完整备份与跨电脑迁移**：`下载完整备份` 打包成 zip（数据库 + 所有上传文件 +
配置，路径以相对形式记录，换机器也能正确落位）；另一台机器上传该 zip「恢复备份」
即可一键还原。恢复前自动备份当前数据，任一步失败自动回滚，绝不因恢复失败丢数据。

### 主要页面

| 页面 | 地址 | 用途 |
|---|---|---|
| 战情室 | `/` | 所有项目一屏看完，每张卡显示进度、阶段、待传结果数 |
| 项目列表 | `/projects` | 卡片式，可新建和删除 |
| 项目详情 | `/projects/{id}` | 大目标 + 实验表 + 决策日志 + 阶段切换 |
| 实验详情 | `/experiments/{id}` | 分「📐 设计」和「📊 结果」两块 |
| 上传结果 | `/experiments/{id}/results` | 实验做完后填结果小结、加指标记录（**不画曲线**）、上传图片 / .pt 权重、写笔记 |
| 周复盘 | `/review` | 每周一篇：高光、卡点、下周重点 |
| 搜索 | `/search?q=关键词` | 顶栏搜索框回车或点按钮进入，按 项目/实验与目标/文件/笔记与决策/周复盘 分组展示 |
| 设置 | `/settings` | 外观 / 数据与存储 / 常规 / 关于 |

文件跟随所属实验 / 项目保留在实验详情页和项目详情页，没有单独的「文件与成果」页面。

### 典型流程

1. 战情室 → 新建项目（比如「声学成像」）
2. 进项目 → 新建大目标（比如「2D 角谱反演验证」）
3. 在大目标下新建实验，填写设计（假设、配置、方法）
4. 实验跑完 → 点「📤 上传结果」，填结果小结、指标、笔记

---

## 从训练脚本自动推送数据

不想手动录入的话，可以让训练脚本直接把指标推过来。
`scripts/example_log.py` 是一个可以直接运行的完整示例：

```bash
python scripts/example_log.py
```

在自己的训练脚本里这样用：

```python
from scripts.example_log import ensure_project, ensure_goal, create_experiment, log_metric

proj = ensure_project("声学成像")
goal = ensure_goal(proj["id"], "2D 角谱反演验证")
exp  = create_experiment(proj["id"], goal_id=goal["id"], name="v1",
                         hypothesis="...")

for step, loss in enumerate(losses):
    log_metric(exp["id"], "loss", loss, note=f"epoch {step}", step=step)
```

注意：

- `create_experiment` 的 `goal_id` 是**必填**的，因为实验必须挂在大目标下面。
- `log_metric` 的 `note` 是这一行的上下文说明（"这一轮改了 lr" / "用的是验证集" 之类）。
  指标记录**不画曲线**，只纯记录。
- 旧版用过的 `config={"lr": 0.001}` 已经废弃 —— 超参现在是 Markdown 文字
  `config_md`，手记即可，训练脚本不需要管。

### 直接调 HTTP API

完整的接口文档在 **http://127.0.0.1:8000/docs**（应用启动后可访问）。

```bash
# 1. 建项目
curl -X POST http://127.0.0.1:8000/api/projects \
     -H 'Content-Type: application/json' \
     -d '{"name": "声学成像"}'

# 2. 建大目标（拿上一步返回的 project id）
curl -X POST http://127.0.0.1:8000/api/projects/1/goals \
     -H 'Content-Type: application/json' \
     -d '{"name": "2D 角谱反演验证"}'

# 3. 建实验（goal_id 必填；config_md 是 Markdown 文字）
curl -X POST http://127.0.0.1:8000/api/projects/1/experiments \
     -H 'Content-Type: application/json' \
     -d '{"name": "exp-001", "goal_id": 1, "config_md": "- lr: 0.001\n- batch_size: 32"}'

# 4. 记录指标（带备注）
curl -X POST http://127.0.0.1:8000/api/experiments/1/metrics \
     -H 'Content-Type: application/json' \
     -d '{"key": "loss", "value": 0.123, "note": "epoch 1"}'

# 5. 上传文件（图片 / .pt 都行；归属三选一）
curl -X POST http://127.0.0.1:8000/api/experiments/1/artifacts \
     -F 'file=@/path/to/best.pt' \
     -F 'description=最终模型权重'
```

Windows 的 CMD / PowerShell 不认上面的反斜杠续行和单引号，
建议直接用 `/docs` 页面点着调试，或者用上面的 Python 写法。

### 导出实验

实验详情页和上传结果页右上角都有 **📦 导出** 按钮，点了就把这个实验打包成 ZIP 下载：

```
experiment.md      # 整篇可读的 Markdown(简介+假设+配置+笔记+结果)
metrics.csv        # 所有指标（带 UTF-8 BOM,Excel 直接打开不乱码）
notes/             # 多条笔记各一篇 Markdown；只有一条就 notes.md
artifacts/         # 图片、.pt 等全部附件原样打包
```

API 端点：`GET /api/experiments/{id}/export`

---

## 技术栈

| 层 | 选择 |
|---|---|
| 后端 | FastAPI + Starlette |
| 存储 | SQLite（SQLAlchemy 2.0 ORM） |
| 校验 | Pydantic v2 |
| 前端 | Jinja2 模板 + 自定义 CSS（紫色主题，不依赖任何 UI 框架） |
| 图表 | 服务端渲染的内联 SVG |
| Markdown | python-markdown |
| 桌面壳 | Tauri v2（`desktop/`，可选：把浏览器版包成桌面 App） |

没有前端构建步骤，改完模板或 CSS 刷新页面就生效。

> 前端资源（含 HTMX）均已本地化到 `app/static/`，页面不依赖任何外部 CDN，
> 内网 / 离线环境下也能完整加载。

## 项目结构

```
workbench/
├── start.py                # 一键启动逻辑（跨平台，全在这里）
├── run.sh                  # Mac/Linux 入口，转发给 start.py
├── run.bat                 # Windows 入口，转发给 start.py
├── app/
│   ├── main.py             # FastAPI 入口
│   ├── config.py           # 配置（环境变量 / .env）
│   ├── database.py         # 引擎 + Session
│   ├── models.py           # SQLAlchemy ORM
│   ├── schemas.py          # Pydantic schemas
│   ├── crud.py             # 数据库操作
│   ├── routes/
│   │   ├── pages.py        # HTML 页面路由
│   │   └── api.py          # JSON API
│   ├── templates/          # Jinja2 模板
│   └── static/             # CSS / JS
├── data/                   # 数据库默认放这儿（已被 git 忽略）
├── scripts/
│   └── example_log.py      # 从训练脚本推送数据的示例
├── desktop/                # 桌面 App（Tauri v2 套壳 + PyInstaller sidecar）
│   ├── sidecar/            # Python 服务 → PyInstaller 打包配置
│   └── src-tauri/          # Tauri 壳源码
├── requirements.txt
└── .env.example
```

---

## 给想改代码的人

```bash
./run.sh --dev
```

### 桌面 App（desktop/）

桌面 App = Tauri v2 壳 + PyInstaller 打包的 sidecar（FastAPI 服务）。构建与运维细节见 `desktop/README.md`。

| 事项 | 说明 |
|---|---|
| 运行依赖 | **无** —— Python 运行时已打包进安装包 |
| 构建平台 | GitHub Actions 矩阵（macOS / Windows / Linux），打 `v*` tag 自动出安装包 |
| 构建依赖 | Rust、Node.js、Python 3.11+、PyInstaller（CI 已自动处理） |

`--dev` 会开启 uvicorn 的自动重载，改完 `.py` 文件不用手动重启。
（不加这个参数时不会重载，改了 Python 代码需要 `Ctrl+C` 后重新启动。
模板和 CSS 任何时候都是改完刷新就生效。）

其他有用的参数：

```bash
./run.sh --port 9000       # 换端口（默认 8000，被占用时会自动往后找）
./run.sh --no-browser      # 启动后不自动开浏览器
```

### 已知的待办

- `app/static/charts.js` 目前没有任何模板引用它（指标走的是表格，不再画曲线）
- `base.html` 加载了本地化的 HTMX（`app/static/htmx.min.js`，不依赖外部 CDN），但代码里还没有用到任何 `hx-*` 属性
- `WORKBENCH_DEBUG` 配置项已声明但尚未接线
- 桌面 App 三平台安装包（v0.3.0）已由 GitHub Actions 构建并上传到 Release（draft 待发布）

## 排查问题

**启动时说找不到 Python / 版本太低**
装 Python 3.10+，Windows 记得勾 “Add Python to PATH”，装完重开终端。

**依赖装不上**
多半是连不上 PyPI。国内可以用镜像源：

```bash
.venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Windows 上是 `.venv\Scripts\pip`。装完再跑启动脚本。

**想推倒重来**
删掉 `.venv` 文件夹再启动即可，会重新装一遍。
**注意不要删 `.db` 文件** —— 那是你所有的数据。

**想看数据库里到底存了什么**

```bash
sqlite3 data/workbench.db ".tables"
```
