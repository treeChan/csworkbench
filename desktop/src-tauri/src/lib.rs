use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::{Update, UpdaterExt};

/// 更新下载进度事件 payload（发给前端展示进度条）。
/// current / total 为已下载字节与总字节（total 可能未知为 0）。
#[derive(Clone, Serialize)]
struct UpdateProgress {
    current: u64,
    total: u64,
    percent: f32,
}

/// 检查到的新版本（emit 给前端统一弹窗展示）。
#[derive(Clone, Serialize)]
struct UpdateAvailable {
    version: String,
    notes: String,
}

/// 持有检查到、等待用户确认安装的 Update。前端点「立即更新」后由 install_update 取出。
struct PendingUpdate(Mutex<Option<Update>>);

/// 自动检查循环是否已启动（防止前端多页面切换时 spawn 多个重复循环）。
struct AutoCheckStarted(Mutex<bool>);

/// 更新渠道对应的 updater endpoint。
/// - stable：GitHub 最新正式 Release（自动排除 pre-release，普通用户不受预览版影响）
/// - preview：固定 tag 名 `preview` 的 Release（每次预览发布 force push 更新该 tag，
///   latest.json 里带完整版本号，如 0.4.4-preview.1，updater 弹窗即显示该版本号）
fn channel_endpoint(channel: &str) -> Option<String> {
    if channel == "preview" {
        Some(
            "https://github.com/treeChan/csworkbench/releases/download/preview/latest.json"
                .to_string(),
        )
    } else {
        None // 走 tauri.conf.json 里配置的默认 endpoint（正式渠道）
    }
}

/// 构建对应渠道的 Updater 并检查更新（网络结果），不依赖前端状态。
/// check_and_notify 与 install_update 兜底共用。
async fn fetch_update(
    app: &tauri::AppHandle,
    channel: &str,
) -> Result<Option<Update>, String> {
    // app.updater_builder() 返回 UpdaterBuilder（有 .endpoints() / .build()）；
    // app.updater() 是它的 build() 结果（无 .endpoints()）。
    let mut updater = app.updater_builder();
    if let Some(endpoint) = channel_endpoint(channel) {
        // 运行时覆盖 endpoint（UpdaterBuilder.endpoints 接受 Vec<Url>）
        let url = url::Url::parse(&endpoint).map_err(|e| format!("更新地址解析失败：{e}"))?;
        updater = updater
            .endpoints(vec![url])
            .map_err(|e| format!("更新配置失败：{e}"))?;
    }
    let updater = updater.build().map_err(|e| format!("更新组件初始化失败：{e}"))?;
    updater.check().await.map_err(|e| format!("无法连接更新服务器：{e}"))
}

/// 持有 sidecar 子进程句柄,退出时 kill。
struct Sidecar(Mutex<Option<CommandChild>>);

/// sidecar 就绪后打到 stdout 的握手行。
#[derive(Deserialize)]
struct Handshake {
    status: String,
    port: u16,
}

/// 平台用户数据目录:
///   macOS   ~/Library/Application Support/<identifier>
///   Windows %APPDATA%\<identifier>
///   Linux   $XDG_DATA_HOME 或 ~/.local/share/<identifier>
fn data_dir(app: &tauri::App) -> PathBuf {
    app.path()
        .app_data_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("csworkbench"))
}

/// 检查更新。有新版 → 存入 PendingUpdate 状态 + emit update://available 事件（前端统一弹窗），
/// 返回 "发现新版本 vX"；无新版 → 返回 "当前已是最新版本"；网络失败 → Err(可读错误)。
///
/// 本函数只「检查并广播」，不再弹原生对话框——确认与安装统一走前端居中弹窗 + install_update。
/// channel: "stable"（默认）/ "preview"。preview 用固定 tag 的 endpoint，
/// stable 走 tauri.conf.json 默认 endpoint。
async fn check_and_notify(app: &tauri::AppHandle, channel: &str) -> Result<String, String> {
    let update = match fetch_update(app, channel).await {
        Ok(u) => u,
        Err(e) => return Err(e),
    };
    let Some(update) = update else {
        return Ok("当前已是最新版本".to_string());
    };

    let version = update.version.clone();
    // release notes（latest.json 的 body 字段，来自 GitHub Release 说明，可能缺失）。
    // 完整原样传给前端（markdown 由前端渲染，弹窗内滚动查看），不在 Rust 侧截断。
    let notes = update.body.as_deref().unwrap_or("").trim().to_string();

    // 存起来，等用户在前端弹窗点「立即更新」后由 install_update 取用。
    *app.state::<PendingUpdate>().0.lock().unwrap() = Some(update);

    let _ = app.emit(
        "update://available",
        UpdateAvailable {
            version: version.clone(),
            notes,
        },
    );
    Ok(format!("发现新版本 v{version}"))
}

