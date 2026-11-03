#!/usr/bin/env python3
"""End-to-end test: spin the real server on a temp DB and walk the full flow.
Run: python3 tests/test_e2e.py  (exit 0 = all pass)"""
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = "18099"
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = {"X-Admin-Token": "test-token", "Content-Type": "application/json"}
passed = failed = 0

def call(method, path, data=None, headers=None, raw_headers=None):
    h = dict(headers or {})
    if raw_headers is not None:
        h = raw_headers
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode()
            try: return r.status, json.loads(txt)
            except Exception: return r.status, txt
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try: return e.code, json.loads(txt)
        except Exception: return e.code, txt

def call_full(method, path, data=None, headers=None):
    """Like call() but also returns response headers (for Set-Cookie checks)."""
    h = dict(headers or {})
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            txt = r.read().decode()
            try: parsed = json.loads(txt)
            except Exception: parsed = txt
            return r.status, parsed, dict(r.headers.items())
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try: parsed = json.loads(txt)
        except Exception: parsed = txt
        return e.code, parsed, dict(e.headers.items())

def check(name, cond):
    global passed, failed
    print(("PASS " if cond else "FAIL ") + name)
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)

def main():
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, PORT=PORT, ADMIN_TOKEN="test-token", DRY_RUN="1",
               DATABASE_PATH=os.path.join(tmp, "test.db"),
               META_AI_API_KEY="", META_AI_BASE_URL="https://api.meta.ai/v1",
               META_AI_MODEL="muse-spark-1.3")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                s, _ = call("GET", "/health"); break
            except Exception: time.sleep(0.2)
        s, b = call("GET", "/health"); check("health 200", s == 200)
        s, b = call("GET", "/"); check("GUI landing serves hero + dashboard link", s == 200 and "Open the dashboard" in b and "optin-widget" in b)
        s, b = call("GET", "/admin"); check("GUI dashboard serves tabs + stats + campaign actions", s == 200 and 'id="stats"' in b and 'data-tab="campaigns"' in b and "Approve" in b and 'id="toast"' in b)
        s, b = call("GET", "/widget.js"); check("GUI widget serves styled form", s == 200 and "oim" in b and "Subscribe" in b)
        s, b = call("GET", "/api/contacts"); check("admin routes 401 without token", s == 401)
        s, b = call("GET", "/api/contacts", headers=ADMIN); check("admin routes 200 with token", s == 200)

        s, b = call("POST", "/api/optin", {"name": "Rahim", "identifier": "+8801712345678",
                                           "channel": "sms", "tags": "dhaka,launch", "source": "test"})
        check("optin creates pending", s == 201 and "confirm_url" in b)
        confirm_url = b["confirm_url"]

        s, b = call("POST", "/api/templates", {"name": "launch", "channel": "sms",
                                               "body": "Hi {name}, launch is live!"}, headers=ADMIN)
        check("template created", s == 201)
        s, b = call("POST", "/api/campaigns", {"name": "c1", "template_id": 1, "channel": "sms"}, headers=ADMIN)
        cid = b["id"]; check("campaign created as draft", s == 201)
        s, b = call("POST", f"/api/campaigns/{cid}/send", {}, headers=ADMIN)
        check("APPROVAL GATE: send blocked before approval (409)", s == 409)

        s, b = call("GET", confirm_url)
        check("double opt-in confirm works", s == 200)
        s, b = call("GET", "/api/contacts?status=confirmed", headers=ADMIN)
        check("contact now confirmed", s == 200 and len(b) == 1)

        s, b = call("POST", f"/api/campaigns/{cid}/approve", {}, headers=ADMIN)
        check("campaign approved", s == 200)
        s, b = call("POST", f"/api/campaigns/{cid}/send", {}, headers=ADMIN)
        check("send after approval targets 1, sends 1", s == 200 and b["targeted"] == 1 and b["sent"] == 1)

        s, b = call("POST", "/webhook/inbound", {"from": "+8801712345678", "body": "STOP"})
        check("STOP webhook opts out", s == 200)
        s, b = call("GET", "/api/contacts?status=opted_out", headers=ADMIN)
        check("contact opted_out", s == 200 and len(b) == 1)

        s, b = call("POST", "/api/campaigns", {"name": "c2", "template_id": 1, "channel": "sms"}, headers=ADMIN)
        cid2 = b["id"]
        call("POST", f"/api/campaigns/{cid2}/approve", {}, headers=ADMIN)
        s, b = call("POST", f"/api/campaigns/{cid2}/send", {}, headers=ADMIN)
        check("opted-out contact excluded from next send", s == 200 and b["targeted"] == 0)

        csv_text = "name,phone,consent\nKarim,+8801912345678,true\nNoConsent,+8801812345678,false\n"
        s, b = call("POST", "/api/import/consented", {"csv": csv_text, "channel": "sms"}, headers=ADMIN)
        check("CleanReach import: only consented rows imported", s == 200 and b["imported"] == 1 and b["skipped"] == 1)

        s, b = call("GET", "/api/audit", headers=ADMIN)
        events = {r["event"] for r in b}
        check("audit log has full trail", {"optin_requested", "optin_confirmed", "campaign_approved",
                                           "campaign_sent", "opted_out", "cleanreach_import"} <= events)

        # ---------------- v0.4.0: auth ----------------
        s, b = call("GET", "/login")
        check("login page serves", s == 200 and "submitAuth" in b)
        s, b, hdrs = call_full("POST", "/api/auth/register",
                               {"email": "owner@test.shop", "password": "TestPass2026"})
        cookie = hdrs.get("Set-Cookie", "")
        check("register creates account + HttpOnly session cookie",
              s == 201 and "oim_session=" in cookie and "HttpOnly" in cookie)
        session = {"Cookie": cookie.split(";")[0], "Content-Type": "application/json"}
        s, b = call("GET", "/api/auth/me", headers=session)
        check("auth/me returns the logged-in user", s == 200 and b["user"]["email"] == "owner@test.shop")
        s, b = call("GET", "/api/contacts", headers=session)
        check("session cookie grants admin API access (no token)", s == 200)
        s, b = call("POST", "/api/auth/login", {"email": "owner@test.shop", "password": "wrong-pass"})
        check("login rejects a wrong password", s == 401)
        s, b, hdrs = call_full("POST", "/api/auth/login",
                               {"email": "owner@test.shop", "password": "TestPass2026"})
        check("login with correct password sets a session", s == 200 and "oim_session=" in hdrs.get("Set-Cookie", ""))

        # ---------------- v0.4.0: AI Studio ----------------
        s, b = call("POST", "/api/ai/draft",
                    {"goal": "Our Eid collection is live", "channel": "whatsapp",
                     "tone": "friendly", "language": "en"}, headers=ADMIN)
        check("AI draft returns text + engine field",
              s == 200 and len(b.get("draft", "")) > 10 and b.get("engine") in ("meta-ai", "local"))
        spammy = "FREE FREE FREE!!! WIN CASH NOW!!! Click here to claim your GUARANTEED prize, earn $5000, hurry, offer expires!!!"
        clean = "Hi {name}, our winter collection is live at Dhaka Threads. Reply STOP to opt out."
        s, spam = call("POST", "/api/ai/check", {"text": spammy, "channel": "whatsapp"}, headers=ADMIN)
        s2, good = call("POST", "/api/ai/check", {"text": clean, "channel": "whatsapp"}, headers=ADMIN)
        check("AI check flags spam: spammy scores lower than clean",
              s == 200 and s2 == 200 and spam["score"] < good["score"] and spam["engine"] in ("meta-ai", "local"))
        check("AI check lists findings + safer rewrite for spam",
              len(spam.get("findings", [])) >= 3 and len(spam.get("safer_rewrite", "")) > 5)
        s, b = call("POST", "/api/ai/translate",
                    {"text": "Hello, our Eid sale is live! Shop now.", "target": "bn"}, headers=ADMIN)
        check("AI translate en->bn returns non-empty, different text",
              s == 200 and b.get("translation") and b["translation"] != "Hello, our Eid sale is live! Shop now."
              and b.get("engine") in ("meta-ai", "local"))
        s, b = call("POST", "/api/ai/draft", {"goal": "x"}, headers={})
        check("AI Studio is auth-protected (401 without token/session)", s == 401)

        # ---------------- v0.4.0: inbox + analytics + PWA ----------------
        s, b = call("POST", "/webhook/inbound",
                    {"from": "+8801912345678", "body": "Do you deliver outside Dhaka?", "channel": "sms"})
        check("inbound non-STOP message is stored", s == 200 and b.get("stored") is True)
        s, b = call("GET", "/api/inbox", headers=ADMIN)
        check("inbox lists the stored message",
              s == 200 and any(m["body"] == "Do you deliver outside Dhaka?" for m in b))
        inbox_id = [m for m in b if m["body"] == "Do you deliver outside Dhaka?"][0]["id"]
        s, b = call("POST", f"/api/inbox/{inbox_id}/reply",
                    {"body": "Yes, delivery outside Dhaka is Tk 120."}, headers=ADMIN)
        check("inbox reply records a dry-run delivery", s == 201 and b.get("dry_run") is True)
        s, b = call("GET", "/api/analytics", headers=ADMIN)
        check("analytics returns numeric totals + delivery rate",
              s == 200 and isinstance(b.get("total_contacts"), int) and b["total_contacts"] >= 2
              and isinstance(b.get("delivery_rate"), (int, float)))
        s, b = call("GET", "/manifest.json")
        check("PWA manifest serves with name + theme", s == 200 and b.get("name") == "Opt-In Message"
              and b.get("theme_color") == "#FAF8F4")
        s, b = call("GET", "/sw.js")
        check("PWA service worker serves cache-first shell", s == 200 and "cache-first" in b and "/admin" in b)

        # ---------------- v0.5.1: BYOK AI settings ----------------
        s, b = call("GET", "/api/settings/ai")
        check("AI settings 401 unauthenticated (GET)", s == 401)
        s, b = call("POST", "/api/settings/ai", {"api_key": "x"})
        check("AI settings 401 unauthenticated (POST)", s == 401)
        s, b = call("GET", "/api/settings/ai", headers=ADMIN)
        check("AI settings: nothing configured at start",
              s == 200 and b["configured"] is False and b["source"] == "none" and b["key_hint"] == "")
        s, b = call("POST", "/api/settings/ai/test", {}, headers=ADMIN)
        check("AI settings test with no key is honestly ok=false",
              s == 200 and b["ok"] is False and "no key" in b.get("error", ""))
        s, b = call("POST", "/api/settings/ai", {"base_url": "http://insecure.example/v1"}, headers=ADMIN)
        check("AI settings rejects a non-https base_url", s == 400)

        FAKE_KEY = "sk-test-FAKEKEY-1234wxyz"
        s, b = call("POST", "/api/settings/ai",
                    {"api_key": FAKE_KEY, "base_url": "https://127.0.0.1:9/v1",
                     "model": "muse-spark-1.3"}, headers=ADMIN)
        check("AI settings save accepts a key", s == 200 and b["configured"] is True)
        s, b = call("GET", "/api/settings/ai", headers=ADMIN)
        check("AI settings GET: configured via settings, hint = last 4 only",
              s == 200 and b["configured"] is True and b["source"] == "settings"
              and b["key_hint"] == "wxyz")
        check("full AI key never appears in the settings response",
              FAKE_KEY not in json.dumps(b))
        s, b = call("GET", "/api/audit", headers=ADMIN)
        check("full AI key never appears in the audit log",
              FAKE_KEY not in json.dumps(b)
              and any(r["event"] == "ai_key_updated" for r in b))
        s, b = call("POST", "/api/ai/draft",
                    {"goal": "Our Eid collection is live", "channel": "whatsapp",
                     "tone": "friendly", "language": "en"}, headers=ADMIN)
        check("AI draft with an unreachable saved key falls back gracefully (engine field)",
              s == 200 and len(b.get("draft", "")) > 10 and b.get("engine") == "local")
        s, b = call("POST", "/api/settings/ai/test", {}, headers=ADMIN)
        check("AI settings test with unreachable key is honestly ok=false",
              s == 200 and b["ok"] is False and b.get("engine") == "meta-ai")
        s, b = call("POST", "/api/settings/ai", {"api_key": ""}, headers=ADMIN)
        check("clearing the key returns 200 + unconfigured",
              s == 200 and b["configured"] is False)
        s, b = call("GET", "/api/settings/ai", headers=ADMIN)
        check("after clear: source falls back to none, hint empty",
              s == 200 and b["configured"] is False and b["source"] == "none" and b["key_hint"] == "")
    finally:
        proc.terminate()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
