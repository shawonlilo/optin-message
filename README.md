<p align="center"><img src="app/branding/logo.png" width="140" alt="Opt-In Message logo"></p>

# 📩 Opt-In Message

**Consent-first messaging for SMS, WhatsApp & Email — double opt-in, proof logged, STOP honoured instantly.**

No consent = no send. That rule is enforced in code, not in a policy page:
contacts must complete double opt-in before they can receive anything,
campaigns cannot send until a human approves them, and anyone who replies
STOP is excluded from every future send — automatically.

Free & open source (MIT). Self-host it anywhere Python runs. Bring your own
provider keys (Twilio / Meta / SMTP) — there is no per-contact pricing and
nobody sits between you and your audience.

**▶ Try it live (sandbox — no install, no real messages sent):**
<https://optin-message.shawonshovon687.workers.dev>
A safe demo running on Cloudflare: sign up through the opt-in form, look
at the dashboard, draft a campaign. Sending is simulated there, so you
can click everything without worrying.

## 💻 Desktop & Mobile Installers (built via CI)

Opt-In Message ships with the same native-app packaging as the rest of
the suite (CleanReach · Network Diagnostics · Cloud Drive Bridge) — and
since v0.6.0, **the engine is built in.** Each desktop CI job
freezes the Python engine (`engine_main.py`) with PyInstaller into a
standalone `optin-engine` binary bundled as a Tauri sidecar, so the
desktop app just works — **no Python installation needed.** The shell's
launch order is: bundled engine binary → system Python 3 running the
bundled sources → (Windows) a private runtime fetched by the welcome
screen's one-click **auto-setup** → the welcome screen with an honest
status. The welcome screen is a real product page — logo, feature cards,
a live engine status pill driven by a real Rust `app_info` invoke, and
native options: **Restart engine**, **Open data folder** and **Copy
dashboard URL**.

Installers are produced by `.github/workflows/release.yml` when a version
tag is pushed (nothing is compiled on a developer machine):

| Platform | Assets CI produces |
|---|---|
| Windows | `Opt-In Message_0.6.1_x64_en-US.msi` + `Opt-In Message_0.6.1_x64-setup.exe` (NSIS) |
| macOS | `Opt-In Message_0.6.1_aarch64.dmg` + `Opt-In Message_0.6.1_x64.dmg` (+ `.app.tar.gz`) |
| Linux / Debian | `Opt-In Message_0.6.1_amd64.AppImage` + `Opt-In Message_0.6.1_amd64.deb` |
| Android | `optin-message-android.apk` (experimental, signed with an ephemeral CI key; the Android shell is a viewer — the engine cannot spawn on Android, use the PWA there) |
| Anywhere | the source zip on the release — unzip and `python3 app.py` (Python 3 is only needed for this source-zip path, not for the installers) |

The release workflow runs the quality gates first (49 Python engine tests
+ credential grep + gitleaks) — a red gate means no installers.

Want to rebuild any of this yourself? [BUILDING.md](BUILDING.md) walks
through every platform, the Android quirk, and the Docker image.

## 🆕 v0.4.0 — Hackathon Cut

- **👤 User accounts** — register / log in with email + password
  (pbkdf2_hmac hashing with per-user salt, HttpOnly session cookies).
  The dashboard works with a login session **or** the classic
  `X-Admin-Token` header — both are accepted on every admin route.
- **✨ AI Studio** (new dashboard tab) — three working tools:
  - **Draft** — generate a campaign message from a goal, per channel/tone/language
  - **Translate** — EN ↔ বাংলা (Bangla) for marketing copy
  - **Compliance check** — a real rule-based spam/ban-risk score (0–100)
    with itemised findings (ALL-CAPS ratio, spam trigger words, urgency,
    links, money claims, missing opt-out line, length) **and a safer rewrite**
  - Engine honesty: set `META_AI_API_KEY` and the tools call the
    **Meta Model API** (Muse Spark); with no key, a genuinely functional
    local engine (templates + phrasebook translator + rule scorer) runs
    instead. Every response carries an `engine` field — `"meta-ai"` or
    `"local"` — the source is never faked.
- **📥 Shared Inbox** — inbound replies (non-STOP/HELP) are stored and
  listed; you can reply from the dashboard (recorded as a dry-run
  delivery — nothing real sends while `DRY_RUN=1`).
- **📊 Analytics** — contacts by status/channel, campaigns and deliveries
  by status, opt-in/opt-out counts and delivery rate, with CSS bar charts.
- **📱 PWA** — `manifest.json` + service worker; install the same app on
  Android, macOS, Windows or any browser. One codebase, every platform.
- **🌱 Demo seed** — `python3 seed_demo.py` fills a demo workspace for a
  fictional Dhaka clothing shop, **Dhaka Threads** (idempotent).

### 🎬 Demo account (for judges)

After `python3 seed_demo.py`, log in at `/login` with:

- **Email:** `demo@dhakathreads.shop`
- **Password:** `DemoPass2026`

This is a **public demo account inside your local demo database only** —
created so judges can explore instantly. It is not a real credential;
change/remove it before any real deployment.

## 🖥️ GUI — V1 Essential Light

