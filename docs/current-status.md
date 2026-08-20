# 当前工作状态

> 维护者下次接手时先读这份。本文档是「待办 + 决策 + 上下文」备忘录，不是用户文档。

## 最近一次发布

**v0.4.6-preview.8121522** (`cb91b45`) — preview tag 推到了这个 commit, CI 已触发三平台构建.

发布相关:
- 版本号: `0.4.6-preview.8121522` (本地时间 8月12日 15:22, MMDDHHMM 月去前导零)
- preview tag 指向: `cb91b45df0279a51dbd03aa8aab84e290d5bfe8e`
- main HEAD: `cb91b45` (覆盖了先前错版本号的 `1a96e07`)
- Release notes: `docs/release-notes.md` (4 节: C1/C2/C3/C4 + 体验优化)
- 完整发布流程见 `docs/versioning.md` + `desktop/README.md` 第 82-96 行

CI 构建完成后, 维护者需要:
1. 去 GitHub Releases 把 `preview` 的 draft 转正式 (保留 `prerelease` 标记)
2. 把 `docs/release-notes.md` 内容粘进 GitHub Release body (updater 弹窗从这里读)
3. 如发现 bug, 用预览版号模式再 bump 一次 + 更新 release notes

## 最近一轮已完成的功能 (commit 历史)

按时间倒序:

| commit | 内容 | 备注 |
|---|---|---|
| `cb91b45` | chore: bump 预览版 8121522 | preview tag |
| `cd55eda` | **C4**: 拖拽改子目录 + 子目录改名 | API: `/api/artifacts/{id}/move` + `/api/folders/rename` |
| `ea1230e` | **C3**: 项目级自定义类别 + 类别下子目录 | `Project.categories_json` 字段 + 项目设置页 UI |
| `9f814b7` | **C2**: 文件树 + 缩略图网格 + 灯箱 | `build_artifact_tree` 在 Python 端构建, Jinja 只渲染 |
| `6bdbbae` | docs: 正式版发布时同步 preview tag | 维护性 commit |
| `6261cb6` | **C1**: 创建实验默认选中当前目标 | URL `?goal_id=` |
| `46bfc20` | v0.4.5 正式版 | 上一个 stable |

C1-C4 围绕「文件与成果」栏目展开, 用户反馈驱动:
- C1 是用户提的「创建实验默认选第一个目标」bug
- C2-C4 是用户提的「图片结果比较乱, 想分类 / 子目录可折叠 / 可拖拽 / 可改名」

## 待办 / 搁置中的事

### 1. 思维导图连线交互方式选择 (优先级: 中)

文件: `.claude/plans/dynamic-scribbling-hamming.md`

背景: 上一轮改了「选中节点后点另一个节点自动连边」用户嫌太激进. 准备了 6 个备选方案 (A 锚点拖拽 / B Alt+点击 / C 双击准备态 / D 显式按钮 / E 悬停+按钮 / F Ctrl+点击). 用户没选, plan 文件还在.

下次先问用户挑哪个再动手.

### 2. 桌面端 PyInstaller 冒烟 (优先级: 高, 每次发版前必跑)

`desktop/README.md:181-183` 提到的检查清单:
```bash
cd desktop/sidecar
.venv/bin/python -m PyInstaller --clean --noconfirm workbench-server.spec
```
本轮没跑过, 直接打 tag 推到 CI 了. 下次正式发 v0.4.6 之前必须跑一次.

### 3. C4 的拖拽手势 / 交互细节打磨 (优先级: 低)

- 拖拽时缩略图半透明, 但没有拖拽预览 (cursor 跟手)
- 折叠的 folder 也能接收拖拽, 但拖到 summary 边框附近才高亮, 中间一大片没反应
- 改名 input 验证: 空 / 重名 / 与现有 folder 重合的检查只在服务端, 客户端不预检

### 4. tags / labels 体系重构 (优先级: 低, 之前讨论过)

项目标签 (现在 `Project.tags`, 逗号分隔字符串) 太简陋, 想做成结构化 tag 表. 没动.

### 5. 国际化 / 英文版 (优先级: 低)

整个 UI 都是中文, 没考虑过 i18n. 提到过, 没动.

## 后台清理

- 已清掉端口 8000-8019 上的 20 个残留 uvicorn 进程 (2026-08-12)
- 当前没有遗留后台 workbench

## 本地开发状态

- 服务跑在哪个端口? **没跑**. 上次 `kill -9` 清完后没重启. 下次想本地看效果直接:
  ```bash
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/wb.log 2>&1 &
  ```
- 数据库: `data/workbench.db` (test 数据混在里面, 如要干净快照 `cp data/workbench.db /tmp/before.db`)

## 重要约束 (用户偏好, 不要违反)

- **强制推送 (`--force-with-lease`) 会触发自动拦截弹窗**, 用 AskUserQuestion 先确认
- 发布预览版必须写 release notes (用户最看重, 漏了会被打回)
- 版本号格式 `x.y.z-preview.MMDDHHMM`, **月去前导零、日时分补零**
- preview tag 固定叫 `preview`, 每次 force push 滚动
- commit message 中文, 简短描述 + 在 body 里写「详见 docs/release-notes.md」

## 相关文档索引

- `docs/versioning.md` — 版本号规范 + 发布流程 (强制)
- `docs/release-notes.md` — 最新 release notes (本轮预览版)
- `docs/troubleshooting.md` — 历史踩坑 (NSIS / ACL / 单实例 / semver 限制)
- `desktop/README.md` 第 75-117 行 — 桌面端发布 + updater 双轨
- `.claude/plans/dynamic-scribbling-hamming.md` — 思维导图连线交互方案 (待决策)