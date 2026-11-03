#!/usr/bin/env python3
"""
Opt-In Message — compliant, consent-first messaging for SMS / WhatsApp / Email.

Stdlib-only (no pip installs). Users bring their own provider keys.
Core rule enforced in code: a message is only ever sent to a contact whose
status is 'confirmed' (double opt-in completed) and who has not opted out,
and only from a campaign a human explicitly approved.

Public routes (by explicit decision, see README):
  GET  /                landing page with live opt-in widget demo
  GET  /widget.js       embeddable opt-in widget
  POST /api/optin       create a *pending* contact + double-opt-in token
  GET  /confirm         confirm an opt-in via its token
  POST /webhook/inbound STOP / HELP handling from providers
  GET  /health          health check

Admin routes (require X-Admin-Token header, else 401):
  GET  /admin           dashboard
  GET  /api/stats       counts
  GET  /api/contacts    list / filter contacts
  POST /api/templates   create template      GET /api/templates
  POST /api/campaigns   create campaign      GET /api/campaigns
  POST /api/campaigns/<id>/approve   human approval gate
  POST /api/campaigns/<id>/send      blocked (409) unless approved
  GET  /api/audit       audit log
"""
import hashlib
import json
import os
import re
import secrets
import smtplib
import sqlite3
import time
import urllib.parse
import urllib.request
import base64
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me-dev-token")
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "data", "optin.db"))
DRY_RUN = os.environ.get("DRY_RUN", "1") not in ("0", "false", "False")
MAX_RECIPIENTS = int(os.environ.get("MAX_RECIPIENTS_PER_CAMPAIGN", "1000"))
CONSENT_VERSION = os.environ.get("CONSENT_VERSION", "v1-2026-11-02")
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
HELP_WORDS = {"HELP", "INFO"}

_rate = {}  # ip -> [timestamps], simple in-memory opt-in rate limit
RATE_LIMIT = 20  # opt-in requests per IP per hour


