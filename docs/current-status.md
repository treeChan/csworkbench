# 当前工作状态

> 维护者下次接手时先读这份。本文档是「待办 + 决策 + 上下文」备忘录，不是用户文档。

## 最近一次发布

**v0.4.6-preview.8241708** (`<本次 bump commit>`) — 思维导图布局修复 + CI read-notes 加固 + 版本 bump.

发布相关:
- 版本号: `0.4.6-preview.8241708` (本地时间 8月24日 17:08, MMDDHHMM 月去前导零)
- 版本文件已同步: VERSION + `desktop/package.json` + `desktop/src-tauri/Cargo.toml` (sync_version.py)。
- **preview tag**: 本次 commit 提交并 push main 后, force push `preview` tag 到此 commit 触发 CI (force push 前 AskUserQuestion 确认)。
- **上一版 (`4eea59c`) 的 CI 曾失败**: 该 commit 的 `docs/release-notes.md` 末尾缺换行, read-notes 步骤的 heredoc 解析失败 (`Matching delimiter not found 'EOF'`)。本次已修复: release-notes 末尾补换行 + workflow 该步骤加固 (自动补换行 + 用长串分隔符)。
- **⚠️ 版本号格式坑 (务必遵守 docs/versioning.md)**: 8月预览版必须用 `824XXXX` (**月去前导零**)。`0824XXXX` 带前导零会被 Cargo 拒绝 (`invalid leading zero in pre-release identifier`)——上游 `08241339`/`08241522` 两个版本号都违反此规则, 从未构建成功过。本次先用错误的 `08241708` 提交后也被 Cargo 拒, 已 amend 为正确的 `8241708`。
- Release notes: `docs/release-notes.md` (整体覆盖, 本次增量: 思维导图布局修复 + 构建加固)
- 完整发布流程见 `docs/versioning.md` + `desktop/README.md` 第 82-96 行

CI 构建完成后, 维护者需要:
1. 去 GitHub Releases 把 `preview` 的 draft 转正式 (保留 `prerelease` 标记)
2. 把 `docs/release-notes.md` 内容粘进 GitHub Release body (updater 弹窗从这里读)
3. 如发现 bug, 用预览版号模式再 bump 一次 + 更新 release notes

## main HEAD 相对于 preview 的差

**main HEAD = 本次 bump commit (8241708)**, 比 preview tag (`4eea59c`) 多了 **2 个 commit**:

| commit | 内容 |
|---|---|
| `<本次>` | **chore**: bump 预览版 8241708 (含 CI read-notes 加固 + release-notes/current-status 更新) |
| `2f6740e` | **fix**: 思维导图编辑页布局修复 (左栏固定 84px / 工具栏带文字按钮自适应 / 恢复被 `{% block scripts %}` 清空的全局脚本 / 画布高度改填满网格; STYLE_VERSION → 20260824a) |

**本次动作**: push main 后, 把 preview tag force push 到本次 commit (新 sha) 触发 CI, 发布 8241708。

## 下一步具体动作 (接手者从这里开始)

1. **跑 PyInstaller 冒烟** (高优先级, 发版前必跑, 见 [[workbench-gotchas]]):
   ```bash
   cd ~/workbench/desktop/sidecar
   .venv/bin/python -m PyInstaller --clean --noconfirm workbench-server.spec
   ```
   确认无报错 + 二进制能起来。**从 08241339 到现在 (含 Plotly 图表 + 布局修复) 一直没跑过, 正式发 v0.4.6 前必须跑一次。**

2. **同步 preview tag → 触发 CI** (本次发布动作):
   把 preview tag force push 到本次 bump commit (新 sha, GitHub 对同 sha 重 push 不触发新构建):
   ```bash
   cd ~/workbench
   git fetch --tags --force origin
   git push origin HEAD:refs/tags/preview --force   # force push, AskUserQuestion 先确认
   ```
   推完去 GitHub Releases 把 draft 转正式 + 贴 release notes。

3. **CI 触发后**: 确认三平台构建通过; 通过的 Release 去 GitHub Releases 把 `preview` draft 转正式 (保留 prerelease 标记), body 贴最新 release notes。

4. **思维导图连线交互**: `.claude/plans/dynamic-scribbling-hamming.md` 文件**已丢失**, 需要重新整理 6 个备选方案给用户挑。

## 最近一轮已完成的功能 (commit 历史)

按时间倒序:

