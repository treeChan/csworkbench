use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Deserialize;
use tauri::{Manager, RunEvent};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

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

/// 检查更新并弹原生确认框。有新版 → 确认后下载安装并重启;无新版 → 提示已是最新;
/// 网络失败 → 返回可读错误(离线用户走安装包覆盖升级,在线更新只是补充路径)。
async fn check_and_prompt(app: tauri::AppHandle) -> Result<String, String> {
    let updater = app.updater().map_err(|e| format!("更新组件初始化失败：{e}"))?;
    let update = match updater.check().await {
        Ok(u) => u,
        Err(e) => return Err(format!("无法连接更新服务器：{e}")),
    };
    let Some(update) = update else {
        return Ok("当前已是最新版本".to_string());
    };

    let version = update.version.clone();
    // release notes（latest.json 的 body 字段，来自 GitHub Release 说明，可能缺失）。
    // 对话框里展示更新日志，让用户决定是否更新；太长截断，避免窗口撑得过高。
    let body = update.body.as_deref().unwrap_or("").trim();
    let notes = if body.is_empty() {
        String::new()
    } else {
        let truncated: String = body.chars().take(600).collect();
        if truncated.chars().count() < body.chars().count() {
            format!("{truncated}\n…（已截断，完整说明见 GitHub Release）")
        } else {
            truncated
        }
    };
    let message = if notes.is_empty() {
        format!("发现新版本 v{version}，是否下载并安装？\n\n安装完成后应用将自动重启。")
    } else {
        format!("发现新版本 v{version}：\n\n{notes}\n\n是否下载并安装？\n安装完成后应用将自动重启。")
    };

    app.dialog()
        .message(message)
        .title("Workbench 更新")
        .kind(tauri_plugin_dialog::MessageDialogKind::Info)
        .buttons(tauri_plugin_dialog::MessageDialogButtons::OkCancel)
        .show(move |result| {
            if result {
                tauri::async_runtime::spawn(async move {
                    match update.download_and_install(|_, _| {}, || {}).await {
                        Ok(_) => app.restart(),
                        Err(e) => eprintln!("[csworkbench] 更新下载/安装失败: {e}"),
                    }
                });
            }
        });
    Ok(format!("发现新版本 v{version}"))
}

/// 设置页「检查更新」按钮入口（web 版无 __TAURI__ 时按钮被前端隐藏）。
#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle) -> Result<String, String> {
    check_and_prompt(app).await
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
        .invoke_handler(tauri::generate_handler![check_for_updates])
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            // 启动后延迟几秒静默检查一次更新;离线失败静默,发现新版才弹提示。
            let updater_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_secs(5)).await;
                let _ = check_and_prompt(updater_handle).await;
            });

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
