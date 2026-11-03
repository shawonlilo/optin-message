# Building Opt-In Message

How every version of this app gets built, in plain language. If you just
want to *use* Opt-In Message, grab an installer from the Releases page —
or try the live sandbox at
<https://optin-message.shawonshovon687.workers.dev>. This file is for
people who want to rebuild it themselves.

There are three ways to run Opt-In Message:

1. **The Simple App (the product, since v0.7.0)** — native installers
   built by GitHub Actions, never by hand on a developer machine. The
   app is a thin Tauri 2 shell around one static frontend
   (`app/ui/index.html` + `app/ui/app.js`): the whole product runs
   on-device in the webview with local storage. No engine, no local
   server, no background process, nothing to install first.
2. **Docker** — one image that runs the *server edition* web app
   anywhere Docker runs.
3. **Plain Python** — the optional server edition is standard-library
   only, so `python3 app.py` is the whole build.

---

## 1. Releases: tag a version, get installers

Releases are produced by `.github/workflows/release.yml`. Nobody compiles
installers locally; the workflow is the factory.

```bash
# bump "version" in app/src-tauri/tauri.conf.json and app/src-tauri/Cargo.toml,
# commit, then tag vX.Y.Z and push the tag (this repo ships via the
# Composio GitHub route — see the maintainer notes).
```

That tag triggers the pipeline:

1. **Quality gates** (Ubuntu) — `node --check` on the app JS, a static
   smoke check (UI files present, no server calls in the app), a
   credential grep over the tree, and a gitleaks scan. The Python
   server-edition tests also run, for information only — they never
   block the app release. A red hard gate stops the release: no
   installers are built from a failing tree.
2. **Desktop jobs** (Windows, macOS ×2, Ubuntu) — `tauri-action` builds
   the app from `app/` and attaches the installers to a release.
   Releases land as *prerelease* on purpose — a human checks the asset
   list, then flips the good one to Latest.
3. **Android job** (Ubuntu, allowed to fail without blocking the rest) —
   builds the APK, signs it, and attaches it to the same release. The
   Android app is the same full on-device app, not a viewer.

### Why installers need nothing installed

There is nothing to freeze or bundle: the frontend is static files and
the shell only opens a window on them. Earlier versions (≤ v0.6.1)
carried a PyInstaller-frozen Python engine as a sidecar with a
multi-step fallback launcher; v0.7.0 removed all of that — the shell's
`src/lib.rs` is a plain Tauri builder, `tauri.conf.json` declares no
`externalBin` and no bundled resources, and the old
`tauri.android.conf.json` override (which emptied `externalBin` for
Android) was deleted with the sidecar it worked around.

### Windows

Produced on `windows-latest`: an MSI
(`Opt-In Message_<version>_x64_en-US.msi`) and an NSIS installer
(`..._x64-setup.exe`). Nothing special to know beyond the tag push —
Tauri's bundler does both from the same build.

### macOS

Produced on `macos-latest` for both Apple Silicon (`aarch64.dmg`) and
Intel (`x64.dmg`), plus `.app.tar.gz` archives.

### Linux

Produced on `ubuntu-22.04`: an AppImage and a `.deb`. The job installs
the WebKitGTK development packages first; if you ever build the shell
locally on Debian/Ubuntu you need the same set:

```bash
sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev \
  libayatana-appindicator3-dev librsvg2-dev libssl-dev
```

### Android

The Android job runs `cargo tauri android init`, builds a release APK
for arm64, then signs it with a throwaway key generated in CI (a release
APK straight out of the builder is unsigned, and Android refuses those).
Because the signing key is ephemeral, Android asks the user to allow
the install — fine for a beta sideload, which is what this is. The APK
asset is named `optin-message-android.apk`.

Historical note: up to v0.6.1 this section documented the
`tauri.android.conf.json` override that emptied the desktop sidecar's
`externalBin` for Android. v0.7.0 has no sidecar, so the override file
is gone and Android builds straight from the main config.

---

## 2. Docker

The repo root has a `Dockerfile` and `docker-compose.yml` for the web
app (engine + dashboard). The image is `python:3.12-slim` plus the app
files — the app has no third-party dependencies, so there is nothing to
pip-install. It runs as a non-root user and keeps its database on a
volume.

```bash
# with compose (recommended)
docker compose up -d
docker compose logs -f        # watch it start
# open http://127.0.0.1:8080/ — dashboard at /admin

# or by hand
docker build -t optin-message .
docker run -d --name optin-message -p 8080:8080 \
  -v optin-data:/data \
  -e ADMIN_TOKEN=pick-something-better \
  optin-message
```

Configuration is environment variables, same names as `.env.example`
(`ADMIN_TOKEN`, provider keys, `META_AI_*`). Compose passes them
through from your shell or a local `.env` file, with safe defaults.

Two things to know:

- **`DRY_RUN=1` is the default everywhere.** A fresh container sends
  nothing real. Set `DRY_RUN=0` only when you have wired your own
  provider keys and mean it.
- The container sets `HOST=0.0.0.0` so the published port can reach the
  server. Plain local runs keep the safer `127.0.0.1` default.

Health: the image carries a `HEALTHCHECK` against `GET /health`, and
`docker compose ps` shows the status once it has run for a few seconds.

Stop it with `docker compose down`. Your data stays in the
`optin-data` volume until you delete the volume on purpose
(`docker compose down -v`).

---

## 3. Plain Python

The engine has zero dependencies — Python 3.10+ standard library and
SQLite, both built in.

```bash
cp .env.example .env   # set ADMIN_TOKEN (and provider keys when ready)
set -a; source .env; set +a
python3 app.py              # http://127.0.0.1:8080
python3 seed_demo.py        # optional demo data + demo login
python3 tests/test_e2e.py   # the test suite (49 checks)
```

Run the tests before tagging anything. The CI gates will catch it
anyway, but it is quicker to hear it from your own machine.
