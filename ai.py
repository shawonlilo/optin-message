#!/usr/bin/env python3
"""
AI Studio engine for Opt-In Message.

Honesty rule: every public function returns an "engine" field that says
where the output actually came from — "meta-ai" when the Meta Model API
answered, "local" when the built-in local engine produced it. The source
is never faked.

Meta path: if a Meta Model API key is configured, call an OpenAI-compatible
chat-completions endpoint (default https://api.meta.ai/v1, model
muse-spark-1.3) with a timeout, and fall back to the local engine on ANY
failure. Key resolution (BYOK — bring your own key), via the ONE helper
get_ai_config(): (a) the instance's `settings` table (a key the owner
pasted into Dashboard → AI Studio → AI Settings), then (b) env vars
META_AI_API_KEY / META_AI_BASE_URL / META_AI_MODEL. A stored key lives
only in the owner's own local database file; it is never written to any
repo file or log, and only its last 4 characters are ever shown back.

Local engine (fully functional, no key needed):
  * draft     — template-based campaign drafting per channel/tone/language
  * translate — phrasebook + word-map translator, EN <-> BN marketing copy
  * check     — real rule-based compliance / spam-risk scorer with an
                itemised findings list and a safer-rewrite builder
"""
import json
import os
import re
import time
import urllib.request

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
META_TIMEOUT = float(os.environ.get("META_AI_TIMEOUT", "20"))

# settings-table keys (written by Dashboard → AI Settings)
_SETTING_KEYS = {"meta_ai_api_key": "api_key", "meta_ai_base_url": "base_url",
                 "meta_ai_model": "model"}

OPT_OUT_EN = "Reply STOP to opt out."
OPT_OUT_BN = "বন্ধ করতে STOP লিখে রিপ্লাই করুন।"


def get_ai_config(conn=None):
    """THE one place AI config is resolved. Returns a dict:
    {api_key, base_url, model, source, configured}.

    Resolution order: settings table (conn) -> env vars -> none.
    `source` says where the API KEY came from: "settings" | "env" | "none".
    base_url / model settings override env independently of the key.
    """
    cfg = {
        "api_key": os.environ.get("META_AI_API_KEY", ""),
        "base_url": os.environ.get("META_AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "model": os.environ.get("META_AI_MODEL", DEFAULT_MODEL),
        "source": "env" if os.environ.get("META_AI_API_KEY") else "none",
    }
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key IN"
                " ('meta_ai_api_key','meta_ai_base_url','meta_ai_model')").fetchall()
        except Exception:  # noqa: BLE001 — table may not exist (old DB)
            rows = []
        stored = {}
        for r in rows:
            try:
                stored[r["key"]] = r["value"]
            except Exception:  # noqa: BLE001 — plain tuple rows
                stored[r[0]] = r[1]
        for skey, field in _SETTING_KEYS.items():
            if stored.get(skey):
                cfg[field] = stored[skey].strip().rstrip("/") if field == "base_url" \
                    else stored[skey].strip()
                if field == "api_key":
                    cfg["source"] = "settings"
    cfg["configured"] = bool(cfg["api_key"])
    return cfg


def config_status(conn=None):
    """Public (key-free) view of the effective config: never the full key."""
    cfg = get_ai_config(conn)
    return {"configured": cfg["configured"], "source": cfg["source"],
            "base_url": cfg["base_url"], "model": cfg["model"],
            "key_hint": cfg["api_key"][-4:] if cfg["api_key"] else ""}


def meta_available(conn=None):
    return get_ai_config(conn)["configured"]