| commit | 内容 | 备注 |
|---|---|---|
| `<本次>` | **chore**: bump 预览版 8241708 | 思维导图布局修复 + CI read-notes 加固 |
| `2f6740e` | **fix**: 思维导图编辑页布局修复 | 左栏固定 84px / 工具栏文字按钮自适应 / 恢复全局脚本 / 画布高度修正 |
| `4eea59c` | chore: bump 预览版 08241522 | 上游: 图表逐条勾选 + 删除跳转 + key:value 解析; ⚠️ 该版 CI 因 release-notes 缺换行失败 |
| `458910c` | chore: bump 预览版 08241339 | 上游: Plotly 图表区 + 批量粘贴 + UX 改进 |
| `7ca1abf` | **feat**: 项目「当前研究阶段」加数据佐证条 (方案 A) | 手动标签 + 统计依据 |
| `07b23dd` | **fix**: 项目编辑改走更新路由 + 删除收敛到侧滑/菜单 + 二次确认 UX | 编辑表单 action 按模式路由 |
| `96bcf05` | docs: 记录当前工作进展 (8121522 发布状态 + 待办清单) | 维护性 commit |
| `cb91b45` | chore: bump 预览版 8121522 | 旧 preview tag (远端已滚到 `4eea59c`) |
| `cd55eda` | **C4**: 拖拽改子目录 + 子目录改名 | API: `/api/artifacts/{id}/move` + `/api/folders/rename` |
| `ea1230e` | **C3**: 项目级自定义类别 + 类别下子目录 | `Project.categories_json` 字段 + 项目设置页 UI |
| `9f814b7` | **C2**: 文件树 + 缩略图网格 + 灯箱 | `build_artifact_tree` 在 Python 端构建, Jinja 只渲染 |
| `6bdbbae` | docs: 正式版发布时同步 preview tag | 维护性 commit |
| `6261cb6` | **C1**: 创建实验默认选中当前目标 | URL `?goal_id=` |
| `46bfc20` | v0.4.5 正式版 | 上一个 stable |

C1-C4 围绕「文件与成果」栏目展开, 用户反馈驱动:
- C1 是用户提的「创建实验默认选第一个目标」bug
- C2-C4 是用户提的「图片结果比较乱, 想分类 / 子目录可折叠 / 可拖拽 / 可改名」
- `07b23dd` 是用户提的「编辑项目老误建 / 删除按钮太显眼」bug
- 思维导图布局修复是用户提的「导图页左栏宽度漂移 / 按钮文字截断 / 全局功能失效」bug

## 待办 / 搁置中的事

### 1. 桌面端 PyInstaller 冒烟 (优先级: 🔴 高, 每次发版前必跑)

`desktop/README.md:181-183` 提到的检查清单:
```bash
cd ~/workbench/desktop/sidecar
.venv/bin/python -m PyInstaller --clean --noconfirm workbench-server.spec
```
本轮没跑过, 直接打 tag 推到 CI 了. **正式发 v0.4.6-preview.08240937 之前必须跑一次**, 不然桌面端更新了用户装上起不来就麻烦.

### 2. 思维导图连线交互方式选择 (优先级: 🟡 中)

~~文件: `.claude/plans/dynamic-scribbling-hamming.md`~~ — **plan 文件已丢失** (2026-08-24 接手时发现)。

背景: 上一轮改了「选中节点后点另一个节点自动连边」用户嫌太激进. 当时准备了 6 个备选方案 (A 锚点拖拽 / B Alt+点击 / C 双击准备态 / D 显式按钮 / E 悬停+按钮 / F Ctrl+点击). 用户没选, 现在 plan 文件也没了.

**下次接手**: 需要重新整理 6 个备选方案给用户挑, 再动手实现. 不要凭印象直接挑一个做.

### 3. C4 的拖拽手势 / 交互细节打磨 (优先级: 🟢 低)

- 拖拽时缩略图半透明, 但没有拖拽预览 (cursor 跟手)
- 折叠的 folder 也能接收拖拽, 但拖到 summary 边框附近才高亮, 中间一大片没反应
- 改名 input 验证: 空 / 重名 / 与现有 folder 重合的检查只在服务端, 客户端不预检

### 4. tags / labels 体系重构 (优先级: 🟢 低, 之前讨论过)

项目标签 (现在 `Project.tags`, 逗号分隔字符串) 太简陋, 想做成结构化 tag 表. 没动.

### 5. 国际化 / 英文版 (优先级: 🟢 低)

整个 UI 都是中文, 没考虑过 i18n. 提到过, 没动.

## 后台清理

- 已清掉端口 8000-8019 上的 20 个残留 uvicorn 进程 (2026-08-12)
- 当前进程 (2026-08-24 09:37 实测):
  - `uvicorn app.main:app` PID **10760** (在 8000 端口监听)
  - `start.py` PID **10653** (估计是它拉起的 uvicorn, 自己也跑着)
  - `curl http://127.0.0.1:8000/` → HTTP 200, 15ms

## 本地开发状态

- **服务在跑**, 端口 8000。`./run.sh --dev` / `./run.sh` 都能再开一份, 注意端口别冲突。
- **日志位置**: uvicorn 进程的 stdout 当前**没有重定向到文件** (lsof 看 fd 1/2 没指向任何 /tmp/wb*.log, 估计是 start.py 进程继承了它的 stdout)。
  - 临时看输出: `lsof -p 10760 | grep -E ' (1u|2u) '` 找 TTY, 或直接 `curl :8000` 验活。
  - 重启并写日志: `kill 10760; nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/wb.log 2>&1 &`
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
- `.claude/plans/dynamic-scribbling-hamming.md` — ~~思维导图连线交互方案~~ **文件已丢失, 待重写**
