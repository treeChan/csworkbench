# 版本号规范

> 维护者必读。改版本号**永远只改一处**：仓库根 `VERSION` 文件（详见 `scripts/sync_version.py`）。

## 版本号唯一来源

- **仓库根 `VERSION` 文件**是一行 `x.y.z` 或 `x.y.z-preview.MDHHNN` 的版本号，全项目唯一真源。
- 改版本号 = 改 `VERSION` 一行，或 `python scripts/sync_version.py 0.4.5`。
- 运行时读它（`app/config.py`）；构建时由 `scripts/sync_version.py` 同步到 Cargo.toml / package.json；
  `tauri.conf.json` 不写版本号（Tauri 自动回退 Cargo.toml）。

## 版本号结构

格式：**主版本号.次版本号.修订号**，可选预览后缀。

```
x.y.z
x.y.z-preview.MDHHNN
│ │ │        └── 预览序号：月日时 + 序号，去前导零（如 809011 = 8月9日01时第1版）
│ │ └────────── z 修订号：bug 修复、小调整，不影响功能使用方式
│ └──────────── y 次版本号：新功能（用户可感知的能力增加）
└────────────── x 主版本号：架构级变化，如上云、多端同步、存储/API 不兼容重建
```

### x 主版本号

- **定义**：架构级更新，用户数据格式 / 存储 / API 不兼容，或产品形态发生根本变化。
- **何时 +1**：上云、多端同步、从本地单机变服务器模式、数据库结构不兼容重建、跨大版本升级。
- 升级通常需要迁移指引，旧版本数据不保证直接兼容。

### y 次版本号

- **定义**：新功能，向后兼容。
- **何时 +1**：加入用户可感知的新能力（如思维导图、更新渠道、备份恢复）。
- 新增功能后旧数据仍可正常使用，不需迁移。

### z 修订号

- **定义**：bug 修复、样式调整、性能优化，无新功能。
- **何时 +1**：修 bug、统一 UI、改文案、日常小修。
- 只做修复的版本通常不应引入新功能（避免用户被迫升级才能体验新东西）。

### preview 预览版

- **用途**：提前体验新功能的版本，只推送给设置页勾选了「加入预览计划」的用户。
- **版本号**：正式号 + `-preview.` + `MDHHNN`，其中
  - `MDHHNN` = 月日时 + 序号连写，**去掉所有前导零**（semver 规定 pre-release 里纯数字段不允许前导零，`080900001` 会被 cargo 拒绝）；
  - `M` = 月（1-12）、`DD` = 日（1-31）、`HH` = 时（0-23）、`NN` = 序号（同小时多次构建递增）。
  - 示例：`0.4.5-preview.809011` = 8月9日01时第1版；同日02时第2版 → `0.4.5-preview.809022`。
- **为什么这样命名**：让预览版版本号可排序——同一正式版上所有 `-preview.xxx` 都小于其正式版
  `x.y.z`（语义化版本规则），保证用户从预览版升正式版时检测到「更新」，且同一天内多个预览版可区分先后。
- 预览稳定后：把版本号改回 `x.y.z`，正式发布。

## 发布流程

### 正式版

1. `VERSION` 改成 `x.y.z`（如 `0.4.5`）。
2. 打 tag：`git tag v0.4.5 && git push origin v0.4.5`。
3. CI 构建三平台安装包，发布正式 Release。

### 预览版

1. `VERSION` 改成 `x.y.z-preview.MDHHNN`（如 `0.4.5-preview.809011`）。
2. 打 **固定名 `preview`** 的 tag（**不是**带版本号的 tag）：
   `git tag -f preview && git push origin preview -f`。
3. CI 构建三平台安装包，发布到 `preview` 的 pre-release。
4. 再次发预览版时，重复第 1~3 步（版本号换新，tag 仍叫 `preview`）。

> **为什么预览 tag 固定叫 `preview`**：桌面版检查更新的预览地址写死为
> `https://github.com/treeChan/csworkbench/releases/download/preview/latest.json`，
> 只有 tag 叫 `preview` 才能取到最新的 latest.json。版本号则在 latest.json 里，用户能看到完整的
> `x.y.z-preview.MDHHNN`。tag 名与版本号解耦：**tag 固定，版本号滚动**。

## 版本号一致性检查

改完 VERSION 后，确认四处一致：

```bash
python scripts/sync_version.py        # 同步 Cargo.toml / package.json
cat VERSION                           # 唯一源
grep '^version' desktop/src-tauri/Cargo.toml
python -c "from app.config import APP_VERSION; print(APP_VERSION)"
```

- `tauri.conf.json` **不应**包含 `version` 字段（Tauri 回退 Cargo.toml）。
- 本地 `npm run tauri build` 会自动触发同步（package.json 的 `pretauri` hook）；
  CI 构建前也会显式同步一次。
