// Opt-In Message — Tauri library root. Desktop runs this through
// src/main.rs; Android/iOS load this same code as a library target.
//
// v0.6.0: the engine is BUILT IN. CI freezes the Python engine
// (engine_main.py) with PyInstaller and bundles it as a Tauri sidecar
// (`optin-engine`), so end users need nothing installed. Launch order:
//   (a) bundled sidecar binary (exe dir),
//   (b) system python3/python/py running the bundled .py resources,
//   (c) an embedded Python previously fetched by auto-setup,
//   (d) welcome screen with honest status (+ one-click auto-setup on
//       Windows: download the official Python embeddable zip into the
//       app-data dir and run the engine with it).
//
// No external crates beyond tauri itself: process spawn is std, the
// /health poll is a raw TcpStream HTTP GET, downloads use the OS's own
// curl.exe / PowerShell on Windows.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent, State};

struct Engine {
    child: Mutex<Option<Child>>,
    url: Mutex<Option<String>>,
    runtime: Mutex<Option<String>>, // label: "built-in engine" / "Python 3.11.4" / ...
    mode: Mutex<Option<String>>,    // "sidecar" | "python" | "embedded"
    launch_done: Mutex<bool>,       // a full launch attempt has finished
    setting_up: Mutex<bool>,
    setup_error: Mutex<Option<String>>,
    data_dir: Mutex<Option<String>>,
}

const EMBED_ZIP_URL: &str =
    "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip";

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
            let text = String::from_utf8_lossy(&buf[..n]).to_string();
            return text.contains("\"ok\": true") || text.contains("\"ok\":true");
        }
    }
    false
}

fn escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}

fn json_opt(v: &Option<String>) -> String {
    match v {
        Some(u) => format!("\"{}\"", escape(u)),
        None => "null".to_string(),
    }
}

