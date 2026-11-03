# Building Opt-In Message

How every version of this app gets built, in plain language. If you just
want to *use* Opt-In Message, grab an installer from the Releases page —
or try the live sandbox at
<https://optin-message.shawonshovon687.workers.dev>. This file is for
people who want to rebuild it themselves.

There are three ways to run the same app:

1. **Desktop and mobile installers** — built by GitHub Actions, never by
   hand on a developer machine.
2. **Docker** — one image that runs the web app anywhere Docker runs.
3. **Plain Python** — the app is standard-library only, so
   `python3 app.py` is the whole build.

---

## 1. Releases: tag a version, get installers

Releases are produced by `.github/workflows/release.yml`. Nobody compiles
installers locally; the workflow is the factory.

```bash
# bump "version" in app/src-tauri/tauri.conf.json and app/src-tauri/Cargo.toml,
# bump VERSION in app.py, commit, then:
git tag vX.Y.Z
git push origin vX.Y.Z
```

That push triggers the pipeline:

1. **Quality gates** (Ubuntu) — the 49 Python engine tests, a credential
   grep over the tree, and a gitleaks scan. If any of these go red, the
   release stops there. No installers are built from a failing tree.
2. **Desktop jobs** (Windows, macOS ×2, Ubuntu) — each job first freezes
   the Python engine into a standalone binary (next section), then
   `tauri-action` builds the Tauri app and attaches the installers to a
   release. Releases land as *prerelease* on purpose — a human checks
   the asset list, then flips the good one to Latest.
3. **Android job** (Ubuntu, allowed to fail without blocking the rest) —
   builds the APK, signs it, and attaches it to the same release.

### The built-in engine (why installers need no Python)

The desktop app is a Tauri 2 shell around the Python web app. End users
should not have to install Python, so each desktop CI job runs
PyInstaller over `engine_main.py` and produces a single `optin-engine`
binary, placed in `app/src-tauri/binaries/` with the target triple in
its name and bundled via `bundle.externalBin` in `tauri.conf.json`.

At launch the shell tries, in order:

1. the bundled `optin-engine` binary,
2. system Python 3 running the bundled `.py` sources (they also ship as
   resources),
3. on Windows only, a private Python runtime the welcome screen offers
   to fetch with one click ("auto-setup"),
4. the welcome screen itself, showing an honest status if nothing
   could start.

One quirk worth knowing: the macOS x86_64 job runs on an Apple Silicon
runner, so the engine frozen in that job is actually arm64 code. The
shell notices the dead binary and falls back to system Python there.

`engine_main.py` is a thin wrapper: it defaults `DATABASE_PATH` to a
per-user data folder (the install directory is read-only) and then runs
the same server as `app.py`.

### Windows

Produced on `windows-latest`: an MSI
(`Opt-In Message_<version>_x64_en-US.msi`) and an NSIS installer
(`..._x64-setup.exe`). Nothing special to know beyond the tag push —
Tauri's bundler does both from the same build.

### macOS

Produced on `macos-latest` for both Apple Silicon (`aarch64.dmg`) and
Intel (`x64.dmg`), plus `.app.tar.gz` archives. The Intel engine caveat
above is the only oddity.

### Linux

Produced on `ubuntu-22.04`: an AppImage and a `.deb`. The job installs
the WebKitGTK development packages first; if you ever build the shell
locally on Debian/Ubuntu you need the same set:

```bash
sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev \
  libayatana-appindicator3-dev librsvg2-dev libssl-dev
```

### Android — and the override file that makes it work

The Android job runs `cargo tauri android init`, builds a release APK
for arm64, then signs it with a throwaway key generated in CI (a release
APK straight out of the builder is unsigned, and Android refuses those).
Because the signing key is ephemeral, Android asks the user to allow
the install — fine for a beta sideload, which is what this is. The APK
asset is named `optin-message-android.apk`.

The part that cost real debugging time:
**`app/src-tauri/tauri.android.conf.json` must stay in the repo.** The
desktop config declares the engine binary under `bundle.externalBin`,
and Tauri's build checks that a sidecar exists for *every* target —
including Android, where no engine binary can ever exist (Python cannot
spawn there). Without the override, `tauri-build` aborts with
``resource path `binaries/optin-engine-aarch64-linux-android` doesn't exist``.
The Android config file simply empties `externalBin` for that platform:

```json
{ "bundle": { "externalBin": [] } }
```

Tauri merges it over the main config for Android builds only. If a
future cleanup deletes it as "unused", Android breaks again.

The Android shell is a launcher/viewer: it shows the welcome page, but
the engine cannot run on a phone. For the real app on mobile, run the
engine on a desktop or server and use the PWA it serves.

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
