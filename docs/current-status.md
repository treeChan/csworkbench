# 当前工作状态

> 维护者下次接手时先读这份。本文档是「待办 + 决策 + 上下文」备忘录，不是用户文档。

## 最近一次发布

**v0.4.6** (`62ce938`) — 桌面端冷启动优化 + 实验图表（Plotly）+ 思维导图编辑全面修复 + 项目页 UX 改进 + 文件与成果栏目重构（C1–C4）+ CI 流程加固。**已于 2026-09-06 发布完成**。

发布相关（已完成动作）:
- 版本号: `0.4.6`（从 `0.4.6-preview.8241708` 正式化）
- 版本文件已同步: `VERSION` + `desktop/package.json` + `desktop/src-tauri/Cargo.toml`（`sync_version.py`）
- **正式 tag** `v0.4.6`（在 `62ce938` 上）已推送并构建；**preview tag** 已 force-push 同步到 `62ce938`（预览用户自动升级到正式版）。两个 GitHub Release 均已转正式：`v0.4.6` 取消 prerelease、`preview` 保留 prerelease 标记、body 已贴 release notes。
- ⚠️ **v0.4.6 发布后发现 updater 清单并发坑**（详见下方「重要约束」与 build-desktop.yml finalize 注释）：
  preview 的 `latest.json` 被三平台并行覆盖坏成**只剩 linux**，Windows/Mac 预览端检查更新报 `fallback platforms` 找不到。已手动用 v0.4.6 完整清单覆盖修复，并加 CI `finalize` job 治本（commit `<本次>`，见「下一步」第 2 条）。
- Release notes: `docs/release-notes.md`（**整体覆盖**，整合了 preview `08241522` + `8241708` + 冷启动优化的全部内容）
- 完整发布流程见 `docs/versioning.md` + `desktop/README.md` 第 75–117 行

## 本次发布前的实测数据

- **桌面端冷启动**: 25s → 12–13s（macOS Apple Silicon、空闲态冷启动、`workbench-server` 二进制到首屏可用的总时长）
- **PyInstaller 冒烟**: 优化前后均在本机跑过，spec 变更无回归；优化前产物大小 ~52 MB、优化后 ~46 MB
- **桌面端 smoke**: 二进制能起、`/healthz` 200、`/` 渲染正常（15ms）、updater JSON 模板正确

## main HEAD 相对于上一个 stable 的差

**main HEAD = `62ce938`**，比上一个 stable (`v0.4.5` = `46bfc20`) 多了 **13 个 commit**：

| commit | 类型 | 内容 |
|---|---|---|
| `62ce938` | **chore** | bump 正式版 0.4.6（桌面端冷启动优化 + 实验图表/批量录入/项目页 UX 改进） |
| `4ba08b3` | **fix** | 思维导图编辑全面修复（不能编辑根因 `syncDiff` 缺 `\| safe` → MM_BOOT 语法错误；拖动/字体/箭头/按钮；HTML 加 no-cache） |
| `6c762c5` | docs | 记录版本号格式坑（8月预览版必须月去前导零，`08` 开头被 Cargo 拒） |
| `8ceca25` | chore | bump 预览版 8241708（思维导图布局修复 + CI read-notes 加固） |
| `2f6740e` | **fix** | 思维导图编辑页布局修复（左栏固定 84px + 工具栏文字按钮自适应 + 恢复被 `{% block scripts %}` 清空的全局脚本 + 画布高度改填满网格） |
| `4eea59c` | chore | bump 预览版 08241522（图表逐条勾选 + 删除跳转 + key:value 解析；⚠️ CI 因 release-notes 缺换行失败一次） |
| `458910c` | chore | bump 预览版 08241339（Plotly 图表区 + 批量粘贴 + UX 改进） |
| `7ca1abf` | **feat** | 项目「当前研究阶段」加数据佐证条（方案 A） |
| `07b23dd` | **fix** | 项目编辑改走更新路由 + 删除收敛到侧滑/菜单 + 二次确认 UX |
| `cd55eda` | **C4** | 拖拽改子目录 + 子目录改名（API: `/api/artifacts/{id}/move` + `/api/folders/rename`） |
| `ea1230e` | **C3** | 项目级自定义类别 + 类别下子目录（`Project.categories_json` 字段） |
| `9f814b7` | **C2** | 文件树 + 缩略图网格 + 灯箱（`build_artifact_tree` Python 端构建） |
| `6261cb6` | **C1** | 创建实验默认选中当前目标（URL `?goal_id=`） |

## 下一步具体动作（接手者从这里开始）

1. **v0.4.6 发布已完成**（2026-09-06）: `main`、`v0.4.6` tag、`preview` tag（= `62ce938`）均已推送，两个 GitHub Release 已转正式。**无需重复 push / tag / 转正式**。

2. **本次 commit（未发版）—— updater latest.json 并发竞态修复**:
   - 根因: `build-desktop.yml` 三平台并行矩阵 + tauri-action `includeUpdaterJson` 对同一 `latest.json` 做非原子「下载 → 合并 → 上传」，后写覆盖前写 → 清单随机丢平台。v0.4.6 的 preview 清单曾被覆盖成只剩 linux（Windows 预览端报 `fallback platforms` 找不到）。
   - 处置: preview 清单已手动用 v0.4.6 完整清单覆盖修复（2026-09-07）。
   - 治本: workflow 新增 **`finalize` job**（`needs: desktop`），等三平台全部成功后用 `scripts/build_updater_json.py` 从 release 资产重建完整 `latest.json` 并 `--clobber` 覆盖；desktop 矩阵保留 `includeUpdaterJson`（各平台 `.sig` 上传行为不变），其并发产物只是 draft 期中间态。重建 key/url/signature 与 tauri-action 逐项一致（本地比对 v0.4.6 验证过）。
   - **下次预览/正式发版即走新流程**，latest.json 由 finalize 权威生成，无需人工补清单。

