<p align="center"><img src="app/branding/logo.png" width="140" alt="Opt-In Message logo"></p>

# 📩 Opt-In Message — Simple App

**Download for your platform, install, open. No Python. No server. No setup. Your data stays on your device.**

Opt-In Message keeps your messaging consent-first: people opt in, confirm
(double opt-in, with the proof saved), and can STOP at any time — forever.
Campaigns are checked for spam/ban risk and stay locked until a human
approves them. The whole app runs on your device; there is no account
and nothing runs in the background.

Free & open source (MIT).

## ⬇️ Download (v0.7.0)

Grab your file from the **[v0.7.0 release](https://github.com/shawonlilo/optin-message/releases/tag/v0.7.0)**:

| Platform | File to download |
|---|---|
| Windows | `Opt-In.Message_0.7.0_x64_en-US.msi` (or the `_x64-setup.exe`) |
| macOS (Apple Silicon) | `Opt-In.Message_0.7.0_aarch64.dmg` |
| macOS (Intel) | `Opt-In.Message_0.7.0_x64.dmg` |
| Linux | `Opt-In.Message_0.7.0_amd64.AppImage` (or the `.deb`) |
| Android | `optin-message-android.apk` (arm64, CI-signed — allow the sideload install) |

Install it, open it, use it. That is the whole setup.

**▶ Or try it live first (sandbox — no install, no real messages sent):**
<https://optin-message.shawonshovon687.workers.dev>
A safe demo running on Cloudflare: sign up through the opt-in form, look
at the dashboard, draft a campaign. Sending is simulated there, so you
can click everything without worrying.

## 📱 What the app does

- **Opt-in form** — name, phone/email, channel (WhatsApp / SMS / Email)
  and an explicit consent checkbox. Subscribing is step 1: the contact
  starts as **Pending**.
- **Double opt-in** — a **Confirm** action simulates the customer
  replying YES. Only then are they **Subscribed**, with a consent proof
  saved: timestamp, method, and the exact consent text/version.
- **Contacts list** — status badges (Subscribed / Pending / Stopped),
  search and status filter, proof shown per contact.
- **STOP is permanent** — stopping works from the STOP form or the list.
  A stopped contact can never be messaged again, and re-subscribing the
  same address is blocked with a clear message.
- **Campaigns with a human approval gate** — write a message and a plain
  rule-based check scores its spam/ban risk as you type (ALL-CAPS,
  trigger words, links, urgency, missing opt-out line), with flags and a
  safer rewrite you can apply in one click. A saved campaign is
  **waiting for approval** and cannot send until a person approves it.
- **Simulated sending** — "Send" writes a delivery log entry per
  Subscribed contact. Pending and Stopped contacts are always skipped.
  No real messages ever leave this app.
- **Audit log** — every opt-in, confirmation, STOP, campaign, approval
  and send is recorded with a timestamp. That paper trail is the proof
  consent was respected.
- **Demo data, clearly labelled** — first run loads 3 demo contacts
  (Dhaka Threads, a fictional shop) so the app is never empty. Reset or
  clear them any time from the Contacts screen.

Everything is stored only in the app's own local storage on your device.

## 🛠️ How it is built (for the curious)

The app is a thin native shell (Tauri 2, `app/`) around one static
frontend (`app/ui/index.html` + `app/ui/app.js`). No build step, no
bundled engine, no local server, no background process — the installers
are produced by GitHub Actions (`.github/workflows/release.yml`) when a
version tag is pushed. [BUILDING.md](BUILDING.md) explains rebuilding
every platform, including Android and the Docker image for the server
edition below.

---

## 🧑‍💻 Developer / self-hosted server edition (optional)

*Most people should stop reading here — the app above is the product.*
*This section is the older, optional edition for developers who want
real sending from their own server.*

The repo root also carries a zero-dependency Python server edition
(`app.py` + `ai.py`, SQLite, dry-run by default): a web dashboard with
user accounts, a shared inbox, analytics, an AI Studio (Meta Model API /
Muse Spark when a key is set, an honest local engine otherwise), and
real delivery through your own provider keys — Twilio SMS, Meta
WhatsApp Cloud API, SMTP email. Same consent rules, enforced server-side:
double opt-in with a logged proof trail, a human approval gate (send
returns `409` until approved), and STOP honoured instantly and
permanently.

Run it:

```bash
python3 seed_demo.py   # optional: demo workspace "Dhaka Threads"
python3 app.py         # → http://127.0.0.1:8080/  (dashboard at /admin)
```

Demo account after seeding: `demo@dhakathreads.shop` / `DemoPass2026`
(a public demo login inside your local demo database only — change or
remove it before any real deployment).

Or with Docker:

```bash
docker compose up -d   # same app, containerised; DRY_RUN stays on
```

Details, the full file map and the platform build notes live in
[BUILDING.md](BUILDING.md) and [DETAILED_PLAN.md](DETAILED_PLAN.md).

## 🙏 Thanks

Built with Tauri, and with patterns proven across the OpenTools suite
(CleanReach · Network Diagnostics · Cloud Drive Bridge). The sandbox
edition runs on Cloudflare Workers.

## License

MIT — see [LICENSE](LICENSE).
