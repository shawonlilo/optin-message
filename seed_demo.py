#!/usr/bin/env python3
"""
seed_demo.py — idempotent demo data for Opt-In Message v0.4.0.

Creates the demo workspace of a fictional Dhaka clothing shop,
"Dhaka Threads", so a judge (or you) can open the dashboard and see a
living product instead of empty tables.

DEMO ACCOUNT (public, for hackathon judges — NOT a real credential):
    email:    demo@dhakathreads.shop
    password: DemoPass2026
This account only exists inside the local demo database created by this
script. Do not reuse this password anywhere real.

Run:  python3 seed_demo.py        (respects DATABASE_PATH, default data/optin.db)
Safe to run repeatedly — every insert is checked / INSERT OR IGNORE.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: E402  (app reads PORT/DB env at import; harmless here)

DEMO_EMAIL = "demo@dhakathreads.shop"
DEMO_PASSWORD = "DemoPass2026"  # public demo password, see header

CONTACTS = [
    # name, identifier, channel, tags
    ("রহিম উদ্দিন", "+8801711000001", "whatsapp", "eid,vip"),
    ("Fatema Begum", "+8801711000002", "whatsapp", "eid"),
    ("করিম শেখ", "+8801811000003", "sms", "winter"),
    ("Ayesha Rahman", "+8801911000004", "whatsapp", "eid,winter,vip"),
    ("তানভীর হাসান", "+8801611000005", "whatsapp", "winter"),
    ("Nusrat Jahan", "+8801511000006", "sms", "eid"),
    ("শাকিল আহমেদ", "+8801711000007", "whatsapp", "vip"),
    ("Sharmin Akter", "+8801811000008", "whatsapp", "winter"),
    ("মেহেদী হাসান", "+8801911000009", "sms", "eid,winter"),
    ("Priya Das", "+8801611000010", "whatsapp", "eid"),
    ("আরিফুল ইসলাম", "+8801511000011", "whatsapp", "winter,vip"),
    ("sumi@dhakathreads.shop", "sumi@dhakathreads.shop", "email", "eid"),
]

TEMPLATES = [
    ("Eid Collection Launch", "whatsapp",
     "Hi {name}! 🌙 Dhaka Threads: our Eid collection is live — premium panjabi & saree, up to 20% off for subscribers. Reply STOP to opt out."),
    ("Winter Sale SMS", "sms",
     "Hi {name}, Dhaka Threads winter sale is on — jackets & hoodies from Tk 899. Reply STOP to opt out."),
    ("VIP Early Access", "whatsapp",
     "Hi {name} ⭐ VIP early access: shop the new collection 24h before everyone else at Dhaka Threads. Reply STOP to opt out."),
]

INBOX = [
    ("+8801711000002", "whatsapp", "আসসালামু আলাইকুম, ঈদ কালেকশনে কি লাল শাড়ি আছে?"),
    ("+8801611000005", "whatsapp", "What are your delivery charges inside Dhaka?"),
    ("+8801711000001", "whatsapp", "Got my panjabi, fitting is perfect. Thank you!"),
    ("+8801811000008", "whatsapp", "Do you have the winter hoodie in size L?"),
]


def main():
    app.init_db()
    conn = app.db()
    now = app.now()

    # --- demo user (idempotent) ---
    if not conn.execute("SELECT id FROM users WHERE email=?", (DEMO_EMAIL,)).fetchone():
        pw_hash, pw_salt = app.hash_password(DEMO_PASSWORD)
        conn.execute("INSERT INTO users(email,pw_hash,pw_salt,created_at) VALUES(?,?,?,?)",
                     (DEMO_EMAIL, pw_hash, pw_salt, now))
        app.audit(conn, "seed_demo_user", DEMO_EMAIL)

    # --- contacts: 12 confirmed subscribers (idempotent via UNIQUE) ---
    contact_ids = {}
    for name, ident, channel, tags in CONTACTS:
        conn.execute(
            "INSERT OR IGNORE INTO contacts(name,identifier,channel,tags,source,ip,consent_version,"
            "status,created_at,confirmed_at) VALUES(?,?,?,?,'seed-demo','127.0.0.1',?,'confirmed',?,?)",
            (name, ident, channel, tags, app.CONSENT_VERSION, now, now))
        row = conn.execute("SELECT id FROM contacts WHERE identifier=? AND channel=?",
                           (ident, channel)).fetchone()
        contact_ids[ident] = row["id"]
    app.audit(conn, "seed_contacts", f"{len(CONTACTS)} confirmed Dhaka Threads subscribers")

    # --- templates (idempotent by name) ---
    template_ids = {}
    for name, channel, body in TEMPLATES:
        row = conn.execute("SELECT id FROM templates WHERE name=?", (name,)).fetchone()
        if not row:
            conn.execute("INSERT INTO templates(name,channel,body,created_at) VALUES(?,?,?,?)",
                         (name, channel, body, now))
            row = conn.execute("SELECT id FROM templates WHERE name=?", (name,)).fetchone()
        template_ids[name] = row["id"]

    # --- campaign 1: approved + sent (dry-run deliveries) ---
    c1 = conn.execute("SELECT * FROM campaigns WHERE name='Eid Launch — WhatsApp'").fetchone()
    if not c1:
        conn.execute(
            "INSERT INTO campaigns(name,template_id,channel,tag_filter,status,created_at,approved_at,sent_at)"
            " VALUES(?,?,?,'eid','sent',?,?,?)",
            ("Eid Launch — WhatsApp", template_ids["Eid Collection Launch"], "whatsapp", now, now, now))
        c1 = conn.execute("SELECT * FROM campaigns WHERE name='Eid Launch — WhatsApp'").fetchone()
        sent = 0
        for name, ident, channel, tags in CONTACTS:
            if channel == "whatsapp" and "eid" in tags.split(","):
                conn.execute(
                    "INSERT INTO deliveries(campaign_id,contact_id,provider,status,detail,created_at)"
                    " VALUES(?,?, 'dry-run','sent','simulated send (DRY_RUN=1)',?)",
                    (c1["id"], contact_ids[ident], now))
                sent += 1
        app.audit(conn, "campaign_approved", f"campaign {c1['id']} approved by demo@dhakathreads.shop")
        app.audit(conn, "campaign_sent", f"campaign {c1['id']}: {sent} sent, 0 failed, {sent} targeted (seed)")

    # --- campaign 2: draft awaiting approval (shows the gate) ---
    if not conn.execute("SELECT id FROM campaigns WHERE name='Winter Sale — Draft'").fetchone():
        conn.execute(
            "INSERT INTO campaigns(name,template_id,channel,tag_filter,status,created_at)"
            " VALUES(?,?,?,'winter','draft',?)",
            ("Winter Sale — Draft", template_ids["Winter Sale SMS"], "sms", now))
        app.audit(conn, "campaign_created", "Winter Sale — Draft (seed, awaiting approval)")

    # --- inbox messages (idempotent by identifier+body) ---
    for ident, channel, body in INBOX:
        if not conn.execute("SELECT id FROM messages WHERE identifier=? AND body=?",
                            (ident, body)).fetchone():
            conn.execute(
                "INSERT INTO messages(contact_id,identifier,channel,direction,body,status,created_at)"
                " VALUES(?,?,?,'inbound',?,'received',?)",
                (contact_ids.get(ident), ident, channel, body, now))

    app.audit(conn, "seed_demo", "Dhaka Threads demo data seeded")
    conn.commit()

    stats = {
        "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "contacts": conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"],
        "templates": conn.execute("SELECT COUNT(*) c FROM templates").fetchone()["c"],
        "campaigns": conn.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"],
        "deliveries": conn.execute("SELECT COUNT(*) c FROM deliveries").fetchone()["c"],
        "messages": conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"],
    }
    conn.close()
    print("Dhaka Threads demo seeded:", stats)
    print(f"Demo login: {DEMO_EMAIL} / {DEMO_PASSWORD}  ->  open /login")


if __name__ == "__main__":
    main()