3. **思维导图连线交互**: `.claude/plans/dynamic-scribbling-hamming.md` 文件**已丢失**，需要重新整理 6 个备选方案给用户挑。

## 待办 / 搁置中的事

### 1. 思维导图连线交互方式选择（优先级: 🟡 中）

~~文件: `.claude/plans/dynamic-scribbling-hamming.md`~~ — **plan 文件已丢失**（2026-08-24 接手时发现）。

背景: 上一轮改了「选中节点后点另一个节点自动连边」用户嫌太激进。当时准备了 6 个备选方案（A 锚点拖拽 / B Alt+点击 / C 双击准备态 / D 显式按钮 / E 悬停+按钮 / F Ctrl+点击）。用户没选，现在 plan 文件也没了。

**下次接手**: 需要重新整理 6 个备选方案给用户挑，再动手实现。不要凭印象直接挑一个做。

### 2. C4 的拖拽手势 / 交互细节打磨（优先级: 🟢 低）

- 拖拽时缩略图半透明，但没有拖拽预览（cursor 跟手）
- 折叠的 folder 也能接收拖拽，但拖到 summary 边框附近才高亮，中间一大片没反应
- 改名 input 验证: 空 / 重名 / 与现有 folder 重合的检查只在服务端，客户端不预检

### 3. tags / labels 体系重构（优先级: 🟢 低，之前讨论过）

项目标签（现在 `Project.tags`，逗号分隔字符串）太简陋，想做成结构化 tag 表。没动。

### 4. 国际化 / 英文版（优先级: 🟢 低）

整个 UI 都是中文，没考虑过 i18n。提到过，没动。

## 后台清理

- 已清掉端口 8000–8019 上的 20 个残留 uvicorn 进程（2026-08-12）
- 当前进程（2026-09-05 实测）:
  - `uvicorn app.main:app` PID **10760**（在 8000 端口监听）
  - `start.py` PID **10653**（估计是它拉起的 uvicorn，自己也跑着）
  - `curl http://127.0.0.1:8000/` → HTTP 200, 15ms

## 本地开发状态

- **服务在跑**，端口 8000。`./run.sh --dev` / `./run.sh` 都能再开一份，注意端口别冲突。
- **日志位置**: uvicorn 进程的 stdout 当前**没有重定向到文件**（lsof 看 fd 1/2 没指向任何 `/tmp/wb*.log`，估计是 start.py 进程继承了它的 stdout）。
  - 临时看输出: `lsof -p 10760 | grep -E ' (1u|2u) '` 找 TTY，或直接 `curl :8000` 验活。
  - 重启并写日志: `kill 10760; nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/wb.log 2>&1 &`
- 数据库: `data/workbench.db`（test 数据混在里面，如要干净快照 `cp data/workbench.db /tmp/before.db`）

## 重要约束（用户偏好，不要违反）

- **强制推送（`--force-with-lease`）会触发自动拦截弹窗**，用 AskUserQuestion 先确认
- 发布预览版必须写 release notes（用户最看重，漏了会被打回）；正式版也要写并**整合之前预览版的全部功能描述**（用户要求）
- 版本号格式 `x.y.z-preview.MMDDHHMM`，**月去前导零、日时分补零**
- preview tag 固定叫 `preview`，每次 force push 滚动
- commit message 中文，简短描述 + 在 body 里写「详见 docs/release-notes.md」
- **模板 `<script>` 里的 `| tojson` 必须加 `| safe`**: 项目自定义了 tojson filter（`pages.py`，返回普通 `str` 而非 `Markup`），autoescape 会把 `"` 转义成 `&#34;`，而 `<script>` 是 raw text 不解码实体 → 整段 JS 语法错误。思维导图从 `08241339` 起不能编辑就是这个根因（`syncDiff` 漏加 safe），已修 — 已记入 `workbench-gotchas.md` 第 #18 项。
- **CI updater `latest.json` 并发坑**: tauri-action 的 `includeUpdaterJson` 在三平台并行矩阵下会对同一 `latest.json` 做非原子「下载→合并→上传」，后写覆盖前写 → 清单随机丢平台（v0.4.6 的 preview 清单曾被覆盖成只剩 linux，Windows/Mac 预览端检查更新报 `"None of the fallback platforms [\"windows-x86_64-nsis\", ...] were found"`）。已由 `build-desktop.yml` 的 **`finalize` job** + `scripts/build_updater_json.py` 治本（重建 key/url/signature 与 tauri-action 逐项一致，比对 v0.4.6 验证过）。**改 CI 时别把 latest.json 写回并行矩阵、别删 finalize**。

## 相关文档索引

- `docs/versioning.md` — 版本号规范 + 发布流程（强制）
- `docs/release-notes.md` — 最新 release notes（**本轮正式版 v0.4.6**）
- `docs/troubleshooting.md` — 历史踩坑（NSIS / ACL / 单实例 / semver 限制）
- `desktop/README.md` 第 75–117 行 — 桌面端发布 + updater 双轨
- `.claude/plans/dynamic-scribbling-hamming.md` — ~~思维导图连线交互方案~~ **文件已丢失，待重写**
- `scripts/build_updater_json.py` — finalize job 重建 latest.json 的生成脚本（含资产→platform key 映射注释）
- `~/.claude/projects/-Users-chenshu/memory/workbench-gotchas.md` — 维护性踩坑集合（已加入 #18 stdin EOF watchdog）