def _meta_chat_timed(system, user, cfg):
    """One chat-completions call with an explicit config.
    Returns (text_or_None, error_or_None, latency_ms). Errors never
    contain the API key (it only ever travels in the header)."""
    if not cfg.get("api_key"):
        return None, "no key configured", 0
    payload = json.dumps({
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(cfg["base_url"] + "/chat/completions",
                                 data=payload, method="POST")
    req.add_header("Authorization", f"Bearer {cfg['api_key']}")
    req.add_header("Content-Type", "application/json")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=META_TIMEOUT) as r:
            data = json.loads(r.read().decode())
        latency = int((time.time() - started) * 1000)
        text = data["choices"][0]["message"]["content"].strip()
        return (text or None), (None if text else "empty response from Meta API"), latency
    except Exception as e:  # noqa: BLE001 — any failure falls back to local
        return None, str(e)[:200], int((time.time() - started) * 1000)


def _meta_chat(system, user, cfg):
    """One chat-completions call. Returns reply text or None on any failure."""
    text, _err, _lat = _meta_chat_timed(system, user, cfg)
    return text


def test_connection(conn=None):
    """One minimal live call with the effective config (Settings → Test)."""
    cfg = get_ai_config(conn)
    if not cfg["configured"]:
        return {"ok": False, "engine": "local", "source": cfg["source"],
                "model": cfg["model"], "error": "no key configured"}
    text, err, latency = _meta_chat_timed(
        "You are a connectivity test. Reply with the single word: OK", "ping", cfg)
    if text:
        return {"ok": True, "engine": "meta-ai", "source": cfg["source"],
                "model": cfg["model"], "latency_ms": latency}
    return {"ok": False, "engine": "meta-ai", "source": cfg["source"],
            "model": cfg["model"], "latency_ms": latency,
            "error": err or "request failed"}


# ------------------------------------------------------------------ translate
# Phrasebook first (longest match wins), then a word map. Covers the common
# marketing vocabulary a small shop actually sends, EN <-> BN.
_PHRASES_EN_BN = [
    ("thank you for shopping with us", "আমাদের থেকে কেনাকাটা করার জন্য ধন্যবাদ"),
    ("our new collection is here", "আমাদের নতুন কালেকশন এসেছে"),
    ("our winter collection is live", "আমাদের শীতের কালেকশন লাইভ হয়েছে"),
    ("our eid collection is live", "আমাদের ঈদ কালেকশন লাইভ হয়েছে"),
    ("reply stop to opt out", "বন্ধ করতে STOP লিখে রিপ্লাই করুন"),
    ("free home delivery", "ফ্রি হোম ডেলিভারি"),
    ("cash on delivery", "ক্যাশ অন ডেলিভারি"),
    ("limited time offer", "সীমিত সময়ের অফার"),
    ("shop now", "এখনই কিনুন"),
    ("order now", "এখনই অর্ডার করুন"),
    ("new collection", "নতুন কালেকশন"),
    ("eid sale", "ঈদ সেল"),
    ("winter sale", "শীতের সেল"),
    ("sale is live", "সেল চলছে"),
    ("dear customer", "প্রিয় গ্রাহক"),
    ("special offer", "বিশেষ অফার"),
    ("best price", "সেরা দামে"),
    ("premium quality", "প্রিমিয়াম মানের"),
    ("for you", "আপনার জন্য"),
]
_WORDS_EN_BN = {
    "hello": "হ্যালো", "hi": "হাই", "dear": "প্রিয়", "customer": "গ্রাহক",
    "customers": "গ্রাহকবৃন্দ", "friend": "বন্ধু", "friends": "বন্ধুরা",
    "our": "আমাদের", "your": "আপনার", "you": "আপনি", "we": "আমরা",
    "new": "নতুন", "collection": "কালেকশন", "sale": "সেল", "offer": "অফার",
    "discount": "ছাড়", "off": "ছাড়", "price": "দাম", "shop": "কিনুন",
    "store": "দোকান", "buy": "কিনুন", "order": "অর্ডার", "now": "এখনই",
    "today": "আজ", "only": "মাত্র", "get": "পান", "on": "উপর",
    "all": "সব", "items": "পণ্যে", "item": "পণ্য", "dress": "পোশাক",
    "dresses": "পোশাক", "shirt": "শার্ট", "shirts": "শার্ট",
    "winter": "শীতের", "eid": "ঈদ", "festive": "উৎসবের",
    "delivery": "ডেলিভারি", "free": "ফ্রি", "home": "হোম",
    "quality": "মানের", "premium": "প্রিমিয়াম", "best": "সেরা",
    "visit": "আসুন", "us": "আমাদের", "and": "এবং", "the": "",
    "is": "হয়েছে", "are": "আছে", "live": "লাইভ", "available": "পাওয়া যাচ্ছে",
    "inside": "ভিতরে", "dhaka": "ঢাকা", "outside": "বাইরে",
    "thank": "ধন্যবাদ", "thanks": "ধন্যবাদ", "welcome": "স্বাগতম",
    "happy": "শুভ", "day": "দিন", "week": "সপ্তাহ", "month": "মাস",
    "up": "পর্যন্ত", "to": "পর্যন্ত", "in": "এ", "at": "এ",
}
_PHRASES_BN_EN = [(bn, en) for en, bn in _PHRASES_EN_BN]
_WORDS_BN_EN = {}
for _en, _bn in _WORDS_EN_BN.items():
    if _bn and _bn not in _WORDS_BN_EN:
        _WORDS_BN_EN[_bn] = _en


def _apply_phrases(text, pairs):
    for src, dst in sorted(pairs, key=lambda p: -len(p[0])):
        if not src:
            continue
        text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)
    return text


