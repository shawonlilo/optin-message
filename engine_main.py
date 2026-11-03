#!/usr/bin/env python3
"""
Opt-In Message — frozen engine entrypoint.

This is the file PyInstaller freezes into the standalone `optin-engine`
sidecar binary bundled with the desktop app, so end users never need
Python installed. It is a thin, frozen-safe wrapper around app.py:

- multiprocessing.freeze_support() runs first (required for frozen apps).
- DATABASE_PATH is defaulted BEFORE app.py is imported (app.py reads it
  at import time): if the shell/user hasn't set it, the DB goes in a
  per-user data directory — never next to the executable, because the
  install dir (Program Files / .app bundle) is read-only.
- app.py's own __main__ behaviour (init_db + HTTP server on PORT) is
  then executed by importing app and calling its main() entry.

Run from source it behaves exactly like `python3 app.py`.
"""
import multiprocessing
import os
import sys


def _default_db_path():
    """Per-user writable data dir for the frozen engine's database."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        folder = os.path.join(base, "Opt-In Message")
    elif sys.platform == "darwin":
        folder = os.path.expanduser("~/Library/Application Support/Opt-In Message")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        folder = os.path.join(base, "optin-message")
    return os.path.join(folder, "optin.db")


def main():
    multiprocessing.freeze_support()
    if not os.environ.get("DATABASE_PATH"):
        os.environ["DATABASE_PATH"] = _default_db_path()
    import app as engine_app

    engine_app.init_db()
    import ai as ai_engine

    conn = engine_app.db()
    cfg = ai_engine.get_ai_config(conn)
    conn.close()
    print(
        f"Opt-In Message v{engine_app.VERSION} engine on "
        f"http://127.0.0.1:{engine_app.PORT}  DRY_RUN={engine_app.DRY_RUN}  "
        f"AI={'meta-ai' if cfg['configured'] else 'local'} (source={cfg['source']})  "
        f"DB={engine_app.DB_PATH}",
        flush=True,
    )
    from http.server import ThreadingHTTPServer

    ThreadingHTTPServer(("127.0.0.1", engine_app.PORT), engine_app.H).serve_forever()


if __name__ == "__main__":
    main()
