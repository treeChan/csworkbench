# Workbench 排错记录

> 本文件记录开发 / 构建 / 发布过程中踩过的坑与解法。每条都对应一次真实事故，
> 描述「现象 → 根因 → 修复」。以后遇到类似问题先查这里。
>
> 相关：主 README「排查问题」是给**用户**看的；本文件是给**维护者**看的。
> 桌面端专项见 `desktop/README.md`。

## 目录

- [一、Tauri 桌面端（ACL / updater / 构建）](#一tauri-桌面端)
- [二、NSIS 安装包（升级 / 卸载 / 目录）](#二nsis-安装包)
- [三、Windows 启动时序](#三windows-启动时序)
- [四、macOS Gatekeeper](#四macos-gatekeeper)
- [五、版本与 CI](#五版本与-ci)
- [六、Python 后端](#六python-后端)

> 数据库表结构 / schema 变更 / 前后向兼容：见 [database-schema.md](database-schema.md)。

---

## 一、Tauri 桌面端

### 1.1 自定义命令报 `not allowed by ACL` / 构建报 `Permission allow-check-for-updates not found`

**现象**：
- 运行期：设置页点「检查更新」，toast 显示「检查更新失败：Command check_for_updates not allowed by ACL」。
- 构建期：三平台 CI 全失败，日志：
  ```
  Permission allow-check-for-updates not found, expected one of core:default, ...
  ```

**根因**（Tauri v2 的破坏性变更）：
- Tauri v2 中，**自定义命令（`#[tauri::command]`）默认不自动生成 `allow-*` 权限**，
  也不对本地窗口设限。但我们的页面来自**远程 URL**（`http://127.0.0.1:PORT`，
  Tauri 视作 remote origin）。
- Tauri **2.11 起**，remote origin 调用自定义命令被**强制走 ACL 检查**：既要在
  `capabilities/*.json` 里授权，该权限也必须真实存在。而我们只在 capability 里写了
  `allow-check-for-updates`，它从未被生成 → 构建期直接报 not found。

**修复**（两处都要做）：
1. `desktop/src-tauri/build.rs`：把命令声明为受限，tauri-build 才会生成权限：
   ```rust
   fn main() {
       tauri_build::try_build(
           tauri_build::Attributes::new().app_manifest(
               tauri_build::AppManifest::new().commands(&["check_for_updates"]),
           ),
       )
       .expect("failed to run tauri-build");
   }
   ```
2. `desktop/src-tauri/capabilities/remote-dialog.json`：给远程页面授权：
   ```json
   { "remote": { "urls": ["http://127.0.0.1:*/*"] },
     "permissions": ["core:default", "dialog:allow-open", "allow-check-for-updates"] }
   ```

**权限名规则**：命令 `check_for_updates`（snake_case）→ 权限 `allow-check-for-updates`
（kebab-case）。tauri-build 源码里就是 `command.replace('_', "-")`。

> **新增自定义命令的清单**：以后每加一个需要从设置页（远程页面）调用的命令，
> 都要同步改 `build.rs` 的 `commands` 列表 + `remote-dialog.json` 的 permissions。
> 只加 capability 不加 build.rs → not found；只加 build.rs 不加 capability → not allowed by ACL。

### 1.1b 同一命令的类型坑：`update.body` 是 `Option<String>`

**现象**：`let body = update.body.trim();` 编译报
`no method named trim found for enum Option<String>`。

**根因**：`tauri_plugin_updater::Update` 的 `body`（release notes）字段类型是
**`Option<String>`**（服务端可能不返回说明），不是 `String`。

**修复**：`update.body.as_deref().unwrap_or("").trim()`。

### 1.2 updater 签名报 `Invalid symbol 37, offset 348`

**现象**：CI 构建 Windows 安装包时，updater 签名步骤失败，报 `Invalid symbol 37, offset 348`。

**根因**：`TAURI_SIGNING_PRIVATE_KEY` 私钥内容粘贴到 GitHub Secrets 时，
末尾**混入了 shell 提示符的 `%`**（zsh 提示符里的 `%`）。私钥是 minisign 格式
单行 base64（348 字符），以 `==` 结尾；base64 的 `==` padding 必须保留，多出来的
`%` 必须去掉。

**修复**：重新配置 GitHub Secret，粘贴完整私钥、去掉末尾多余字符。

> ⚠️ 注意：GitHub Secrets 里**不允许空值**。`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
> 若没有口令，需设置非空占位或改用有口令的私钥（参考当时处理）。

### 1.3 Rust 编译错误：dialog 回调 / download_and_install 参数

**现象**：`dialog().show()` 回调、`update.download_and_install()` 编译不过。

**根因 / 修复**：
- `tauri-plugin-dialog` 的 `.show(|result| ...)` 回调参数是 **`bool`**（不是
  `MessageDialogResult`），判断用 `if result { ... }`。
- `tauri-plugin-updater` 的 `update.download_and_install` **需要两个参数**：
  `(|_, _| {}, || {})` —— 进度闭包 + 完成闭包，缺一个编译报错。

### 1.4 updater 需要自有签名密钥对（与苹果 / 微软证书无关）

**现象 / 认知**：以为装了代码签名证书就能用 updater。

**事实**：Tauri v2 updater **必须**有自己的签名密钥对：
```bash
npm run tauri signer generate -w ~/.tauri/csworkbench.key
```
- 公钥（`.pub.pem` 内容）写进 `tauri.conf.json` 的 `plugins.updater.pubkey`
- 私钥存 GitHub Secrets：`TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
- CI（tauri-action）里 `uploadUpdaterJson: true`、`uploadUpdaterSignatures: true`

### 1.5 在线更新端点依赖正式 Release

**现象**：点「检查更新」总是「当前已是最新版本」/ 连不上。

**根因**：updater 端点是 `/releases/latest/download/latest.json`。**draft Release
期间取不到**，必须把 draft **转正式 Release** 才能在线更新。安装包覆盖升级不受影响
（离线可用）。

---

## 二、NSIS 安装包

### 2.1 升级安装总是「先卸载再安装」

**现象**：在已装旧版上跑新安装包，流程是「卸载 → 重装」，用户无从选「直接更新」。

**根因**：Tauri 默认 `installer.nsi` 的 `PageReinstall` 检测到已装旧版后展示
「卸载后安装 / 不卸载」选择页，默认文案偏「先卸载」。

**修复**：自定义 NSIS 模板 `desktop/src-tauri/nsis/installer.nsi`：
- `PageReinstall` 里 `SemverCompare` 后，`$R0 >= 0`（同版重装 / 升级）且非 WiX
  迁移 → `Abort`（跳过选择页）+ `StrCpy $UpgradeMode 1`。
- `PageLeaveReinstall` 里同样 `$R0 >= 0` 时 `Goto reinst_done`（双保险）。

### 2.2 升级时还会「先选安装位置」

**现象**：2.1 修完后，升级仍然显示「选择安装位置」页。

**根因**：页面顺序是 `PageReinstall → MUI_PAGE_DIRECTORY → 开始菜单 → 安装`。
跳过重装选择页后，**目录选择页（MUI_PAGE_DIRECTORY）还在**。

**修复**：给目录页加 pre 函数 `SkipDirectoryIfUpgrade`：
```nsi
Function SkipDirectoryIfUpgrade
  ${If} $UpgradeMode = 1
    Abort
  ${EndIf}
  ${IfThen} $PassiveMode = 1  ${|} Abort ${|}
FunctionEnd
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipDirectoryIfUpgrade
!insertmacro MUI_PAGE_DIRECTORY
```

**安装位置继承**：`.onInit` 里 `RestorePreviousInstallLocation` 从注册表
`Software\Manufacturer\ProductName` 读回上次安装位置覆盖 `$INSTDIR`。
所以升级时**自动继承已装位置**，不是用默认位置；全新安装才让选目录。
在线更新（updater 调用安装器）同样走这条继承逻辑。

### 2.3 升级时看不出是「更新」，像默默覆盖

**现象**：2.1 / 2.2 修完后升级几乎无提示，用户以为只是覆盖。

**修复**：安装 Section 开头（`$UpgradeMode = 1` 时）：
```nsi
SectionSetText ${Install} "正在更新 ${PRODUCTNAME}"
DetailPrint "正在将 ${PRODUCTNAME} 更新至 ${VERSION}"
DetailPrint "（覆盖升级：保留应用配置与数据，安装位置沿用上次的）"
```

> ⚠️ 维护注意：NSIS 模板升级 Tauri 版本时需对照新版 tauri-bundler 默认模板同步，
> 重点是 4 处「自定义」块：PageReinstall 强制覆盖、PageLeaveReinstall 双保险、
> SkipDirectoryIfUpgrade 目录跳过、Install Section 升级提示。

### 2.4 appdata 数据保留

**事实**：NSIS 卸载器默认**不删 `%APPDATA%\com.csworkbench.desktop`**，只有卸载页
勾选「删除数据」才删。所以无论覆盖升级还是卸载重装，`.env` + `workbench.db` 都保留，
配置不重置。

---

## 三、Windows 启动时序

### 3.1 启动后先转圈 → 闪「127.0.0.1 拒绝访问」→ 自动刷新正常

**现象**：打开桌面 App：先显示「正在启动本地服务」占位页，页面消失后短暂显示
「127.0.0.1 拒绝访问」，几秒后自动刷新变正常。

**根因**：Python sidecar 的 ready 握手时机正确（socket bind + lifespan 完成后才输出
`{"status":"ready","port":N}`），但 Rust 侧拿到端口后**一次性 `navigate`、无重试**，
Windows 冷启动下 WebView 首请求到达时 uvicorn 可能尚未 accept → 短暂拒绝访问，
靠 Chromium 自身重试才恢复。

**修复**：`desktop/src-tauri/src/lib.rs` 的 `wait_for_http`：navigate 前用
tokio `TcpStream` 连 `127.0.0.1:port`，发 `GET /health HTTP/1.1` 读到 `HTTP/1.x`
状态行才导航；失败 150ms 重试，最多 30s，超时仍 navigate 兜底。

---

## 四、macOS Gatekeeper

### 4.1 未签名 App 提示「已损坏，应将其移入废纸篓」

**现象**：macOS 上打开从 GitHub Releases 下载的 App，系统提示「csworkbench 已损坏，
应将其移入废纸篓」。

**根因**：未签名 + 未公证的 App 被 Gatekeeper 拦截（quarantine 属性）。

**修复（文档引导，无证书）**：
1. 右键 → 打开；或
2. 终端：`xattr -cr "/Applications/csworkbench.app"`（移除 quarantine 属性）

**根治**：Apple Developer 会员（$99/年），Developer ID 签名 + 公证。**不必上架
Mac App Store**。Apple 对开源**没有免费证书豁免**。有证书后还需配置 CI secrets：
`APPLE_CERTIFICATE` / `APPLE_CERTIFICATE_PASSWORD` / `APPLE_SIGNING_IDENTITY` /
`APPLE_ID` / `APPLE_PASSWORD` / `APPLE_TEAM_ID`。

---

## 五、版本与 CI

### 5.1 Cargo.lock 未提交 → CI 每次都解析最新 Tauri 版本

**现象**：功能代码没动，某次 CI 突然全平台失败（如 1.1 的 ACL 强制变更）。

**根因**：`desktop/src-tauri/Cargo.toml` 依赖写的是 `version = "2"`（浮动），且
**Cargo.lock 未提交到 git**。CI 每次构建都会重新解析、拉取当时最新 2.x，上游
破坏性变更会直接打穿。

**当前状态**：已知但**未处理**（保持浮动版本，跟随上游）。若想稳定：
- 提交 `desktop/src-tauri/Cargo.lock`（推荐，可复现构建），或
- 在 Cargo.toml 锁定具体版本（如 `tauri = "=2.11.1"`）。

### 5.2 CI 里 updater 产物 / 签名

**事实**：tauri-action 需 `uploadUpdaterJson: true`、`uploadUpdaterSignatures: true`
才会产出 `latest.json` 和 `.sig`。产物出现在 draft Release 和 workflow artifacts 里。

---

## 六、Python 后端

### 6.1 FastAPI TestClient 需要 httpx

**现象**：用 `TestClient` 测路由报错 `No module named 'httpx'` / starlette 依赖缺失。

**修复**：`pip install httpx`；本仓库验证渲染时改用「真实 uvicorn 启动 + curl」的方式
（见历史会话）。

---

## 维护原则

1. **改完功能先本地验证**（网页版 `python start.py --dev`），再走桌面打包检查清单
   （见 `desktop/README.md`「打包检查清单」）。
2. **每次打 tag 前**：核对 4 处版本号（`app/config.py`、`Cargo.toml`、
   `tauri.conf.json`、`package.json`）+ 更新 release notes。
3. **新增设置页可调用的自定义命令**：改 `build.rs` commands + `remote-dialog.json`
   permissions（见 1.1）。
4. **push 前先更新文档**；commit 尽量合并。
