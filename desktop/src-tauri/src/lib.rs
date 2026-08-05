use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Deserialize;
use tauri::{Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

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
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
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
                    let url = format!("http://127.0.0.1:{p}/")
                        .parse::<tauri::Url>()
                        .unwrap();
                    if let Some(win) = handle.get_webview_window("main") {
                        let _ = win.navigate(url);
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
