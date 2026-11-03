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

Admin routes (require X-Admin-Token header OR a logged-in session cookie,
else 401):
  GET  /admin           dashboard
  GET  /api/stats       counts
  GET  /api/contacts    list / filter contacts
  POST /api/templates   create template      GET /api/templates
  POST /api/campaigns   create campaign      GET /api/campaigns
  POST /api/campaigns/<id>/approve   human approval gate
  POST /api/campaigns/<id>/send      blocked (409) unless approved
  GET  /api/audit       audit log
  GET  /api/inbox       inbound messages     POST /api/inbox/<id>/reply
  GET  /api/analytics   totals + rates
  POST /api/ai/draft    AI Studio: campaign draft   (engine: meta-ai | local)
  POST /api/ai/translate AI Studio: EN<->BN translate
  POST /api/ai/check    AI Studio: compliance / spam-risk score
  GET  /api/settings/ai BYOK: AI config status (key shown as last-4 hint only)
  POST /api/settings/ai BYOK: save/clear own Meta AI key, base URL, model
  POST /api/settings/ai/test  BYOK: one live test call with the saved key

Auth routes (public):
  POST /api/auth/register   create account (email + password, pbkdf2 hash)
  POST /api/auth/login      set HttpOnly session cookie
  POST /api/auth/logout     clear session
  GET  /api/auth/me         current user from session cookie
  GET  /login               login page

PWA:
  GET  /manifest.json   GET /sw.js