def _apply_words(text, wordmap):
    def repl(m):
        w = m.group(0)
        return wordmap.get(w.lower(), w)
    # Latin words only; Bengali words are matched by the BN->EN caller path.
    return re.sub(r"[A-Za-z']+", repl, text)


def _apply_words_bn(text, wordmap):
    for src in sorted(wordmap, key=len, reverse=True):
        text = text.replace(src, wordmap[src])
    return text


def local_translate(text, target):
    """target: 'bn'/'bengali'/'bangla' or 'en'/'english'. Returns translated text."""
    t = (target or "en").strip().lower()
    if t in ("bn", "bengali", "bangla"):
        out = _apply_phrases(text, _PHRASES_EN_BN)
        out = _apply_words(out, _WORDS_EN_BN)
        return re.sub(r"\s+", " ", out).strip()
    out = _apply_phrases(text, _PHRASES_BN_EN)
    out = _apply_words_bn(out, _WORDS_BN_EN)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:1].upper() + out[1:] if out else out


def translate(text, target, conn=None):
    cfg = get_ai_config(conn)
    t = (target or "en").strip().lower()
    lang_name = "Bengali (Bangla)" if t in ("bn", "bengali", "bangla") else "English"
    got = _meta_chat(
        "You are a precise translator for marketing messages. Output ONLY the translation.",
        f"Translate this message into {lang_name}:\n\n{text}", cfg)
    if got:
        return {"translation": got, "engine": "meta-ai", "model": cfg["model"]}
    return {"translation": local_translate(text, target), "engine": "local"}


# ---------------------------------------------------------------------- draft
_DRAFT_TEMPLATES = {
    ("whatsapp", "friendly", "en"): "Hi {name}! 👋 {business} here — {goal}. Made for our confirmed subscribers only. {optout}",
    ("whatsapp", "formal", "en"): "Hello {name}, this is {business}. {goal}. Thank you for subscribing to our updates. {optout}",
    ("whatsapp", "urgent", "en"): "Hi {name} — quick update from {business}: {goal}. Don't miss out! {optout}",
    ("sms", "friendly", "en"): "Hi {name}! {business}: {goal}. {optout}",
    ("sms", "formal", "en"): "{business}: Hello {name}, {goal}. {optout}",
    ("sms", "urgent", "en"): "{business} update for {name}: {goal}. {optout}",
    ("email", "friendly", "en"): "Hi {name},\n\nGreat news from {business} — {goal}.\n\nThanks for being a subscriber!\n{business}\n{optout}",
    ("email", "formal", "en"): "Dear {name},\n\n{business} is pleased to share: {goal}.\n\nRegards,\n{business}\n{optout}",
    ("email", "urgent", "en"): "Hi {name},\n\nTime-sensitive update from {business}: {goal}.\n\n{business}\n{optout}",
}
_DRAFT_TEMPLATES_BN = {
    "friendly": "হাই {name}! 👋 {business} থেকে বলছি — {goal}। শুধুমাত্র আমাদের নিশ্চিত গ্রাহকদের জন্য। {optout}",
    "formal": "আসসালামু আলাইকুম {name}, {business} থেকে জানাচ্ছি — {goal}। আমাদের আপডেটে সাবস্ক্রাইব করার জন্য ধন্যবাদ। {optout}",
    "urgent": "হাই {name} — {business} থেকে জরুরি আপডেট: {goal}। মিস করবেন না! {optout}",
}


