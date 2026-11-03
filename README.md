# 📩 Opt-In Message

**Consent-first messaging for SMS, WhatsApp & Email — double opt-in, proof logged, STOP honoured instantly.**

No consent = no send. That rule is enforced in code, not in a policy page:
contacts must complete double opt-in before they can receive anything,
campaigns cannot send until a human approves them, and anyone who replies
STOP is excluded from every future send — automatically.

Free & open source (MIT). Self-host it anywhere Python runs. Bring your own
provider keys (Twilio / Meta / SMTP) — there is no per-contact pricing and
nobody sits between you and your audience.

## 🖥️ GUI (v0.3.0 — V1 Essential Light)

A real dashboard — no JSON, no command line needed. v0.3.0 wears the
**V1 Essential light** design chosen for the suite: warm off-white surfaces,
white cards with soft depth, ink text and a mint accent — premium light,
never plain-white sloppy.

- **Overview** — live stat cards (confirmed / pending / opted-out / campaigns / deliveries), template builder, campaign builder
- **Contacts** — filterable table (status, channel, tag) with status badges
- **Campaigns** — one-click **Approve** and **Send now** (with confirmation), status badges; the approval gate is visible, not hidden
- **Import from CleanReach** — pick the consented_phones.csv file or paste it; imported/skipped counts shown
- **Audit Log** — every opt-in, approval, send and opt-out, in a table
- Polished landing page with a 3-step explainer and the styled live widget

Run it: `python3 app.py` → open `http://127.0.0.1:8080/` and `/admin` (enter your `ADMIN_TOKEN` once, it's remembered in your browser).

## ✨ Features

- **Embeddable opt-in widget** (`/widget.js`) + landing form
- **Double opt-in** with a full consent proof trail (time, source, IP, consent version)
- **Human approval gate** — campaigns are drafts; send returns `409` until approved
- **STOP / HELP webhook** — opt-outs processed instantly and permanently
- **Segments & templates** — filter by tag/channel/status, `{name}` personalisation
- **3 channels, your keys** — Twilio SMS, Meta WhatsApp Cloud API, SMTP email
- **Dry-run mode by default** — test the whole flow with zero provider spend
- **Audit log** for every opt-in, approval, send and opt-out
- **CleanReach integration** — import CleanReach's consented-phones export;
  only rows with a real consent record are accepted
- **Zero dependencies** — Python 3 standard library only, SQLite storage

## 🚀 Quick start

```bash
cp .env.example .env   # set ADMIN_TOKEN (and provider keys when ready)
set -a; source .env; set +a
python3 app.py         # http://127.0.0.1:8080
python3 tests/test_e2e.py   # full end-to-end test
```

Open `/` for the live widget demo, `/admin` for the dashboard
(enter your `ADMIN_TOKEN` there).

## 🔐 Routes

**Public (by explicit decision):** `GET /`, `GET /widget.js`, `POST /api/optin`
(rate-limited), `GET /confirm?token=…`, `POST /webhook/inbound`, `GET /health`.

**Admin (`X-Admin-Token` header required, 401 without):** contacts, templates,
campaigns (create / approve / send), CleanReach import, audit log, stats.

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
