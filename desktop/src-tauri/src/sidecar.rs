// sidecar.rs — 拉起 Python 后端 exe，解析 JARVIS_PORT 握手
use std::time::Duration;

use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

use crate::AppState;

/// 清扫历史遗留的孤儿后端进程。
/// PyInstaller onefile 的 bootloader 可能因旧版本 crash/退出竞态而残留子进程
///（uvicorn），它们会一直占着 8100 端口、出现在任务管理器。单实例插件保证
/// 同一时刻只有一个主应用，因此按镜像名清扫只会命中孤儿、不会误杀合法后端。
fn sweep_orphan_backends() {
    // 注意：taskkill /IM 的通配符不支持「*」后跟字面后缀（如 *.exe 匹配不到）。
    // 必须用结尾通配符 jarvis2-backend*，才能同时命中 jarvis2-backend.exe 与
    // 原始名的 jarvis2-backend-x86_64-pc-windows-msvc.exe。
    let out = std::process::Command::new("taskkill")
        .args(["/F", "/IM", "jarvis2-backend*"])
        .output();
    match out {
        Ok(o) => {
            if o.status.success() {
                eprintln!("[jarvis2] swept orphan backend processes");
            }
        }
        Err(e) => eprintln!("[jarvis2] orphan sweep failed: {e}"),
    }
}

pub fn spawn_sidecar(app: tauri::AppHandle) {
    // 仅 release 调用本函数：拉起新后端前先清扫历史孤儿（见函数注释）
    sweep_orphan_backends();
    tauri::async_runtime::spawn(async move {
        let sidecar = match app.shell().sidecar("jarvis2-backend") {
            Ok(s) => s,
            Err(e) => {
                eprintln!("[jarvis2] sidecar build failed: {e}");
                return;
            }
        };
        let (mut rx, child) = match sidecar.spawn() {
            Ok(pair) => pair,
            Err(e) => {
                eprintln!("[jarvis2] sidecar spawn failed: {e}");
                return;
            }
        };
        *app.state::<AppState>().child.lock().unwrap() = Some(child);

        while let Some(evt) = rx.recv().await {
            match evt {
                CommandEvent::Stdout(line) => {
                    let s = String::from_utf8_lossy(&line);
                    if let Some(p) = parse_port(&s) {
                        *app.state::<AppState>().port.lock().unwrap() = Some(p);
                        eprintln!("[jarvis2] backend port: {p}");
                    }
                }
                CommandEvent::Stderr(line) => {
                    let s = String::from_utf8_lossy(&line);
                    if s.trim().is_empty() {
                        continue;
                    }
                    eprintln!("[jarvis2][backend] {}", s.trim_end());
                }
                CommandEvent::Terminated(t) => {
                    eprintln!("[jarvis2] backend exited: {:?}", t.code);
                    break;
                }
                _ => {}
            }
        }
    });
}

fn parse_port(s: &str) -> Option<u16> {
    s.lines()
        .find_map(|l| l.trim().strip_prefix("JARVIS_PORT="))
        .and_then(|p| p.trim().parse().ok())
}

/// 连子进程树一起杀掉 sidecar。
/// PyInstaller onefile 是 bootloader+子进程两级结构，`CommandChild::kill()`
/// 只杀 bootloader，子进程（真正的 uvicorn）会成孤儿 → 必须用 taskkill /T /F。
/// 用阻塞 `.status()` 而非 `.spawn()`：确保 taskkill 执行完再进入 `app.exit(0)`，
/// 消除「退出竞态」——否则主进程先退出、taskkill 还没来得及跑就被打断。
pub fn kill_sidecar_tree(app: &tauri::AppHandle) {
    use tauri::Manager;
    let pid = app
        .state::<AppState>()
        .child
        .lock()
        .unwrap()
        .take()
        .map(|c| c.pid());
    if let Some(pid) = pid {
        eprintln!("[jarvis2] killing sidecar tree pid={pid}");
        let _ = std::process::Command::new("taskkill")
            .args(["/T", "/F", "/PID", &pid.to_string()])
            .status();
    }
}

/// 拉起全局后台唤醒 agent（jarvis2-wake-agent.exe，独立常驻进程）。
/// 与后端 sidecar 不同：agent 生命周期独立于主应用 —— 主应用退出后它继续监听麦克风，
/// 因此此处「启动即忘」（detached，不纳入 AppState.child，退出时不杀）。
/// 仅在 agent 未监听 8123 时拉起；找不到 exe（dev 未打包）则静默跳过。
pub fn spawn_wake_agent(_app: &tauri::AppHandle) {
    use std::net::TcpStream;
    use std::time::Duration;

    // 已在监听 → 无需重复拉起（幂等）
    if TcpStream::connect_timeout(&"127.0.0.1:8123".parse().unwrap(), Duration::from_millis(400)).is_ok() {
        return;
    }

    // 两种命名都兼容：NSIS 安装版把 externalBin 装成无 triple 的 jarvis2-wake-agent.exe；
    // dev / staging 直拷时可能是带 triple 的 jarvis2-wake-agent-<triple>.exe。
    let Ok(triple) = tauri::utils::platform::target_triple() else { return };
    let triple_name = format!("jarvis2-wake-agent-{triple}.exe");
    let names = ["jarvis2-wake-agent.exe", triple_name.as_str()];
    let mut candidates: Vec<std::path::PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for n in &names {
                candidates.push(dir.join(n));                        // release：安装目录
                candidates.push(dir.join("../../binaries").join(n)); // dev：target/debug|release → src-tauri/binaries
            }
        }
    }
    let path = candidates.into_iter().find(|p| p.exists());
    let Some(path) = path else {
        eprintln!("[jarvis2] wake-agent exe not found (dev 请先 pyinstaller jarvis2-wake-agent.spec)");
        return;
    };

    match std::process::Command::new(&path).spawn() {
        Ok(_) => eprintln!("[jarvis2] wake-agent launched: {}", path.display()),
        Err(e) => eprintln!("[jarvis2] wake-agent launch failed: {e}"),
    }
}

pub async fn wait_port(port: u16, timeout: Duration) -> bool {
    let deadline = tokio::time::Instant::now() + timeout;
    while tokio::time::Instant::now() < deadline {
        if tokio::net::TcpStream::connect(("127.0.0.1", port))
            .await
            .is_ok()
        {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    false
}
