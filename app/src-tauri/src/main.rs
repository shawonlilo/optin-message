// Desktop entry point — all logic lives in the library target (lib.rs),
// which is also what the Android/iOS builds load.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    optin_message_lib::run();
}
