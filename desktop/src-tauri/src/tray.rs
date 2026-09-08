// tray.rs — 系统托盘：打开 HUD / 显示桌宠 / 隐藏桌宠 / 退出
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::Manager;

use crate::AppState;

pub fn setup_tray(app: &tauri::App<tauri::Wry>) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open_hud", "打开 HUD", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show_pet", "显示桌宠", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide_pet", "隐藏桌宠", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &show, &hide, &quit])?;

    let mut builder = TrayIconBuilder::new()
        .tooltip("JARVIS2")
        .menu(&menu)
        .show_menu_on_left_click(false);

    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }

    builder
        .on_tray_icon_event(|tray, event| {
            // 左键单击托盘图标 → 打开 HUD（符合用户直觉）
            if let tauri::tray::TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: tauri::tray::MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle().clone();
                tauri::async_runtime::spawn(async move {
                    let state = app.state::<AppState>();
                    let _ = crate::commands::open_hud(app.clone(), state).await;
                });
            }
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open_hud" => {
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    let state = app.state::<AppState>();
                    let _ = crate::commands::open_hud(app.clone(), state).await;
                });
            }
            "show_pet" => {
                if let Some(p) = app.get_webview_window("pet") {
                    let _ = p.show();
                    let _ = p.set_focus();
                }
            }
            "hide_pet" => {
                if let Some(p) = app.get_webview_window("pet") {
                    let _ = p.hide();
                }
            }
            "quit" => {
                crate::sidecar::kill_sidecar_tree(app);
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}