/// 设置页「检查更新」按钮入口（web 版无 __TAURI__ 时按钮被前端隐藏）。
/// channel 可选："stable"（默认）/ "preview"。只检查并广播，不弹原生框。
#[tauri::command]
async fn check_for_updates(
    app: tauri::AppHandle,
    channel: Option<String>,
) -> Result<String, String> {
    let channel = channel.as_deref().unwrap_or("stable");
    check_and_notify(&app, channel).await
}

/// 启动后静默自动检查更新：有新版 → 弹前端统一弹窗；已是最新 → 不打扰；
/// 网络失败 → 静默，1 分钟后再试，直到拿到结果。
/// 由前端页面加载时调用（传当前更新渠道）。全程只允许一个检查循环（AutoCheckStarted 防重）。
#[tauri::command]
async fn start_auto_check(app: tauri::AppHandle, channel: Option<String>) -> Result<(), String> {
    {
        let started_state = app.state::<AutoCheckStarted>();
        let mut started = started_state.0.lock().unwrap();
        if *started {
            return Ok(()); // 已有循环在跑，避免页面切换重复启动
        }
        *started = true;
    }
    let channel = channel.unwrap_or_else(|| "stable".to_string());
    tauri::async_runtime::spawn(async move {
        loop {
            match check_and_notify(&app, &channel).await {
                Ok(_) => return,   // 已是最新 or 已广播新版，结束本轮
                Err(_) => {
                    // 网络不通：静默重试（1 分钟间隔），直到成功或应用退出。
                    tokio::time::sleep(Duration::from_secs(60)).await;
                }
            }
        }
    });
    Ok(())
}

/// 前端弹窗点「立即更新」后调用：取出检查到的新版本，下载安装并重启。
/// 下载进度通过 update://download-progress 事件广播给前端弹窗展示。
/// channel 可选："stable"（默认）/ "preview"，与 check 用同一渠道。
#[tauri::command]
async fn install_update(app: tauri::AppHandle, channel: Option<String>) -> Result<(), String> {
    let channel = channel.as_deref().unwrap_or("stable");
    // 优先取 check_and_notify 暂存的 Update；若因极端时序（弹窗已显示但暂存被消费）
    // 为空，现场重新检查兜底——宁可多查一次，也不能让用户「提示有更新却装不上」。
    // 注意：MutexGuard 非 Send，必须先在独立语句里 take() 释放锁，再跨 await。
    let pending_update = app.state::<PendingUpdate>().0.lock().unwrap().take();
    let update = match pending_update {
        Some(u) => u,
        None => match fetch_update(&app, channel).await? {
            Some(u) => u,
            None => return Err("当前已是最新版本，无需更新".to_string()),
        },
    };

    let app_emit = app.clone();
    // 进度回调：把下载进度广播给前端（弹窗进度条）。
    // download_and_install 的第一个闭包收到 (已下载, 总字节 Option)。
    // total 为 None（未知）时只提示等待；Some 时算百分比。
    let progress = move |current: usize, total: Option<u64>| {
        let current = current as u64;
        let (total, percent) = match total {
            Some(t) if t > 0 => (t, (current as f32 / t as f32) * 100.0),
            _ => (0, 0.0),
        };
        let _ = app_emit.emit(
            "update://download-progress",
            UpdateProgress { current, total, percent },
        );
    };
    let installed = update.download_and_install(progress, || {}).await;
    let _ = app.clone().emit("update://download-done", ());
    match installed {
        // app.restart() 返回 never type (!)，可隐式转换成任意类型，直接作为 Ok 分支结果。
        Ok(_) => app.restart(),
        Err(e) => Err(format!("更新下载/安装失败：{e}")),
    }
}