"""
VERSION = "0.5.2"
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

import ai as ai_engine  # AI Studio engine (Meta Model API w/ local fallback)

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
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
          pw_hash TEXT NOT NULL, pw_salt TEXT NOT NULL, created_at TEXT);
        CREATE TABLE IF NOT EXISTS sessions(
          token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY, contact_id INTEGER, identifier TEXT DEFAULT '',
          channel TEXT DEFAULT 'sms', direction TEXT NOT NULL DEFAULT 'inbound',
          body TEXT DEFAULT '', status TEXT DEFAULT 'received',
          reply_to INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conn.commit()
    conn.close()


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def audit(conn, event, detail=""):
    conn.execute("INSERT INTO audit(event,detail,created_at) VALUES(?,?,?)", (event, detail, now()))


# --------------------------------------------------------------------- auth
_PBKDF2_ROUNDS = 200_000
SESSION_TTL = 7 * 24 * 3600  # 7 days


def hash_password(password, salt=None):
    """Return (hash_hex, salt_hex) using pbkdf2_hmac-sha256 with a random salt."""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", str(password).encode(),
                             bytes.fromhex(salt), _PBKDF2_ROUNDS)
    return dk.hex(), salt


def verify_password(password, pw_hash, pw_salt):
    candidate, _ = hash_password(password, pw_salt)
    return secrets.compare_digest(candidate, pw_hash)


MANIFEST = {
    "name": "Opt-In Message", "short_name": "Opt-In Message",
    "description": "Consent-first messaging for SMS, WhatsApp & Email — double opt-in, proof logged, STOP honoured instantly.",
    "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#FAF8F4", "theme_color": "#FAF8F4",
    "icons": [],
}
SERVICE_WORKER = """// Opt-In Message service worker — cache-first for the app shell.
const CACHE = 'oim-v0.5.2';
const SHELL = ['/', '/admin', '/widget.js', '/manifest.json'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request).then(res => {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(e.request, copy));
    return res;
  })));
});
"""


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
LOGIN = _load_gui("login.html", "<h1>Login</h1><form method='post' action='/api/auth/login'></form>")


# ---------------------------------------------------------------- handler
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
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

    def _session_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            part = part.strip()
            if part.startswith("oim_session="):
                return part.split("=", 1)[1].strip()
        return ""

    def _current_user(self):
        token = self._session_token()
        if not token:
            return None
        conn = db()
        row = conn.execute(
            "SELECT u.id, u.email, u.created_at FROM sessions s JOIN users u ON u.id=s.user_id"
            " WHERE s.token=? AND s.expires>?", (token, time.time())).fetchone()
        conn.close()
        return dict(row) if row else None

    def _new_session(self, conn, user_id):
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)",
                     (token, user_id, time.time() + SESSION_TTL))
        return token

    def _session_cookie(self, token):
        return {"Set-Cookie": f"oim_session={token}; HttpOnly; Path=/; SameSite=Lax; Max-Age={SESSION_TTL}"}

    def _admin(self):
        tok = self.headers.get("X-Admin-Token") or ""
        if tok == ADMIN_TOKEN or self._current_user():
            return True
        self._send(401, {"error": "unauthorized — X-Admin-Token or login session required"})
        return False

    # ------------------------------------------------------------ GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/health":
            _c = db()
            _cfg = ai_engine.get_ai_config(_c)
            _c.close()
            return self._send(200, {"ok": True, "version": VERSION, "dry_run": DRY_RUN,
                                    "ai_engine": "meta-ai" if _cfg["configured"] else "local",
                                    "ai_source": _cfg["source"]})
        if u.path == "/":
            return self._send(200, LANDING, "text/html")
        if u.path == "/widget.js":
            return self._send(200, WIDGET, "application/javascript")
        if u.path == "/admin":
            return self._send(200, ADMIN, "text/html")
        if u.path == "/login":
            return self._send(200, LOGIN, "text/html")
        if u.path == "/manifest.json":
            return self._send(200, MANIFEST)
        if u.path == "/sw.js":
            return self._send(200, SERVICE_WORKER, "application/javascript")
        if u.path == "/api/auth/me":
            user = self._current_user()
            if not user:
                return self._send(401, {"error": "not logged in"})
            return self._send(200, {"user": user})
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
            if u.path == "/api/settings/ai":
                status = ai_engine.config_status(conn)
                conn.close(); return self._send(200, status)
            if u.path == "/api/audit":
                rows = [dict(r) for r in conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]
                conn.close(); return self._send(200, rows)
            if u.path == "/api/inbox":
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM messages ORDER BY id DESC LIMIT 200")]
                conn.close(); return self._send(200, rows)
            if u.path == "/api/analytics":
                def counts(table, col):
                    return {r[col]: r["c"] for r in conn.execute(
                        f"SELECT {col}, COUNT(*) c FROM {table} GROUP BY {col}")}
                by_status = counts("contacts", "status")
                by_channel = counts("contacts", "channel")
                camp_status = counts("campaigns", "status")
                del_status = counts("deliveries", "status")
                opt_ins = conn.execute(
                    "SELECT COUNT(*) c FROM audit WHERE event IN ('optin_confirmed','cleanreach_import')"
                ).fetchone()["c"]
                opt_outs = conn.execute(
                    "SELECT COUNT(*) c FROM audit WHERE event='opted_out'").fetchone()["c"]
                total_del = sum(del_status.values())
                sent_del = del_status.get("sent", 0)
                out = {
                    "version": VERSION,
                    "contacts": {"total": sum(by_status.values()), "by_status": by_status,
                                 "by_channel": by_channel,
                                 "confirmed": by_status.get("confirmed", 0),
                                 "pending": by_status.get("pending", 0),
                                 "opted_out": by_status.get("opted_out", 0)},
                    "campaigns": {"total": sum(camp_status.values()), "by_status": camp_status},
                    "deliveries": {"total": total_del, "by_status": del_status,
                                   "sent": sent_del, "failed": del_status.get("failed", 0)},
                    "opt_ins": opt_ins, "opt_outs": opt_outs,
                    "total_contacts": sum(by_status.values()),
                    "total_campaigns": sum(camp_status.values()),
                    "total_deliveries": total_del,
                    "delivery_rate": round(100.0 * sent_del / total_del, 1) if total_del else 0.0,
                    "inbox_messages": conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"],
                    "templates": conn.execute("SELECT COUNT(*) c FROM templates").fetchone()["c"],
                    "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
                }
                conn.close(); return self._send(200, out)
            conn.close(); return self._send(404, {"error": "not found"})
        return self._send(404, {"error": "not found"})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        d = self._body()
        conn = db()

        # ---------------------------------------------------------- auth
        if u.path == "/api/auth/register":
            email = str(d.get("email", "")).strip().lower()
            password = str(d.get("password", ""))
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(password) < 8:
                conn.close()
                return self._send(400, {"error": "valid email and a password of 8+ characters required"})
            if conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
                conn.close(); return self._send(409, {"error": "account already exists — log in instead"})
            pw_hash, pw_salt = hash_password(password)
            conn.execute("INSERT INTO users(email,pw_hash,pw_salt,created_at) VALUES(?,?,?,?)",
                         (email, pw_hash, pw_salt, now()))
            uid = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
            audit(conn, "user_registered", email)
            token = self._new_session(conn, uid)
            conn.commit(); conn.close()
            return self._send(201, {"ok": True, "user": {"id": uid, "email": email}},
                              headers=self._session_cookie(token))
        if u.path == "/api/auth/login":
            email = str(d.get("email", "")).strip().lower()
            password = str(d.get("password", ""))
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if not row or not verify_password(password, row["pw_hash"], row["pw_salt"]):
                conn.close(); return self._send(401, {"error": "invalid email or password"})
            audit(conn, "user_login", email)
            token = self._new_session(conn, row["id"])
            conn.commit(); conn.close()
            return self._send(200, {"ok": True, "user": {"id": row["id"], "email": email}},
                              headers=self._session_cookie(token))
        if u.path == "/api/auth/logout":
            token = self._session_token()
            if token:
                conn.execute("DELETE FROM sessions WHERE token=?", (token,))
                conn.commit()
            conn.close()
            return self._send(200, {"ok": True},
                              headers={"Set-Cookie": "oim_session=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0"})

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
            raw_text = str(d.get("body") or d.get("Body") or d.get("text") or "").strip()
            text = raw_text.upper()
            channel = str(d.get("channel") or "sms").strip().lower()
            if u.path == "/api/optout":
                text = "STOP"
            row = conn.execute("SELECT * FROM contacts WHERE identifier=?", (ident,)).fetchone()
            if text in STOP_WORDS:
                if not row:
                    conn.close(); return self._send(404, {"error": "contact not found"})
                conn.execute("UPDATE contacts SET status='opted_out', opted_out_at=? WHERE id=?", (now(), row["id"]))
                audit(conn, "opted_out", f"contact {row['id']} via '{text}'")
                conn.commit(); conn.close()
                return self._send(200, {"message": "Opted out. You will receive no more messages."})
            if text in HELP_WORDS:
                conn.close(); return self._send(200, {"message": "Help: reply STOP to opt out."})
            # Ordinary inbound message -> store it in the inbox (even when the
            # sender is not a known contact yet; contact_id then stays NULL).
            conn.execute(
                "INSERT INTO messages(contact_id,identifier,channel,direction,body,status,created_at)"
                " VALUES(?,?,?,'inbound',?,'received',?)",
                (row["id"] if row else None, ident,
                 (row["channel"] if row else channel), raw_text, now()))
            mid = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
            audit(conn, "inbox_message", f"message {mid} from {ident}")
            conn.commit(); conn.close()
            return self._send(200, {"message": "received", "stored": True, "id": mid})

        # everything below is admin-only
        if u.path.startswith("/api/"):
            if not self._admin():
                conn.close(); return
            # ------------------------------------------------ BYOK AI settings
            if u.path == "/api/settings/ai":
                # Save the owner's own Meta AI key into the settings table.
                # The value is NEVER logged or audited — only the fact of the
                # update is. Empty api_key clears the stored value.
                updates = {}
                if "base_url" in d:
                    bu = str(d.get("base_url") or "").strip()
                    if bu and not bu.startswith("https://"):
                        conn.close()
                        return self._send(400, {"error": "base_url must start with https://"})
                    updates["meta_ai_base_url"] = bu
                if "api_key" in d:
                    updates["meta_ai_api_key"] = str(d.get("api_key") or "").strip()
                if "model" in d:
                    updates["meta_ai_model"] = str(d.get("model") or "").strip()
                for skey, val in updates.items():
                    if val:
                        conn.execute("INSERT INTO settings(key,value) VALUES(?,?)"
                                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                     (skey, val))
                    else:
                        conn.execute("DELETE FROM settings WHERE key=?", (skey,))
                audit(conn, "ai_key_updated", "AI settings updated (values not logged)")
                conn.commit()
                status = ai_engine.config_status(conn)
                conn.close(); return self._send(200, status)
            if u.path == "/api/settings/ai/test":
                res = ai_engine.test_connection(conn)
                audit(conn, "ai_key_test", f"ok={res['ok']} engine={res['engine']}")
                conn.commit(); conn.close(); return self._send(200, res)
            # ------------------------------------------------ AI Studio
            if u.path == "/api/ai/draft":
                res = ai_engine.draft(str(d.get("goal", "")), str(d.get("channel", "whatsapp")),
                                      str(d.get("tone", "friendly")), str(d.get("language", "en")),
                                      conn=conn)
                audit(conn, "ai_draft", f"engine={res['engine']} channel={d.get('channel','whatsapp')}")
                conn.commit(); conn.close(); return self._send(200, res)
            if u.path == "/api/ai/translate":
                res = ai_engine.translate(str(d.get("text", "")), str(d.get("target", "en")),
                                          conn=conn)
                audit(conn, "ai_translate", f"engine={res['engine']} target={d.get('target','en')}")
                conn.commit(); conn.close(); return self._send(200, res)
            if u.path == "/api/ai/check":
                res = ai_engine.check(str(d.get("text", "")), str(d.get("channel", "whatsapp")),
                                      conn=conn)
                res = dict(res); res["ok"] = True
                audit(conn, "ai_check", f"engine={res['engine']} score={res['score']}")
                conn.commit(); conn.close(); return self._send(200, res)
            # ------------------------------------------------ Inbox reply
            mr = re.match(r"^/api/inbox/(\d+)/reply$", u.path)
            if mr:
                mid = int(mr.group(1))
                orig = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
                if not orig:
                    conn.close(); return self._send(404, {"error": "message not found"})
                body = str(d.get("body", "")).strip()
                if not body:
                    conn.close(); return self._send(400, {"error": "reply body required"})
                status = "sent (dry-run)" if DRY_RUN else "sent"
                conn.execute(
                    "INSERT INTO messages(contact_id,identifier,channel,direction,body,status,reply_to,created_at)"
                    " VALUES(?,?,?,'outbound',?,?,?,?)",
                    (orig["contact_id"], orig["identifier"], orig["channel"],
                     body, status, mid, now()))
                rid = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
                conn.execute(
                    "INSERT INTO deliveries(campaign_id,contact_id,provider,status,detail,created_at)"
                    " VALUES(NULL,?,?,?, ?,?)",
                    (orig["contact_id"], "dry-run" if DRY_RUN else "inbox-reply",
                     "sent" if DRY_RUN else "sent", f"inbox reply {rid} to message {mid}", now()))
                audit(conn, "inbox_reply", f"reply {rid} to message {mid} ({status})")
                conn.commit(); conn.close()
                return self._send(201, {"ok": True, "id": rid, "status": status, "dry_run": DRY_RUN})
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
    _c = db(); _cfg = ai_engine.get_ai_config(_c); _c.close()
    print(f"Opt-In Message v{VERSION} on http://127.0.0.1:{PORT}  DRY_RUN={DRY_RUN}  "
          f"AI={'meta-ai' if _cfg['configured'] else 'local'} (source={_cfg['source']})  DB={DB_PATH}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