#[cfg(windows)]
fn no_console(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_console(_cmd: &mut Command) {}

fn data_dir_for(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    let dir = app.path().app_data_dir().ok()?;
    let _ = std::fs::create_dir_all(&dir);
    let state = app.state::<Engine>();
    let mut slot = state.data_dir.lock().unwrap();
    if slot.is_none() {
        *slot = Some(dir.to_string_lossy().to_string());
    }
    Some(dir)
}

/// Spawn a prepared command as the engine and wait for /health.
/// Returns true when the engine answered healthy within `wait`.
fn spawn_and_wait(app: &tauri::AppHandle, mut cmd: Command, port: u16, wait: Duration) -> bool {
    let state = app.state::<Engine>();
    cmd.env("PORT", port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    no_console(&mut cmd);
    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(_) => return false,
    };
    {
        let mut slot = state.child.lock().unwrap();
        *slot = Some(child);
    }
    let deadline = Instant::now() + wait;
    while Instant::now() < deadline {
        if health_ok(port) {
            let url = format!("http://127.0.0.1:{port}");
            *state.url.lock().unwrap() = Some(url.clone());
            if let Some(win) = app.get_webview_window("main") {
                if let Ok(target) = format!("{url}/admin").parse::<tauri::Url>() {
                    let _ = win.navigate(target);
                }
            }
            return true;
        }
        // Bail early if the child already died (wrong-arch sidecar, bad
        // interpreter, ...) so the next launch option gets its turn.
        let exited = {
            let mut slot = state.child.lock().unwrap();
            match slot.as_mut() {
                Some(c) => matches!(c.try_wait(), Ok(Some(_))),
                None => true,
            }
        };
        if exited {
            let taken = {
                let mut slot = state.child.lock().unwrap();
                slot.take()
            };
            drop(taken);
            return false;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    // Timed out without a health answer: kill this attempt's child so the
    // next launch option (or a later restart) starts from a clean slate.
    let taken = {
        let state = app.state::<Engine>();
        let t = state.child.lock().unwrap().take();
        t
    };
    if let Some(mut c) = taken {
        let _ = c.kill();
        let _ = c.wait();
    }
    false
}

fn set_runtime(app: &tauri::AppHandle, mode: &str, label: String) {
    let state = app.state::<Engine>();
    *state.mode.lock().unwrap() = Some(mode.to_string());
    *state.runtime.lock().unwrap() = Some(label);
}

fn start_engine(app: &tauri::AppHandle) {
    let state = app.state::<Engine>();
    if state.url.lock().unwrap().is_some() {
        return; // already running
    }
    let data_dir = match data_dir_for(app) {
        Some(d) => d,
        None => {
            *state.launch_done.lock().unwrap() = true;
            return;
        }
    };
    let db_path = data_dir.join("optin.db");
    let port = match free_port() {
        Some(p) => p,
        None => {
            *state.launch_done.lock().unwrap() = true;
            return;
        }
    };

    // (a) Bundled sidecar binary — the built-in engine (normal path).
    let exe_name = if cfg!(windows) {
        "optin-engine.exe"
    } else {
        "optin-engine"
    };
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sidecar = dir.join(exe_name);
            if sidecar.exists() {
                set_runtime(app, "sidecar", "built-in engine".to_string());
                let mut cmd = Command::new(&sidecar);
                cmd.current_dir(&data_dir)
                    .env("DATABASE_PATH", db_path.to_string_lossy().to_string());
                if spawn_and_wait(app, cmd, port, Duration::from_secs(25)) {
                    return;
                }
            }
        }
    }

    // Bundled .py resources (used by options b and c).
    let res_dir = app.path().resource_dir().ok();
    let script = res_dir
        .as_ref()
        .map(|d| d.join("python").join("app.py"))
        .filter(|p| p.exists());

    // (b) System Python running the bundled resources.
    if let (Some(script), Some((py_cmd, py_ver))) = (&script, find_python()) {
        set_runtime(app, "python", format!("Python {py_ver}"));
        let mut cmd = Command::new(py_cmd);
        cmd.arg(script)
            .current_dir(script.parent().unwrap())
            .env("DATABASE_PATH", db_path.to_string_lossy().to_string());
        if spawn_and_wait(app, cmd, port, Duration::from_secs(15)) {
            return;
        }
    }

    // (c) Embedded Python fetched earlier by auto-setup (Windows).
    if let Some(script) = &script {
        let embed_exe = data_dir.join("python-embed").join("python.exe");
        if embed_exe.exists() {
            set_runtime(app, "embedded", "embedded Python 3.12".to_string());
            let mut cmd = Command::new(&embed_exe);
            cmd.arg(script)
                .current_dir(script.parent().unwrap())
                .env("DATABASE_PATH", db_path.to_string_lossy().to_string());
            if spawn_and_wait(app, cmd, port, Duration::from_secs(15)) {
                return;
            }
        }
    }

    // (d) Nothing worked — the welcome screen shows the honest status.
    *state.mode.lock().unwrap() = None;
    *state.runtime.lock().unwrap() = None;
    *state.launch_done.lock().unwrap() = true;
}

fn kill_child(app: &tauri::AppHandle) {
    // Same E0597-safe pattern as the exit handler: bind the taken child
    // to a local in its own scope so no MutexGuard temporary outlives
    // the State borrow.
    let child = {
        let state = app.state::<Engine>();
        let taken = state.child.lock().unwrap().take();
        taken
    };
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Real invoke target for the welcome screen's status pill (factory
/// release gate): "connected" is reported only when the Python engine's
/// /health has actually answered.
#[tauri::command]
fn app_info(state: State<Engine>) -> String {
    let url = state.url.lock().unwrap().clone();
    let runtime = state.runtime.lock().unwrap().clone();
    let mode = state.mode.lock().unwrap().clone();
    let setting_up = *state.setting_up.lock().unwrap();
    let setup_error = state.setup_error.lock().unwrap().clone();
    let launch_done = *state.launch_done.lock().unwrap();
    let data_dir = state.data_dir.lock().unwrap().clone();
    let engine = if url.is_some() {
        "connected"
    } else if setting_up {
        "setting_up"
    } else if launch_done {
        "needs_setup"
    } else {
        "starting"
    };
    format!(
        "{{\"version\":\"{}\",\"engine\":\"{}\",\"python_available\":{},\"python_version\":\"{}\",\"runtime\":{},\"mode\":{},\"server_url\":{},\"can_setup\":{},\"setting_up\":{},\"setup_error\":{},\"data_dir\":{}}}",
        env!("CARGO_PKG_VERSION"),
        engine,
        runtime.is_some(),
        escape(runtime.as_deref().unwrap_or("")),
        json_opt(&runtime),
        json_opt(&mode),
        json_opt(&url),
        cfg!(windows) && url.is_none(),
        setting_up,
        json_opt(&setup_error),
        json_opt(&data_dir)
    )
}

/// Navigate the main window to the dashboard. Called by the welcome
/// screen (and automatically once the engine is healthy).
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

/// Kill the engine and start it again (welcome screen's native option).
#[tauri::command]
fn restart_engine(app: tauri::AppHandle) -> Result<(), String> {
    kill_child(&app);
    {
        let state = app.state::<Engine>();
        *state.url.lock().unwrap() = None;
        *state.launch_done.lock().unwrap() = false;
        *state.setup_error.lock().unwrap() = None;
    }
    let handle = app.clone();
    std::thread::spawn(move || start_engine(&handle));
    Ok(())
}

/// Open the app-data folder (database, embedded Python) in the OS file
/// manager (welcome screen's native option).
#[tauri::command]
fn open_data_dir(app: tauri::AppHandle) -> Result<String, String> {
    let dir = data_dir_for(&app).ok_or_else(|| "no app-data dir".to_string())?;
    let path = dir.to_string_lossy().to_string();
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new("explorer");
        c.arg(&dir);
        c
    } else if cfg!(target_os = "macos") {
        let mut c = Command::new("open");
        c.arg(&dir);
        c
    } else {
        let mut c = Command::new("xdg-open");
        c.arg(&dir);
        c
    };
    cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    cmd.spawn().map_err(|e| e.to_string())?;
    Ok(path)
}

fn setup_engine_blocking(app: &tauri::AppHandle) -> Result<(), String> {
    let data_dir = data_dir_for(app).ok_or("no app-data dir")?;
    let embed_dir = data_dir.join("python-embed");
    let python_exe = embed_dir.join("python.exe");

    if !python_exe.exists() {
        let zip_path = data_dir.join("python-embed.zip");
        // Download the official embeddable Python with Windows' own curl.
        let dl = Command::new("curl.exe")
            .args([
                "-L",
                "--fail",
                "-o",
                &zip_path.to_string_lossy(),
                EMBED_ZIP_URL,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|e| format!("could not start curl.exe: {e}"))?;
        if !dl.success() {
            return Err("download of the Python runtime failed (check your connection)".into());
        }
        let _ = std::fs::create_dir_all(&embed_dir);
        let ps = format!(
            "Expand-Archive -Force -Path '{}' -DestinationPath '{}'",
            zip_path.to_string_lossy().replace('\'', "''"),
            embed_dir.to_string_lossy().replace('\'', "''")
        );
        let ex = Command::new("powershell")
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &ps])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|e| format!("could not start PowerShell: {e}"))?;
        if !ex.success() || !python_exe.exists() {
            return Err("expanding the Python runtime failed".into());
        }
    }

    // The embeddable build restricts sys.path to its python312._pth file;
    // add the bundled engine folder so `python.exe <app.py>` can import
    // ai.py and find gui/ next to the script.
    if let Some(res_dir) = app.path().resource_dir().ok() {
        let engine_dir = res_dir.join("python");
        let pth = embed_dir.join("python312._pth");
        if let Ok(mut text) = std::fs::read_to_string(&pth) {
            let entry = engine_dir.to_string_lossy().to_string();
            if !text.lines().any(|l| l.trim() == entry) {
                if !text.ends_with('\n') {
                    text.push_str("\r\n");
                }
                text.push_str(&entry);
                text.push_str("\r\n");
                let _ = std::fs::write(&pth, text);
            }
        }
    }
    Ok(())
}