/// 轮询直到 127.0.0.1:port 的 HTTP 服务可响应;成功 true,超时(30s)false。
async fn wait_for_http(url: &str) -> bool {
    let port: u16 = url
        .trim_start_matches("http://127.0.0.1:")
        .trim_end_matches('/')
        .parse()
        .unwrap_or(0);
    if port == 0 {
        return false;
    }
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if probe_health(port).await {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
    false
}

/// 对端口发一个极简 GET /health,读到任意 HTTP/1.x 状态行即视为可服务。
async fn probe_health(port: u16) -> bool {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let Ok(mut stream) = tokio::net::TcpStream::connect(("127.0.0.1", port)).await else {
        return false;
    };
    let req = format!(
        "GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).await.is_err() {
        return false;
    }
    let mut buf = [0u8; 128];
    match stream.read(&mut buf).await {
        Ok(n) if n > 0 => String::from_utf8_lossy(&buf[..n]).starts_with("HTTP/1."),
        _ => false,
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // 单实例:必须最先注册,防止两个实例同时写 SQLite。
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.show();
                let _ = win.unminimize();
                let _ = win.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            check_for_updates,
            start_auto_check,
            install_update
        ])
        .manage(Sidecar(Mutex::new(None)))
        .manage(PendingUpdate(Mutex::new(None)))
        .manage(AutoCheckStarted(Mutex::new(false)))
        .setup(|app| {
            // 自动检查更新由前端页面加载时调 start_auto_check 触发（能读取当前更新渠道）。
            let handle = app.handle().clone();
            let dir = data_dir(app);
            std::fs::create_dir_all(&dir).ok();
            let db = dir.join("workbench.db");
            let port_file = dir.join(".server-port.json");

            // 拉起 PyInstaller sidecar(文件名 = externalBin 的 basename)。
            let (mut rx, child) = handle
                .shell()
                .sidecar("workbench-server")
                .map_err(|e| e.to_string())?
                // 把 appdata 目录传给 sidecar:它的用户设置持久层(WORKBENCH_APP_DATA_DIR/.env)
                // 写在这里,重启后从这里读回,实现"设置页改路径跨重启生效"。
                .env("WORKBENCH_APP_DATA_DIR", dir.to_string_lossy().into_owned())
                .args(vec![
                    "--db".into(),
                    db.to_string_lossy().into_owned(),
                    "--port".into(),
                    "8750".into(),
                    "--port-file".into(),
                    port_file.to_string_lossy().into_owned(),
                    "--artifacts".into(),
                    dir.join("artifacts").to_string_lossy().into_owned(),
                ])
                .spawn()
                .map_err(|e| e.to_string())?;

            *handle.state::<Sidecar>().0.lock().unwrap() = Some(child);

            // 异步读 stdout,等 {"status":"ready","port":N},拿到端口后导航。
            tauri::async_runtime::spawn(async move {
                let deadline = Instant::now() + Duration::from_secs(120);
                let mut port: Option<u16> = None;

                while port.is_none() && Instant::now() < deadline {
                    match tokio::time::timeout(Duration::from_millis(200), rx.recv()).await {
                        Ok(Some(CommandEvent::Stdout(line))) => {
                            if let Ok(h) =
                                serde_json::from_str::<Handshake>(&String::from_utf8_lossy(&line))
                            {
                                if h.status == "ready" {
                                    port = Some(h.port);
                                }
                            }
                        }
                        Ok(Some(CommandEvent::Stderr(line))) => {
                            eprintln!("[workbench-server] {}", String::from_utf8_lossy(&line));
                        }
                        Ok(Some(CommandEvent::Terminated(s))) => {
                            eprintln!("[workbench-server] exited: {:?}", s.code);
                            break;
                        }
                        Ok(Some(_)) => {}
                        Ok(None) => break,
                        Err(_) => {
                            // recv 超时:兜底读握手文件(万一 stdout 不可读)。
                            if let Ok(txt) = std::fs::read_to_string(&port_file) {
                                if let Ok(h) = serde_json::from_str::<Handshake>(&txt) {
                                    if h.status == "ready" {
                                        port = Some(h.port);
                                    }
                                }
                            }
                        }
                    }
                }

                if let Some(p) = port {
                    let url = format!("http://127.0.0.1:{p}/");
                    // 先确认 HTTP 服务真正可响应再导航:避免 WebView 首请求落在
                    // uvicorn 尚未 accept 的窗口期,短暂显示「127.0.0.1 拒绝访问」。
                    if !wait_for_http(&url).await {
                        eprintln!("[csworkbench] 服务探测超时,仍尝试导航");
                    }
                    if let Ok(u) = url.parse::<tauri::Url>() {
                        if let Some(win) = handle.get_webview_window("main") {
                            let _ = win.navigate(u);
                        }
                    }
                } else {
                    eprintln!("[csworkbench] sidecar 未在预期时间内就绪");
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build app")
        .run(|app, event| {
            // 退出时 kill sidecar;Python 侧另有 stdin-EOF 看门狗兜底。
            if let RunEvent::Exit = event {
                if let Some(child) = app.state::<Sidecar>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}
