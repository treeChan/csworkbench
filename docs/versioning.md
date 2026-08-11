# 版本号规范

> 维护者必读。改版本号**永远只改一处**：仓库根 `VERSION` 文件（详见 `scripts/sync_version.py`）。

## 版本号唯一来源

- **仓库根 `VERSION` 文件**是一行 `x.y.z` 或 `x.y.z-preview.MMDDHHMM` 的版本号，全项目唯一真源。
- 改版本号 = 改 `VERSION` 一行，或 `python scripts/sync_version.py 0.4.5`。
- 运行时读它（`app/config.py`）；构建时由 `scripts/sync_version.py` 同步到 Cargo.toml / package.json；
  `tauri.conf.json` 不写版本号（Tauri 自动回退 Cargo.toml）。

## 版本号结构

格式：**主版本号.次版本号.修订号**，可选预览后缀。

```
x.y.z
x.y.z-preview.MMDDHHMM
│ │ │        └── 预览时间戳：月(1-2位) + 日(2位) + 时(2位) + 分(2位)，
│ │ │            月去前导零、日月时分补零（如 8101351 = 8月10日13时51分）
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
- **版本号**：正式号 + `-preview.` + `MMDDHHMM`，其中
  - `MMDDHHMM` = 月日时分定长编码，**月去前导零**、日/时/分各 2 位补零；
  - `M` = 月（1-12，去前导零，占 1~2 位）、`DD` = 日（1-31，补零 2 位）、
    `HH` = 时（0-23，补零 2 位）、`MM` = 分（0-59，补零 2 位）。
  - 示例：`0.4.5-preview.8101351` = 8月10日13时51分；同日 14:00 → `0.4.5-preview.8101400`。
- **为什么月去前导零、其余补零**：semver 规定 pre-release 里纯数字段不允许前导零
  （`08100351` 会被 cargo 拒绝），而**月份最小为 1**，去掉前导零后整个时间戳必然
  以非零数字开头，天然合法；同时日/时/分固定 2 位让时间戳**定长**，数值大小严格
  反映时间先后（8月10日 → `8101xxx` 恒大于 8月9日 → `809xxxx`），避免「位数变短
  导致数值变小、检查不到更新」的问题。
- **为什么这样命名**：让预览版版本号可排序——同一正式版上所有 `-preview.xxx` 都小于其正式版
  `x.y.z`（语义化版本规则），保证用户从预览版升正式版时检测到「更新」，且同一天内多个预览版可区分先后。
- 预览稳定后：把版本号改回 `x.y.z`，正式发布，并同时把 `preview` tag 指到正式版
  commit（见下方「正式版」发布流程），让预览用户也能升到正式版；之后新的预览版
  （`x.y.z+1-preview.*`）再从正式版出发继续滚动。

## 打包格式：不使用 MSI

- `tauri.conf.json` 的 `bundle.targets` 已移除 `msi`，全平台共用一份配置
  （`["appimage","deb","nsis","app","dmg"]`），各平台自动只打它认识的格式，
  无需分平台代码。
- **原因**：MSI 对 pre-release 版本号有硬限制——pre-release 必须是纯数字且 **≤ 65535**，
  预览版时间戳（如 `809011`）装不下，打包直接失败。Windows 改用 NSIS（`.exe`），
  功能与 msi 完全相同。

## 发布流程

### 正式版

1. `VERSION` 改成 `x.y.z`（如 `0.4.5`）。
2. 打 tag：`git tag v0.4.5 && git push origin v0.4.5`。
3. **同步预览渠道**：把 `preview` tag 也指到同一 commit 并 force push——
   `git tag -f preview && git push origin preview -f`。
   这样已加入预览计划的用户也会自动收到正式版（`x.y.z` > `x.y.z-preview.*`），
   无需手动退出预览计划。
4. CI 构建三平台安装包，发布正式 Release；preview Release 同步更新。

### 预览版

1. `VERSION` 改成 `x.y.z-preview.MMDDHHMM`（如 `0.4.5-preview.8101351`）。
2. 打 **固定名 `preview`** 的 tag（**不是**带版本号的 tag）：
   `git tag -f preview && git push origin preview -f`。
3. CI 构建三平台安装包，发布到 `preview` 的 pre-release。
4. 再次发预览版时，重复第 1~3 步（版本号换新，tag 仍叫 `preview`）。

> **自动清理历史安装包**：构建成功后 CI 会自动删除 `preview` Release 中所有
> 「非当前版本号」的安装包，只保留 `latest.json`（updater 清单）和本次构建的包，
> 避免同一 Release 越积越大（单 Release assets 上限 2GB）。无需手动清理。
> 仅对 `preview` tag 生效；正式版每次 tag 都是独立 Release，不受影响。

> **为什么预览 tag 固定叫 `preview`**：桌面版检查更新的预览地址写死为
> `https://github.com/treeChan/csworkbench/releases/download/preview/latest.json`，
> 只有 tag 叫 `preview` 才能取到最新的 latest.json。版本号则在 latest.json 里，用户能看到完整的
> `x.y.z-preview.MMDDHHMM`。tag 名与版本号解耦：**tag 固定，版本号滚动**。

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
