// Opt-In Message — Simple App shell (v0.7.0).
//
// This is a plain Tauri 2 app: it opens one window showing the static
// frontend in app/ui (index.html + app.js + logo.png). The whole product
// — opt-in, double opt-in, contacts, campaigns, risk check, audit log —
// runs in that frontend with on-device local storage.
//
// There is deliberately NOTHING else here: no helper binary, no child
// processes, no ports, no local web server, no background work.
// Desktop runs this through src/main.rs; Android/iOS load this same
// code as a library target.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .build(tauri::generate_context!())
        .expect("error while building Opt-In Message")
        .run(|_app, _event| {});
}
