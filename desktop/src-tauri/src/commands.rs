// commands.rs — 暴露给前端（桌宠页）的 Tauri IPC 命令
use std::time::Duration;

use serde_json::json;
use tauri::{AppHandle, Manager, State};

use crate::{sidecar, AppState};

#[tauri::command]
pub async fn get_port(state: State<'_, AppState>) -> Result<u16, String> {
    // release：轮询直到 sidecar 报告端口
    for _ in 0..100 {
        if let Some(p) = *state.port.lock().unwrap() {
            return Ok(p);
        }
        tokio::time::sleep(Duration::from_millis(120)).await;
    }
    Err("backend 未在 12s 内就绪".into())
}

#[tauri::command]
pub async fn open_hud(app: AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let port = get_port(state).await?;
    // 等后端 TCP 可连（后端 up 后再开窗，避免白屏）
    sidecar::wait_port(port, Duration::from_secs(30)).await;
    let url = format!("http://127.0.0.1:{port}/");
    if let Some(w) = app.get_webview_window("hud") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    } else {
        let parsed = tauri::Url::parse(&url).map_err(|e| e.to_string())?;
        tauri::WebviewWindowBuilder::new(&app, "hud", tauri::WebviewUrl::External(parsed))
            .title("JARVIS2 · 个人 AI 指挥官")
            .inner_size(1280.0, 820.0)
            .min_inner_size(960.0, 620.0)
            .center()
            .build()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
pub fn set_pet_pos(window: tauri::WebviewWindow, app: AppHandle, x: f64, y: f64) -> Result<(), String> {
    // 只允许桌宠窗调用（HUD 页也注入 IPC 全局，做 label 守卫）
    if window.label() != "pet" {
        return Err("forbidden".into());
    }
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let file = dir.join("pet_pos.json");
    std::fs::write(file, json!({ "x": x, "y": y }).to_string()).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_pet_pos(app: AppHandle) -> Result<Option<(f64, f64)>, String> {
    let dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let file = dir.join("pet_pos.json");
    let raw = match std::fs::read_to_string(file) {
        Ok(s) => s,
        Err(_) => return Ok(None),
    };
    let v: serde_json::Value = serde_json::from_str(&raw).map_err(|e| e.to_string())?;
    Ok(match (v.get("x").and_then(|n| n.as_f64()), v.get("y").and_then(|n| n.as_f64())) {
        (Some(x), Some(y)) => Some((x, y)),
        _ => None,
    })
}

#[tauri::command]
pub fn quit_app(window: tauri::WebviewWindow, app: AppHandle) -> Result<(), String> {
    // 只允许桌宠窗触发退出（HUD 的 http 页也注入了 __TAURI__，做 label 守卫）
    if window.label() != "pet" {
        return Err("forbidden".into());
    }
    sidecar::kill_sidecar_tree(&app);
    app.exit(0);
    Ok(())
}

pub fn apply_saved_pet_pos(app: &tauri::App) -> tauri::Result<()> {
    let pet = match app.get_webview_window("pet") {
        Some(w) => w,
        None => return Ok(()),
    };
    let saved = get_pet_pos(app.handle().clone()).unwrap_or(None);
    if let Some((x, y)) = saved {
        pet.set_position(tauri::PhysicalPosition::new(x as i32, y as i32))?;
    } else {
        // 默认：主显示器右下角（避开任务栏）
        if let Some(monitor) = app.primary_monitor()? {
            let size = monitor.size();
            let scale = monitor.scale_factor();
            let w = pet.outer_size()?.width;
            let h = pet.outer_size()?.height;
            let margin_x = (48.0 * scale) as i32;
            let margin_y = (72.0 * scale) as i32;
            let x = size.width as i32 - w as i32 - margin_x;
            let y = size.height as i32 - h as i32 - margin_y;
            if x > 0 && y > 0 {
                pet.set_position(tauri::PhysicalPosition::new(x, y))?;
            }
        }
    }
    pet.show()?;
    pet.set_focus()?;
    Ok(())
}
