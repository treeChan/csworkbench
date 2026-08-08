fn main() {
    // 声明需要 ACL 权限的自定义命令（check_for_updates / start_auto_check / install_update）：
    // tauri-build 会据此自动生成 `allow-check-for-updates` 等权限，
    // capabilities/remote-dialog.json 里对该远程页面的授权才成立。
    // 默认所有自定义命令对窗口全开；显式声明后该命令受 ACL 约束，
    // 必须在 capability 中授权（设置页「检查更新」/ 全局更新弹窗来自 127.0.0.1 远程页面）。
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "check_for_updates",
                "start_auto_check",
                "install_update",
            ]),
        ),
    )
    .expect("failed to run tauri-build");
}
