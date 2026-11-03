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

def check(name, cond):
    global passed, failed
    print(("PASS " if cond else "FAIL ") + name)
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)

def main():
    tmp = tempfile.mkdtemp()
    env = dict(os.environ, PORT=PORT, ADMIN_TOKEN="test-token", DRY_RUN="1",
               DATABASE_PATH=os.path.join(tmp, "test.db"))
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
    finally:
        proc.terminate()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
