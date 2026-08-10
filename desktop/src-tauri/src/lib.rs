use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::{Update, UpdaterExt};

/// 更新下载进度事件 payload（发给前端展示进度条）。
/// current / total 为已下载字节与总字节（total 可能未知为 0）。
/// speed_bytes_per_sec 为当前下载速度（字节/秒，0 = 未知）；
/// eta_secs 为预计剩余秒数（None = 速度未知无法估算）。
#[derive(Clone, Serialize)]
struct UpdateProgress {
    current: u64,
    total: u64,
    percent: f32,
    speed_bytes_per_sec: f64,
    eta_secs: Option<u64>,
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

/// 自动检查开关（设置页「自动检查更新」）。false 时循环在下一轮退出，
/// 不再主动扫描仓库；手动「检查更新」不受影响。
struct AutoCheckEnabled(Arc<AtomicBool>);

/// 正在进行的更新下载任务。前端点「取消」时 abort 该任务，
/// reqwest 连接随之中断 → 真正的取消（不产生半成品，安装只在下载成功后执行）。
struct UpdateTask(Mutex<Option<tauri::async_runtime::JoinHandle<()>>>);

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
/// 设置页「自动检查更新」开关关闭后（AutoCheckEnabled=false），循环在下一轮退出。
#[tauri::command]
async fn start_auto_check(app: tauri::AppHandle, channel: Option<String>) -> Result<(), String> {
    // 设置页「自动检查更新」已关闭：不启动循环（防御：即使前端误调也不扫描）。
    if !app.state::<AutoCheckEnabled>().0.load(Ordering::SeqCst) {
        return Ok(());
    }
    {
        let started_state = app.state::<AutoCheckStarted>();
        let mut started = started_state.0.lock().unwrap();
        if *started {
            return Ok(()); // 已有循环在跑，避免页面切换重复启动
        }
        *started = true;
    }
    let channel = channel.unwrap_or_else(|| "stable".to_string());
    // 关掉开关后循环也要退出：用带退出标志的循环，避免关不掉。
    let enabled = app.state::<AutoCheckEnabled>().0.clone();
    let app2 = app.clone();
    tauri::async_runtime::spawn(async move {
        loop {
            if !enabled.load(Ordering::SeqCst) {
                break; // 用户关闭了自动检查
            }
            match check_and_notify(&app, &channel).await {
                Ok(_) => return,   // 已是最新 or 已广播新版，结束本轮（保持 started=true）
                Err(_) => {
                    // 网络不通：静默重试（1 分钟间隔），直到成功、开关关闭或应用退出。
                    tokio::time::sleep(Duration::from_secs(60)).await;
                }
            }
        }
        // 只有「开关被关闭」才走到这：复位防重标志，允许用户重新开启后再启动循环。
        // （检查成功 / 广播新版走的是上面的 return，started 保持 true，避免重复检查。）
        *app2.state::<AutoCheckStarted>().0.lock().unwrap() = false;
    });
    Ok(())
}

/// 设置页「自动检查更新」开关：开启 → 允许后台启动静默检查循环（由前端随后调
/// start_auto_check 启动）；关闭 → 停掉正在运行的循环（下一轮 break），不再主动
/// 扫描仓库。手动点「检查更新」不受此开关影响。
/// 复位 AutoCheckStarted：若旧循环已结束（检查成功）而 started 残留 true，
/// 用户重新开启后将永远无法再启动新循环——这里在开关状态变化时允许重新启动。
#[tauri::command]
async fn set_auto_check_enabled(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    app.state::<AutoCheckEnabled>().0.store(enabled, Ordering::SeqCst);
    // 复位防重标志：关闭时允许之后重新开启；开启时若此前已关闭过，也允许启动新循环。
    // （正在跑的循环不依赖此标志，它检查的是 enabled。）
    *app.state::<AutoCheckStarted>().0.lock().unwrap() = false;
    Ok(())
}

/// 前端弹窗点「立即更新」后调用：取出检查到的新版本，后台下载并安装。
/// 下载进度通过 update://download-progress 事件广播；完成 → update://download-done；
/// 失败 → update://download-error；点「取消」→ cancel_update abort 下载任务。
/// channel 可选："stable"（默认）/ "preview"，与 check 用同一渠道。
///
/// 进度修复：tauri-plugin-updater 的进度回调第一个参数是「单次 chunk 字节数」
/// （通常几 KB），不是累计已下载字节——直接拿它当 current 会让进度条永远显示
/// 0.0MB。这里在闭包里累计 downloaded 再上报，进度才真实。
#[tauri::command]
async fn install_update(app: tauri::AppHandle, channel: Option<String>) -> Result<(), String> {
    let channel = channel.as_deref().unwrap_or("stable");
    // 已有下载任务在跑：不允许并发启动第二个（点「重试」前 cancel 会清掉任务槽）。
    if app.state::<UpdateTask>().0.lock().unwrap().is_some() {
        return Err("已有更新任务正在进行".to_string());
    }
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
    // 下载+安装放进后台任务：取消 = abort 该任务（reqwest 连接随之中断）。
    let task = tauri::async_runtime::spawn(async move {
        // 进度回调：累计已下载字节 → 广播给前端（弹窗进度条）。
        let emit = app_emit.clone();
        let mut downloaded: u64 = 0;
        // 速度/ETA 计算：相邻两次回调的时间差与字节差 → bytes/s → 剩余时间。
        let mut last_instant = Instant::now();
        let mut last_bytes: u64 = 0;
        let mut speed: f64 = 0.0;
        let progress = move |chunk: usize, total: Option<u64>| {
            downloaded += chunk as u64;
            // 至少间隔 0.3s 才重算一次速度，避免小包抖动量出 0/极大值。
            let now = Instant::now();
            let dt = now.duration_since(last_instant).as_secs_f64();
            if dt >= 0.3 {
                let d = downloaded.saturating_sub(last_bytes) as f64;
                speed = d / dt;
                last_bytes = downloaded;
                last_instant = now;
            }
            let (total, percent) = match total {
                Some(t) if t > 0 => (t, (downloaded as f32 / t as f32) * 100.0),
                _ => (0, 0.0),
            };
            let eta_secs = if total > downloaded && speed > 0.0 {
                Some(((total - downloaded) as f64 / speed).ceil() as u64)
            } else {
                None
            };
            let _ = emit.emit(
                "update://download-progress",
                UpdateProgress {
                    current: downloaded,
                    total,
                    percent,
                    speed_bytes_per_sec: speed,
                    eta_secs,
                },
            );
        };
        // 下载（插件内部完成签名校验）。失败 → 广播错误 + 清空任务槽。
        let bytes = match update.download(progress, || {}).await {
            Ok(b) => b,
            Err(e) => {
                let _ = app_emit.emit("update://download-error", format!("更新下载失败：{e}"));
                *app_emit.state::<UpdateTask>().0.lock().unwrap() = None;
                return;
            }
        };
        let _ = app_emit.emit("update://download-done", ());
        // 安装。Windows：install 内部已启动安装器并退出应用；macOS/Linux 需手动重启。
        match update.install(bytes) {
            Ok(()) => {
                *app_emit.state::<UpdateTask>().0.lock().unwrap() = None;
                #[cfg(not(windows))]
                app_emit.restart();
            }
            Err(e) => {
                let _ = app_emit.emit("update://download-error", format!("更新安装失败：{e}"));
                *app_emit.state::<UpdateTask>().0.lock().unwrap() = None;
            }
        }
    });
    *app.state::<UpdateTask>().0.lock().unwrap() = Some(task);
    Ok(())
}

/// 前端点「取消」：abort 正在进行的下载任务（reqwest 连接中断 = 真取消）。
/// 此时安装尚未开始，不产生半成品；再次点「立即更新」会重新下载。
/// 广播 download-cancelled 让各页面进度条恢复隐藏。
#[tauri::command]
async fn cancel_update(app: tauri::AppHandle) -> Result<(), String> {
    let task = app.state::<UpdateTask>().0.lock().unwrap().take();
    if let Some(task) = task {
        task.abort();
    }
    let _ = app.emit("update://download-cancelled", ());
    Ok(())
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
            set_auto_check_enabled,
            install_update,
            cancel_update
        ])
        .manage(Sidecar(Mutex::new(None)))
        .manage(PendingUpdate(Mutex::new(None)))
        .manage(AutoCheckStarted(Mutex::new(false)))
        .manage(AutoCheckEnabled(Arc::new(AtomicBool::new(true))))
        .manage(UpdateTask(Mutex::new(None)))
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
