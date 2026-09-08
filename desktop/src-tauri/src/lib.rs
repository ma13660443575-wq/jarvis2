// JARVIS2 桌面壳：托盘 + 桌宠窗 + 后端 sidecar 生命周期
mod commands;
mod sidecar;
mod tray;

use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_autostart::ManagerExt;

pub struct AppState {
    pub port: Mutex<Option<u16>>,
    pub child: Mutex<Option<tauri_plugin_shell::process::CommandChild>>,
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 二次启动：聚焦已有实例（桌宠或 HUD）
            if let Some(hud) = app.get_webview_window("hud") {
                let _ = hud.show();
                let _ = hud.set_focus();
            } else if let Some(pet) = app.get_webview_window("pet") {
                let _ = pet.show();
            }
        }))
        .setup(|app| {
            app.manage(AppState {
                port: Mutex::new(None),
                child: Mutex::new(None),
            });
            tray::setup_tray(app)?;
            // 开机自启（写 HKCU\...\Run）；已启用则 no-op
            let _ = app.autolaunch().enable();
            commands::apply_saved_pet_pos(app)?;
            #[cfg(not(debug_assertions))]
            sidecar::spawn_sidecar(app.handle().clone());
            #[cfg(debug_assertions)]
            {
                // dev：直接用本地 run.bat 起的后端
                *app.state::<AppState>().port.lock().unwrap() = Some(8100);
            }
            // 拉起全局唤醒 agent（独立进程，退出不杀；已在监听则跳过）
            sidecar::spawn_wake_agent(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_port,
            commands::open_hud,
            commands::set_pet_pos,
            commands::get_pet_pos,
            commands::quit_app,
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "hud" {
                    // 关 HUD 只是收起，回到桌宠
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build JARVIS2 app");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            // 确保 sidecar 进程树一并退出，不留孤儿进程
            sidecar::kill_sidecar_tree(app_handle);
        }
    });
}