def _local_draft(goal, channel, tone, language):
    lang = (language or "en").strip().lower()
    tone = (tone or "friendly").strip().lower()
    if tone not in ("friendly", "formal", "urgent"):
        tone = "friendly"
    channel = (channel or "whatsapp").strip().lower()
    if channel not in ("sms", "whatsapp", "email"):
        channel = "whatsapp"
    goal_text = (goal or "our new collection is live").strip()
    if lang in ("bn", "bengali", "bangla"):
        # Translate the goal itself when it is given in English, so the
        # whole draft reads in Bangla.
        if re.search(r"[A-Za-z]", goal_text) and not re.search(r"[\u0980-\u09FF]", goal_text):
            goal_text = local_translate(goal_text, "bn")
        tpl = _DRAFT_TEMPLATES_BN[tone]
        return tpl.format(name="{name}", business="{business}", goal=goal_text, optout=OPT_OUT_BN)
    tpl = _DRAFT_TEMPLATES[(channel, tone, "en")]
    return tpl.format(name="{name}", business="{business}", goal=goal_text, optout=OPT_OUT_EN)


def draft(goal, channel, tone, language, conn=None):
    cfg = get_ai_config(conn)
    got = _meta_chat(
        "You write compliant marketing messages for small businesses. Always end "
        "with 'Reply STOP to opt out.' Keep WhatsApp/SMS under 320 characters. "
        "Never use spam trigger language, false urgency or money claims. "
        "Output ONLY the message text, keeping the {name} personalisation token.",
        f"Write a {tone or 'friendly'} {channel or 'whatsapp'} campaign message in "
        f"{language or 'en'} for this goal: {goal}", cfg)
    if got:
        return {"draft": got, "engine": "meta-ai", "model": cfg["model"]}
    return {"draft": _local_draft(goal, channel, tone, language), "engine": "local"}


# ---------------------------------------------------------------------- check
SPAM_WORDS = [
    "free money", "make money", "act now", "click here", "buy now", "risk-free",
    "risk free", "100% free", "cash prize", "you won", "winner", "guaranteed",
    "congratulations", "claim now", "claim your", "limited time", "offer expires",
    "earn $", "earn money", "double your", "no investment", "work from home",
    "free", "prize", "cash", "urgent", "hurry", "expires", "guarantee",
]
URGENCY_WORDS = ["now!!!", "hurry", "urgent", "last chance", "don't miss",
                 "do not miss", "only today", "act fast", "immediately", "asap"]


