# Opt-In Message — Full Detailed Plan (approved 2026-11-02)

Approved scope from the guardrail gate: build + ship a compliant opt-in
messaging system (SMS / WhatsApp / Email), double opt-in, consent proof,
human approval gate before any send, STOP honoured instantly, users bring
their own provider keys, open-source. Plus the standing integration decision
(2026-11-02 16:12): Opt-In Messaging stays a separate product from
CleanReach, integrated via CleanReach's "consented phones" export lane.

## What it does

1. **Collect** — embeddable widget + landing form. Name, phone/email, channel, tags.
2. **Confirm** — double opt-in: contact stays `pending` until the token link is clicked.
   Every opt-in stores proof: timestamp, source URL, IP, consent text version.
3. **Segment** — filter by tag, channel, status.
4. **Compose** — templates with `{name}` / `{identifier}` personalisation.
5. **Approve** — campaigns are born `draft`. Sending is blocked (HTTP 409)
   until an admin explicitly approves. No approval = no send, in code.
6. **Send** — only to `confirmed`, non-opted-out contacts, per-campaign cap,
   via the user's own Twilio (SMS) / Meta Cloud API (WhatsApp) / SMTP (Email).
   `DRY_RUN=1` simulates everything with zero provider spend.
7. **Respect exits** — inbound STOP/STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT opts a
   contact out workspace-wide, instantly, and they are excluded from all
   future sends. HELP returns help text.
8. **Prove it** — append-only audit log: optin_requested, optin_confirmed,
   campaign_created, campaign_approved, campaign_sent, opted_out, imports.
9. **Integrate** — `POST /api/import/consented` imports CleanReach
   `consented_phones.csv`: only rows with `consent=true` are imported,
   as already-confirmed contacts with source `cleanreach-import`.

## Expanded system design

```
                        ┌──────────────────────── PUBLIC (by decision) ────────────────────────┐
 Visitor ──► Landing / Widget.js  [this app, stdlib HTTP server — VPS or local]
                │ POST /api/optin  (rate-limited 20/IP/hr, format-validated)
                ▼
          Pending contact + token  [SQLite — alt: Postgres/D1 at scale]
                │ confirmation link sent via provider (DRY_RUN: shown for demo)
                ▼ GET /confirm?token=
          Confirmed contact  ── consent proof: time · source · IP · consent version
                        └────────────────────────────────────────────────────────────────────────┘
                        ┌──────────────────────── ADMIN (X-Admin-Token, 401 without) ──────────┐
 CleanReach ──► POST /api/import/consented  (only consent=true rows)
                        ▼
          Segments (tag / channel / status filters)
                        ▼
          Template ──► Campaign [draft] ──► ⚠️ GATE: POST /approve (human)
                                                  │ blocked 409 until approved
                                                  ▼
          Sender ──► Providers (BYO keys, env only):
                       • SMS      → Twilio API            ⚠️ missing keys = failed delivery, logged
                       • WhatsApp → Meta Cloud API        ⚠️ marketing needs approved Meta template
                       • Email    → SMTP                  ⚠️ DRY_RUN=1 default: nothing real is sent
                        ▼
          Deliveries table + Audit log ──► Admin Dashboard (/admin)
 Subscriber ──► POST /webhook/inbound  "STOP" ──► status=opted_out ──► excluded from every future send
                        └────────────────────────────────────────────────────────────────────────┘
```

Where it runs: anywhere Python 3 runs — this VM, Shawon's VPS, Docker, a laptop.
No pip dependencies (stdlib only). Storage: SQLite file (alt: Postgres/D1).
Failure points: provider key missing/rejected, WhatsApp template rejection,
rate limit hit, campaign cap hit — all logged to deliveries/audit, never silent.

## Guardrails (enforced, not promised)

- No purchased/scraped lists: the only entry paths are the opt-in form and
  the consented-only CleanReach import.
- No send to pending or opted-out contacts — query-level filter, tested.
- No send without human campaign approval — tested (409).
- Credentials only in env vars; `.env.example` ships placeholders; credential
  sweep run before any publish.
- Admin surface token-gated; public surface is only landing, widget, opt-in,
  confirm-by-token, inbound webhook, health (service-launch hardening rule).

## Roadmap after v0.1

- v0.2: scheduled campaigns, delivery-status webhooks from Twilio/Meta,
  WhatsApp template-message payloads, CSV export of consent proof.
- v0.3: Postgres/D1 storage option, multi-user admin roles, RCS channel.