A real dashboard — no JSON, no command line needed. Warm off-white
surfaces (#FAF8F4), white cards with soft depth, ink text and a mint
accent. Light, calm, and easy to read for hours.

- **Overview** — live stat cards, template builder, campaign builder
- **Contacts** — filterable table (status, channel, tag) with status badges
- **Campaigns** — one-click **Approve** and **Send now** (with confirmation); the approval gate is visible
- **✨ AI Studio** — draft / translate / compliance-check with an engine badge
- **📥 Inbox** — subscriber replies with one-click dry-run reply
- **📊 Analytics** — totals, rates and bar charts
- **Import from CleanReach** — only rows with a real consent record are accepted
- **Audit Log** — every opt-in, approval, send, opt-out, login and AI action
- Polished landing page with a 3-step explainer and the styled live widget

Run it: `python3 app.py` → open `http://127.0.0.1:8080/`, sign in at
`/login`, and use the dashboard at `/admin`.

## ✨ Features

- **Embeddable opt-in widget** (`/widget.js`) + landing form
- **Double opt-in** with a full consent proof trail (time, source, IP, consent version)
- **Human approval gate** — campaigns are drafts; send returns `409` until approved
- **STOP / HELP webhook** — opt-outs processed instantly and permanently; other replies land in the Inbox
- **Segments & templates** — filter by tag/channel/status, `{name}` personalisation
- **3 channels, your keys** — Twilio SMS, Meta WhatsApp Cloud API, SMTP email
- **Dry-run mode by default** — test the whole flow with zero provider spend
- **User auth + audit log** for every significant event
- **CleanReach integration** — import CleanReach's consented-phones export
- **Zero dependencies** — Python 3 standard library only, SQLite storage

## 🚀 Quick start

```bash
cp .env.example .env   # set ADMIN_TOKEN (and provider keys when ready)
set -a; source .env; set +a
python3 app.py              # http://127.0.0.1:8080
python3 seed_demo.py        # optional: Dhaka Threads demo data + demo login
python3 tests/test_e2e.py   # full end-to-end test suite (49 checks)
```

### 🐳 Or with Docker

No Python on the host? The repo ships a `Dockerfile` and
`docker-compose.yml`:

```bash
docker compose up -d        # builds the image and starts the app
# open http://127.0.0.1:8080/ — dashboard at /admin
```

The database lives in a named volume, so your contacts survive restarts
and rebuilds. The container starts with `DRY_RUN=1`: look around, test
the whole flow, and only wire real provider keys when you are ready.
Details in [BUILDING.md](BUILDING.md).

## 🔑 Bring your own AI key (BYOK)

AI Studio works with **your own Meta Model API key** — two ways to add it:

**Way 1 — in the dashboard (easiest):** open **AI Studio → ⚙️ AI Settings**,
paste your key (base URL and model are prefilled with the defaults), tap
**Save**, then **Test connection**. The status line shows which engine is
live: Meta AI via your saved key, via environment, or the local engine.

**Way 2 — environment variables:**

```bash
META_AI_API_KEY=your-meta-model-api-key     # enables the Meta Model API path
META_AI_BASE_URL=https://api.meta.ai/v1     # OpenAI-compatible endpoint (default)
META_AI_MODEL=muse-spark-1.3                # default model
```

Resolution order: **dashboard-saved key → environment → local engine**.
A dashboard-saved key always wins over the environment.

**Where your key lives:** only in *your own* local database file
(`data/optin.db`, the `settings` table) on *your* server. It is never
written to any repo file or log, never appears in audit entries, and the
API only ever shows its last 4 characters back (`key_hint`). The key
leaves your server for exactly one purpose: calling the Meta API itself.

Without a key the AI Studio still works fully — on the local engine, and
says so in every response (`"engine": "local"`).

## 🔐 Routes

**Public:** `GET /`, `GET /login`, `GET /widget.js`, `GET /manifest.json`,
`GET /sw.js`, `POST /api/optin` (rate-limited), `GET /confirm?token=…`,
`POST /webhook/inbound`, `GET /health`,
`POST /api/auth/register|login|logout`, `GET /api/auth/me` (session).

**Admin (X-Admin-Token header OR login session, 401 without):** contacts,
templates, campaigns (create / approve / send), CleanReach import, audit
log, stats, `GET /api/inbox`, `POST /api/inbox/<id>/reply`,
`GET /api/analytics`, `POST /api/ai/draft|translate|check`,
`GET|POST /api/settings/ai`, `POST /api/settings/ai/test` (BYOK key).

## ⚖️ Compliance notes

Built for TCPA / GDPR / Meta WhatsApp policy style consent regimes: prior
express consent, documented proof, instant opt-out. This is software, not
legal advice — you are responsible for the consent you collect and the
messages you send. WhatsApp marketing at scale requires Meta-approved
templates on the official Cloud API.

## 🙏 Thanks

Built in the open-source spirit of consent-first tools like BrightBean Chat
and ReplyLoom, and Twilio / Meta / Python standard library documentation.

## License

MIT — see [LICENSE](LICENSE). Part of Shawon's open-source tools suite
(CleanReach · Network Diagnostics · Cloud Drive Bridge · Opt-In Message).
