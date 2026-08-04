# Workbench · 研究驾驶舱

一个跑在自己电脑上的轻量科研管理工具，用来同时跟踪多个研究方向的目标、实验和结论。

数据全部存在**你自己电脑的一个 SQLite 文件**里，不上传任何服务器，也不需要联网账号。
每个人跑自己的一份，互不干扰。

---

## 快速开始

### Mac / Linux

```bash
git clone <仓库地址> workbench
cd workbench
./run.sh
```

### Windows

```
git clone <仓库地址> workbench
cd workbench
```

然后**双击 `run.bat`**（或在命令行里执行 `run.bat`）。

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

### 主要页面

| 页面 | 地址 | 用途 |
|---|---|---|
| 战情室 | `/` | 所有项目一屏看完，每张卡显示进度、阶段、待传结果数 |
| 项目列表 | `/projects` | 卡片式，可新建和删除 |
| 项目详情 | `/projects/{id}` | 大目标 + 实验表 + 决策日志 + 阶段切换 |
| 实验详情 | `/experiments/{id}` | 分「📐 设计」和「📊 结果」两块 |
| 上传结果 | `/experiments/{id}/results` | 实验做完后单独填结果、指标、笔记 |

`/cases`、`/files`、`/review`、`/settings` 目前还是占位页，没有实现。

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
                         config={"lr": 0.001}, hypothesis="...")

for step, loss in enumerate(losses):
    log_metric(exp["id"], "loss", loss, step=step)
```

注意 `create_experiment` 的 `goal_id` 是**必填**的，因为实验必须挂在大目标下面。

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

# 3. 建实验（goal_id 必填）
curl -X POST http://127.0.0.1:8000/api/projects/1/experiments \
     -H 'Content-Type: application/json' \
     -d '{"name": "exp-001", "goal_id": 1, "config": {"lr": 0.001}}'

# 4. 记录指标
curl -X POST http://127.0.0.1:8000/api/experiments/1/metrics \
     -H 'Content-Type: application/json' \
     -d '{"key": "loss", "value": 0.123, "step": 0}'
```

Windows 的 CMD / PowerShell 不认上面的反斜杠续行和单引号，
建议直接用 `/docs` 页面点着调试，或者用上面的 Python 写法。

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

没有前端构建步骤，改完模板或 CSS 刷新页面就生效。

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
├── requirements.txt
└── .env.example
```

---

## 给想改代码的人

```bash
./run.sh --dev
```

`--dev` 会开启 uvicorn 的自动重载，改完 `.py` 文件不用手动重启。
（不加这个参数时不会重载，改了 Python 代码需要 `Ctrl+C` 后重新启动。
模板和 CSS 任何时候都是改完刷新就生效。）

其他有用的参数：

```bash
./run.sh --port 9000       # 换端口（默认 8000，被占用时会自动往后找）
./run.sh --no-browser      # 启动后不自动开浏览器
```

### 已知的待办

- `/cases`、`/files`、`/review`、`/settings` 四个页面还是占位符
- `app/static/charts.js` 目前没有任何模板引用它（图表走的是内联 SVG）
- `base.html` 加载了 HTMX，但代码里还没有用到任何 `hx-*` 属性
- `WORKBENCH_DEBUG` 和 `WORKBENCH_PAGE_SIZE` 两个配置项已声明但尚未接线

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