def local_check(text, channel="whatsapp"):
    """Rule-based compliance / spam-risk scorer. 100 = clean, 0 = pure spam."""
    text = text or ""
    findings = []
    score = 100

    def deduct(kind, detail, points):
        nonlocal score
        score -= points
        findings.append({"type": kind, "detail": detail, "points": -points})

    words = re.findall(r"[A-Za-z']+", text)
    caps = [w for w in words if len(w) >= 3 and w.isupper()]
    if words and len(caps) / max(len(words), 1) > 0.15:
        deduct("all_caps", f"{len(caps)} word(s) in ALL CAPS "
               f"({round(100 * len(caps) / len(words))}% of words) — reads as shouting/spam.",
               20 if len(caps) / len(words) > 0.3 else 10)

    low = text.lower()
    hits = sorted({w for w in SPAM_WORDS if w in low})
    if hits:
        deduct("spam_trigger_words",
               "Spam trigger word(s): " + ", ".join(hits[:8]) + ". These trip carrier/Meta filters.",
               min(30, 6 * len(hits)))

    urg = [w for w in URGENCY_WORDS if w in low] + (["!!!"] if text.count("!") >= 3 else [])
    if urg:
        deduct("excessive_urgency",
               "Excessive urgency: " + ", ".join(sorted(set(urg))) +
               ". False urgency raises block/report rates.", 10)
    elif text.count("!") == 2:
        deduct("excessive_urgency", "Two exclamation marks — one is enough.", 3)

    links = re.findall(r"(https?://|www\.|bit\.ly|tinyurl|t\.co/)", low)
    if links:
        short = any(s in low for s in ("bit.ly", "tinyurl", "t.co/"))
        deduct("links", f"{len(links)} link(s) found" +
               (" — including a URL shortener, a classic spam signal." if short else "."),
               15 if short else 8)

    money = re.findall(r"(\$\s?\d|earn\s|profit\s|income\s|\d+\s?%\s?profit)", low)
    if money:
        deduct("money_claims", "Money/income claim detected — unsubstantiated earnings "
               "claims violate Meta and carrier policy.", 15)

    if not re.search(r"(stop|opt out|opt-out|unsubscribe)", low):
        deduct("missing_opt_out",
               "No opt-out line. Every marketing message must say how to stop "
               "(e.g. 'Reply STOP to opt out.').", 15)

    limit = 160 if (channel or "").lower() == "sms" else 320
    if len(text) > limit:
        deduct("length", f"{len(text)} characters — over the ~{limit} sweet spot for "
               f"{channel or 'this channel'}; long blasts get skipped or split-billed.", 8)
    if len(text.strip()) < 10:
        deduct("length", "Message is very short — too little context for a marketing send.", 5)

    score = max(0, min(100, score))
    level = "low" if score >= 80 else "medium" if score >= 55 else "high"
    return {"score": score, "risk": level, "findings": findings,
            "safer_rewrite": safer_rewrite(text, channel)}


def safer_rewrite(text, channel="whatsapp"):
    """Deterministically de-spam a message: tone down caps/urgency, drop
    trigger phrases, guarantee an opt-out line."""
    out = text or ""
    out = re.sub(r"!{2,}", "!", out)
    # ALL-CAPS words (>=3 letters) -> Capitalised, except STOP which is a keyword.
    def fix_caps(m):
        w = m.group(0)
        return w if w in ("STOP",) else w.capitalize()
    out = re.sub(r"\b[A-Z]{3,}\b", fix_caps, out)
    for phrase in ("click here to claim", "click here", "act now", "hurry up",
                   "100% free", "risk-free", "risk free", "guaranteed", "free money"):
        out = re.sub(re.escape(phrase), "", out, flags=re.IGNORECASE)
    out = out.replace("Free", "Complimentary").replace("free", "complimentary")
    out = re.sub(r"\s+", " ", out).replace(" .", ".").replace(" !", "!").strip(" .")
    if out:
        out = out[0].upper() + out[1:]
    if not re.search(r"(stop|opt out|opt-out|unsubscribe)", out.lower()):
        out = (out + " " if out else "") + OPT_OUT_EN
    return out


def check(text, channel="whatsapp", conn=None):
    cfg = get_ai_config(conn)
    result = local_check(text, channel)
    result["engine"] = "local"
    # If Meta AI is configured, let it polish the rewrite — and say so.
    got = _meta_chat(
        "You rewrite marketing messages to be compliant and non-spammy while keeping "
        "the offer. Always keep an opt-out line. Output ONLY the rewritten message.",
        f"Rewrite this {channel} message to be safer:\n\n{text}", cfg)
    if got:
        result["safer_rewrite"] = got
        result["engine"] = "meta-ai"
        result["model"] = cfg["model"]
    return result
