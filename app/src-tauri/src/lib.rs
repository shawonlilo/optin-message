// Opt-In Message — Tauri library root. Desktop runs this through
// src/main.rs; Android/iOS load this same code as a library target.
//
// The product engine is the Python app (app.py + ai.py + gui/, bundled as
// Tauri resources under `python/`). This shell is a launcher: on setup it
// finds Python 3, starts the engine on a free localhost port with its
// database in the app-data dir (resources are read-only on some
// platforms), waits for /health, then navigates the webview to the real
// dashboard at http://127.0.0.1:<port>/admin. If Python is missing, the
// window stays on the bundled launcher page, which explains the Python 3
// requirement and shows the engine badge in "demo" state.
//
// No external crates beyond tauri itself: process spawn is std, the
// /health poll is a raw TcpStream HTTP GET.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent, State};

struct Engine {
    child: Mutex<Option<Child>>,
    url: Mutex<Option<String>>,
    python: Mutex<Option<String>>, // e.g. "3.11.4" once detected
}

fn find_python() -> Option<(&'static str, String)> {
    for cand in ["python3", "python", "py"] {
        if let Ok(out) = Command::new(cand).arg("--version").output() {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
                let ver = text
                    .strip_prefix("Python ")
                    .unwrap_or(&text)
                    .trim()
                    .to_string();
                return Some((cand, ver));
            }
        }
    }
    None
}

fn free_port() -> Option<u16> {
    TcpListener::bind("127.0.0.1:0")
        .ok()
        .and_then(|l| l.local_addr().ok())
        .map(|a| a.port())
}

fn health_ok(port: u16) -> bool {
    if let Ok(mut s) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().unwrap(),
        Duration::from_millis(400),
    ) {
        let _ = s.set_read_timeout(Some(Duration::from_millis(600)));
        if s
            .write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            .is_err()
        {
            return false;
        }
        let mut buf = [0u8; 512];
        if let Ok(n) = s.read(&mut buf) {
            return String::from_utf8_lossy(&buf[..n]).contains("\"ok\": true")
                || String::from_utf8_lossy(&buf[..n]).contains("\"ok\":true");
        }
    }
    false
}

fn escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

/// Real invoke target for the launcher page's engine badge (factory
/// release gate): the badge only says "connected" when this Rust command
/// answers AND the Python engine's /health has responded.
#[tauri::command]
fn app_info(state: State<Engine>) -> String {
    let python = state.python.lock().unwrap().clone();
    let url = state.url.lock().unwrap().clone();
    let engine = if url.is_some() {
        "connected"
    } else if python.is_some() {
        "starting"
    } else {
        "demo"
    };
    format!(
        "{{\"version\":\"{}\",\"engine\":\"{}\",\"python_available\":{},\"python_version\":\"{}\",\"server_url\":{}}}",
        env!("CARGO_PKG_VERSION"),
        engine,
        python.is_some(),
        escape(python.as_deref().unwrap_or("")),
        match &url {
            Some(u) => format!("\"{}\"", escape(u)),
            None => "null".to_string(),
        }
    )
}

/// Navigate the main window to the Python dashboard. Called by the
/// launcher page (and automatically once the engine is healthy).
#[tauri::command]
fn open_dashboard(app: tauri::AppHandle, state: State<Engine>) -> Result<(), String> {
    let url = state
        .url
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "engine not running yet".to_string())?;
    let win = app
        .get_webview_window("main")
        .ok_or_else(|| "main window missing".to_string())?;
    let target = tauri::Url::parse(&format!("{url}/admin")).map_err(|e| e.to_string())?;
    win.navigate(target).map_err(|e| e.to_string())
}

fn start_engine(app: &tauri::AppHandle) {
    let state = app.state::<Engine>();
    let (py_cmd, py_ver) = match find_python() {
        Some(v) => v,
        None => return, // launcher page explains the Python requirement
    };
    *state.python.lock().unwrap() = Some(py_ver);

    let port = match free_port() {
        Some(p) => p,
        None => return,
    };

    // Bundled Python app: <resource_dir>/python/app.py
    let res_dir = match app.path().resource_dir() {
        Ok(d) => d,
        Err(_) => return,
    };
    let script = res_dir.join("python").join("app.py");
    if !script.exists() {
        return;
    }
    // Writable DB location (resource dir is read-only under Program
    // Files / inside .app bundles).
    let data_dir = app.path().app_data_dir().unwrap_or_else(|_| res_dir.clone());
    let _ = std::fs::create_dir_all(&data_dir);
    let db_path = data_dir.join("optin.db");

    let child = Command::new(py_cmd)
        .arg(&script)
        .current_dir(script.parent().unwrap_or(&res_dir))
        .env("PORT", port.to_string())
        .env("DATABASE_PATH", db_path.to_string_lossy().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    let child = match child {
        Ok(c) => c,
        Err(_) => return,
    };
    *state.child.lock().unwrap() = Some(child);

    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if health_ok(port) {
            let url = format!("http://127.0.0.1:{port}");
            *state.url.lock().unwrap() = Some(url.clone());
            if let Some(win) = app.get_webview_window("main") {
                if let Ok(target) = format!("{url}/admin").parse::<tauri::Url>() {
                    let _ = win.navigate(target);
                }
            }
            return;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
}

pub fn run() {
    tauri::Builder::default()
        .manage(Engine {
            child: Mutex::new(None),
            url: Mutex::new(None),
            python: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![app_info, open_dashboard])
        .setup(|app| {
            // The "main" window comes from tauri.conf.json (factory
            // pattern) and starts on the bundled launcher page; Rust
            // navigates it to the real dashboard once the engine is up.
            let handle = app.handle().clone();
            std::thread::spawn(move || start_engine(&handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Opt-In Message")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                // Take the child out in its own scope: keeping the
                // MutexGuard temporary alive across the if-let body
                // while `state` drops is E0597 (this one borrow error
                // failed every CI platform build in v0.5.1).
                let child = {
                    let state = app.state::<Engine>();
                    state.child.lock().unwrap().take()
                };
                if let Some(mut child) = child {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}