/// One-click auto-setup (welcome screen): fetch a private Python runtime
/// into the app-data dir, then launch the engine with it. Windows only —
/// elsewhere the built-in sidecar is the norm and silent install needs
/// admin rights the app does not ask for.
#[tauri::command]
fn setup_engine(app: tauri::AppHandle) -> Result<(), String> {
    if !cfg!(windows) {
        return Err(
            "auto-setup is Windows-only; the built-in engine normally needs no setup".into(),
        );
    }
    {
        let state = app.state::<Engine>();
        if *state.setting_up.lock().unwrap() {
            return Ok(()); // already running
        }
        *state.setting_up.lock().unwrap() = true;
        *state.setup_error.lock().unwrap() = None;
        *state.launch_done.lock().unwrap() = false;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        let result = setup_engine_blocking(&handle);
        {
            let state = handle.state::<Engine>();
            *state.setting_up.lock().unwrap() = false;
            if let Err(e) = &result {
                *state.setup_error.lock().unwrap() = Some(e.clone());
                *state.launch_done.lock().unwrap() = true;
            }
        }
        if result.is_ok() {
            start_engine(&handle);
        }
    });
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(Engine {
            child: Mutex::new(None),
            url: Mutex::new(None),
            runtime: Mutex::new(None),
            mode: Mutex::new(None),
            launch_done: Mutex::new(false),
            setting_up: Mutex::new(false),
            setup_error: Mutex::new(None),
            data_dir: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            app_info,
            open_dashboard,
            restart_engine,
            open_data_dir,
            setup_engine
        ])
        .setup(|app| {
            // The "main" window comes from tauri.conf.json (factory
            // pattern) and starts on the bundled welcome page; Rust
            // navigates it to the real dashboard once the engine is up.
            let handle = app.handle().clone();
            std::thread::spawn(move || start_engine(&handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Opt-In Message")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                kill_child(app);
            }
        });
}