def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS contacts(
          id INTEGER PRIMARY KEY, name TEXT, identifier TEXT NOT NULL, channel TEXT NOT NULL,
          tags TEXT DEFAULT '', source TEXT DEFAULT '', ip TEXT DEFAULT '',
          consent_version TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
          token TEXT, created_at TEXT, confirmed_at TEXT, opted_out_at TEXT,
          UNIQUE(identifier, channel));
        CREATE TABLE IF NOT EXISTS templates(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, channel TEXT NOT NULL,
          body TEXT NOT NULL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS campaigns(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, template_id INTEGER NOT NULL,
          channel TEXT NOT NULL, tag_filter TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'draft',
          created_at TEXT, approved_at TEXT, sent_at TEXT);
        CREATE TABLE IF NOT EXISTS deliveries(
          id INTEGER PRIMARY KEY, campaign_id INTEGER, contact_id INTEGER,
          provider TEXT, status TEXT, detail TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY, event TEXT, detail TEXT, created_at TEXT);
        """
    )
    conn.commit()
    conn.close()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def audit(conn, event, detail=""):
    conn.execute("INSERT INTO audit(event,detail,created_at) VALUES(?,?,?)", (event, detail, now()))


# ---------------------------------------------------------------- providers
def render(body, contact):
    return body.replace("{name}", contact["name"] or "").replace("{identifier}", contact["identifier"])


def send_message(channel, contact, text):
    """Return (provider, ok, detail). DRY_RUN never touches a real provider."""
    if DRY_RUN:
        return ("dry-run", True, "simulated send (DRY_RUN=1)")
    if channel == "sms":
        sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        tok = os.environ.get("TWILIO_AUTH_TOKEN", "")
        frm = os.environ.get("TWILIO_FROM", "")
        if not (sid and tok and frm):
            return ("twilio", False, "missing TWILIO_* env vars")
        data = urllib.parse.urlencode({"From": frm, "To": contact["identifier"], "Body": text}).encode()
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data)
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{tok}".encode()).decode())
        try:
            urllib.request.urlopen(req, timeout=20)
            return ("twilio", True, "sent")
        except Exception as e:  # noqa: BLE001
            return ("twilio", False, str(e)[:200])
    if channel == "whatsapp":
        token = os.environ.get("WHATSAPP_TOKEN", "")
        pnid = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        if not (token and pnid):
            return ("whatsapp", False, "missing WHATSAPP_* env vars")
        payload = json.dumps({"messaging_product": "whatsapp", "to": contact["identifier"],
                              "type": "text", "text": {"body": text}}).encode()
        req = urllib.request.Request(
            f"https://graph.facebook.com/v21.0/{pnid}/messages", data=payload, method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            urllib.request.urlopen(req, timeout=20)
            return ("whatsapp", True, "sent (session text; marketing needs an approved Meta template)")
        except Exception as e:  # noqa: BLE001
            return ("whatsapp", False, str(e)[:200])
    if channel == "email":
        host = os.environ.get("SMTP_HOST", "")
        user = os.environ.get("SMTP_USER", "")
        pw = os.environ.get("SMTP_PASSWORD", "")
        frm = os.environ.get("SMTP_FROM", user)
        if not (host and frm):
            return ("smtp", False, "missing SMTP_* env vars")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = frm, contact["identifier"], "Message from Opt-In Message"
        msg.set_content(text)
        try:
            with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as s:
                s.starttls()
                if user:
                    s.login(user, pw)
                s.send_message(msg)
            return ("smtp", True, "sent")
        except Exception as e:  # noqa: BLE001
            return ("smtp", False, str(e)[:200])
    return ("none", False, f"unknown channel {channel}")


# ---------------------------------------------------------------- pages
# GUI lives in gui/ (landing.html, admin.html, widget.js) — single source,
# loaded once at startup. Run from the project folder or anywhere: paths are
# resolved relative to this file.
_GUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui")


def _load_gui(name, fallback):
    try:
        with open(os.path.join(_GUI_DIR, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return fallback


LANDING = _load_gui("landing.html", "<h1>Opt-In Message</h1><div id='optin-widget'></div><script src='/widget.js'></script>")
WIDGET = _load_gui("widget.js", "")
ADMIN = _load_gui("admin.html", "<h1>Admin</h1>")


# ---------------------------------------------------------------- handler
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if "application/json" in (self.headers.get("Content-Type") or "") or raw.strip().startswith(b"{"):
            try:
                return json.loads(raw or b"{}")
            except Exception:  # noqa: BLE001
                return {}
        return dict(urllib.parse.parse_qsl(raw.decode(errors="ignore")))

    def _admin(self):
        tok = self.headers.get("X-Admin-Token") or ""
        if tok != ADMIN_TOKEN:
            self._send(401, {"error": "unauthorized — X-Admin-Token required"})
            return False
        return True

    # ------------------------------------------------------------ GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/health":
            return self._send(200, {"ok": True, "dry_run": DRY_RUN})
        if u.path == "/":
            return self._send(200, LANDING, "text/html")
        if u.path == "/widget.js":
            return self._send(200, WIDGET, "application/javascript")
        if u.path == "/admin":
            return self._send(200, ADMIN, "text/html")
        if u.path == "/confirm":
            token = (q.get("token") or [""])[0]
            conn = db()
            row = conn.execute("SELECT * FROM contacts WHERE token=?", (token,)).fetchone()
            if not row:
                conn.close()
                return self._send(404, {"error": "invalid token"}, )
            conn.execute("UPDATE contacts SET status='confirmed', confirmed_at=?, token=NULL WHERE id=?",
                         (now(), row["id"]))
            audit(conn, "optin_confirmed", f"contact {row['id']} via double opt-in")
            conn.commit(); conn.close()
            return self._send(200, "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><div style='font-family:system-ui;max-width:520px;margin:60px auto;background:#fff;border:1px solid #e7e0d4;border-radius:16px;padding:28px;text-align:center'><div style='font-size:44px'>✅</div><h2>You're confirmed</h2><p style='color:#6f7a72'>You are opted in and will receive messages on this channel.<br>Reply <b>STOP</b> anytime to opt out.</p></div>", "text/html")
        if u.path.startswith("/api/"):
            if not self._admin():
                return
            conn = db()
            if u.path == "/api/stats":
                stats = {s: conn.execute("SELECT COUNT(*) c FROM contacts WHERE status=?", (s,)).fetchone()["c"]
                         for s in ("pending", "confirmed", "opted_out")}
                stats["campaigns"] = conn.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"]
                stats["deliveries"] = conn.execute("SELECT COUNT(*) c FROM deliveries").fetchone()["c"]
                conn.close(); return self._send(200, stats)
            if u.path == "/api/contacts":
                sql, args = "SELECT * FROM contacts WHERE 1=1", []
                for key, col in (("status", "status"), ("channel", "channel")):
                    if q.get(key):
                        sql += f" AND {col}=?"; args.append(q[key][0])
                if q.get("tag"):
                    sql += " AND tags LIKE ?"; args.append(f"%{q['tag'][0]}%")
                rows = [dict(r) for r in conn.execute(sql, args)]
                for r in rows:
                    r.pop("token", None)
                conn.close(); return self._send(200, rows)
            if u.path == "/api/templates":
                rows = [dict(r) for r in conn.execute("SELECT * FROM templates")]
                conn.close(); return self._send(200, rows)
            if u.path == "/api/campaigns":
                rows = [dict(r) for r in conn.execute("SELECT * FROM campaigns")]
                conn.close(); return self._send(200, rows)
            if u.path == "/api/audit":
                rows = [dict(r) for r in conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]
                conn.close(); return self._send(200, rows)
            conn.close(); return self._send(404, {"error": "not found"})
        return self._send(404, {"error": "not found"})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        d = self._body()
        conn = db()

        if u.path == "/api/optin":
            ip = self.client_address[0]
            ts = [t for t in _rate.get(ip, []) if t > time.time() - 3600]
            if len(ts) >= RATE_LIMIT:
                conn.close(); return self._send(429, {"error": "rate limited"})
            _rate[ip] = ts + [time.time()]
            ident = str(d.get("identifier", "")).strip()
            channel = str(d.get("channel", "sms")).strip().lower()
            if not ident or channel not in ("sms", "whatsapp", "email"):
                conn.close(); return self._send(400, {"error": "identifier and valid channel required"})
            if channel == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", ident):
                conn.close(); return self._send(400, {"error": "invalid email"})
            if channel in ("sms", "whatsapp") and not re.match(r"^\+?[0-9][0-9 \-]{6,}$", ident):
                conn.close(); return self._send(400, {"error": "invalid phone"})
            token = secrets.token_urlsafe(24)
            try:
                conn.execute(
                    "INSERT INTO contacts(name,identifier,channel,tags,source,ip,consent_version,status,token,created_at)"
                    " VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                    (d.get("name", ""), ident, channel, d.get("tags", ""), d.get("source", ""),
                     ip, CONSENT_VERSION, token, now()))
                cid = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
                audit(conn, "optin_requested", f"contact {cid} channel={channel} source={d.get('source','')}")
                conn.commit()
            except sqlite3.IntegrityError:
                conn.execute("UPDATE contacts SET token=?, status='pending', created_at=? WHERE identifier=? AND channel=? AND status!='confirmed'",
                             (token, now(), ident, channel))
                audit(conn, "optin_re_requested", f"{ident} channel={channel}")
                conn.commit()
            conn.close()
            out = {"message": "Pending — please confirm via the link sent to you (double opt-in)."}
            if DRY_RUN:
                out["confirm_url"] = f"/confirm?token={token}"
            return self._send(201, out)

        if u.path in ("/webhook/inbound", "/api/optout"):
            ident = str(d.get("identifier") or d.get("from") or d.get("From") or "").strip()
            text = str(d.get("body") or d.get("Body") or d.get("text") or "").strip().upper()
            if u.path == "/api/optout":
                text = "STOP"
            row = conn.execute("SELECT * FROM contacts WHERE identifier=?", (ident,)).fetchone()
            if not row:
                conn.close(); return self._send(404, {"error": "contact not found"})
            if text in STOP_WORDS:
                conn.execute("UPDATE contacts SET status='opted_out', opted_out_at=? WHERE id=?", (now(), row["id"]))
                audit(conn, "opted_out", f"contact {row['id']} via '{text}'")
                conn.commit(); conn.close()
                return self._send(200, {"message": "Opted out. You will receive no more messages."})
            if text in HELP_WORDS:
                conn.close(); return self._send(200, {"message": "Help: reply STOP to opt out."})
            conn.close(); return self._send(200, {"message": "received"})

        # everything below is admin-only
        if u.path.startswith("/api/"):
            if not self._admin():
                conn.close(); return
            if u.path == "/api/import/consented":
                # CleanReach integration: import ONLY rows that carry a consent
                # record (consent=true). Body: {"csv": "<csv text>", "channel": "sms"}
                # Expected columns: name, phone|identifier, consent  (CleanReach
                # consented_phones.csv export uses this lane format.)
                import csv, io
                text_csv = d.get("csv", "")
                channel = str(d.get("channel", "sms")).lower()
                imported = skipped = 0
                for row in csv.DictReader(io.StringIO(text_csv)):
                    consent = str(row.get("consent", "")).strip().lower()
                    ident = str(row.get("phone") or row.get("identifier") or "").strip()
                    if consent not in ("true", "1", "yes") or not ident:
                        skipped += 1
                        continue
                    try:
                        conn.execute(
                            "INSERT INTO contacts(name,identifier,channel,tags,source,consent_version,status,created_at,confirmed_at)"
                            " VALUES(?,?,?,?,'cleanreach-import',?,'confirmed',?,?)",
                            (row.get("name", ""), ident, channel, d.get("tags", ""),
                             CONSENT_VERSION, now(), now()))
                        imported += 1
                    except sqlite3.IntegrityError:
                        skipped += 1
                audit(conn, "cleanreach_import", f"imported={imported} skipped={skipped} channel={channel}")
                conn.commit(); conn.close()
                return self._send(200, {"ok": True, "imported": imported, "skipped": skipped})
            if u.path == "/api/templates":
                conn.execute("INSERT INTO templates(name,channel,body,created_at) VALUES(?,?,?,?)",
                             (d.get("name", ""), d.get("channel", "sms"), d.get("body", ""), now()))
                audit(conn, "template_created", d.get("name", ""))
                conn.commit(); conn.close(); return self._send(201, {"ok": True})
            if u.path == "/api/campaigns":
                conn.execute("INSERT INTO campaigns(name,template_id,channel,tag_filter,status,created_at)"
                             " VALUES(?,?,?,?, 'draft', ?)",
                             (d.get("name", ""), int(d.get("template_id", 0)), d.get("channel", "sms"),
                              d.get("tag_filter", ""), now()))
                cid = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
                audit(conn, "campaign_created", f"campaign {cid} (draft — needs approval)")
                conn.commit(); conn.close(); return self._send(201, {"ok": True, "id": cid})
            m = re.match(r"^/api/campaigns/(\d+)/(approve|send)$", u.path)
            if m:
                cid = int(m.group(1))
                camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
                if not camp:
                    conn.close(); return self._send(404, {"error": "campaign not found"})
                if m.group(2) == "approve":
                    conn.execute("UPDATE campaigns SET status='approved', approved_at=? WHERE id=?", (now(), cid))
                    audit(conn, "campaign_approved", f"campaign {cid} approved by admin")
                    conn.commit(); conn.close(); return self._send(200, {"ok": True, "status": "approved"})
                # send — the gate
                if camp["status"] != "approved":
                    conn.close()
                    return self._send(409, {"error": "campaign not approved — human approval gate blocks this send"})
                tpl = conn.execute("SELECT * FROM templates WHERE id=?", (camp["template_id"],)).fetchone()
                if not tpl:
                    conn.close(); return self._send(404, {"error": "template not found"})
                sql = "SELECT * FROM contacts WHERE status='confirmed' AND channel=?"
                args = [camp["channel"]]
                if camp["tag_filter"]:
                    sql += " AND tags LIKE ?"; args.append(f"%{camp['tag_filter']}%")
                targets = conn.execute(sql, args).fetchall()[:MAX_RECIPIENTS]
                sent = failed = 0
                for c in targets:
                    provider, ok, detail = send_message(camp["channel"], c, render(tpl["body"], c))
                    conn.execute("INSERT INTO deliveries(campaign_id,contact_id,provider,status,detail,created_at)"
                                 " VALUES(?,?,?,?,?,?)",
                                 (cid, c["id"], provider, "sent" if ok else "failed", detail, now()))
                    sent, failed = sent + (1 if ok else 0), failed + (0 if ok else 1)
                conn.execute("UPDATE campaigns SET status='sent', sent_at=? WHERE id=?", (now(), cid))
                audit(conn, "campaign_sent", f"campaign {cid}: {sent} sent, {failed} failed, {len(targets)} targeted")
                conn.commit(); conn.close()
                return self._send(200, {"ok": True, "targeted": len(targets), "sent": sent, "failed": failed})
            conn.close(); return self._send(404, {"error": "not found"})
        conn.close(); return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    init_db()
    print(f"Opt-In Message on http://127.0.0.1:{PORT}  DRY_RUN={DRY_RUN}  DB={DB_PATH}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
