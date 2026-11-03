// Opt-In Message — Simple App edition (v0.7.0)
// The whole product runs inside this page: no server, no engine, no
// network calls. Data lives in the app's own local storage on this
// device and never leaves it. Sending is simulated by design — this
// edition teaches and enforces the consent flow; real sending lives in
// the optional self-hosted server edition (see the repo README).
"use strict";

/* ---------------- constants ---------------- */

var CONSENT_VERSION = "v1-2026";
var BUSINESS = "Dhaka Threads (demo business)";
var CONSENT_TEXT =
  "I agree to receive offers and updates from " + BUSINESS + " on the " +
  "channel I chose. I understand I can reply STOP at any time to stop " +
  "messages, and that nothing is sent to me until I confirm my subscription.";
var STORE_KEY = "optin-message-simple-v1";

/* ---------------- tiny helpers ---------------- */

function $(id) { return document.getElementById(id); }

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function now() { return new Date().toISOString(); }

function fmtTime(iso) {
  if (!iso) return "";
  var d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit"
  });
}

function uid() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID().slice(0, 8);
  return "id" + Math.random().toString(36).slice(2, 10);
}

/* ---------------- state (localStorage) ---------------- */

function seedContacts() {
  var t = now();
  return [
    {
      id: uid(), name: "Arif Rahman", address: "+8801711000001", channel: "WhatsApp",
      status: "subscribed", demo: true, created_at: t, confirmed_at: t, stopped_at: null,
      proof: { at: t, method: "demo seed (double opt-in)", consent_version: CONSENT_VERSION, consent_text: CONSENT_TEXT }
    },
    {
      id: uid(), name: "Fatema Begum", address: "+8801711000002", channel: "SMS",
      status: "subscribed", demo: true, created_at: t, confirmed_at: t, stopped_at: null,
      proof: { at: t, method: "demo seed (double opt-in)", consent_version: CONSENT_VERSION, consent_text: CONSENT_TEXT }
    },
    {
      id: uid(), name: "Tanvir Hasan", address: "tanvir@example.com", channel: "Email",
      status: "pending", demo: true, created_at: t, confirmed_at: null, stopped_at: null, proof: null
    }
  ];
}

function freshState(withDemo) {
  var s = { contacts: withDemo ? seedContacts() : [], campaigns: [], audit: [] };
  if (withDemo) {
    s.audit.unshift({ at: now(), event: "system.seeded", detail: "3 demo contacts loaded (Dhaka Threads) so the app is not empty. Use “Reset demo data” in Contacts to reload them, or “Clear all data” to start empty." });
  }
  return s;
}

function loadState() {
  try {
    var raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      var s = JSON.parse(raw);
      if (s && Array.isArray(s.contacts) && Array.isArray(s.campaigns) && Array.isArray(s.audit)) return s;
    }
  } catch (e) { /* corrupted storage falls through to a fresh seed */ }
  var fresh = freshState(true);
  saveState(fresh);
  return fresh;
}

function saveState(s) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch (e) { /* storage full/blocked: app keeps running in-memory */ }
}

var state = loadState();

function audit(event, detail) {
  state.audit.unshift({ at: now(), event: event, detail: detail || "" });
  while (state.audit.length > 300) state.audit.pop();
}

/* ---------------- risk check (plain rules, no AI, no network) ----------------
   Ported from the sandbox edition so the app and the online demo agree. */

var SPAM_PHRASES = [
  ["free money", "a special offer"],
  ["100% free", "a special offer"],
  ["guaranteed", ""],
  ["win cash", ""],
  ["you won", ""],
  ["lottery", ""],
  ["click now", ""],
  ["act now", ""],
  ["limited time", ""],
  ["urgent", ""],
  ["hurry", ""],
  ["expires soon", ""],
  ["no risk", ""],
  ["risk-free", ""],
  ["earn cash", ""],
  ["congratulations", ""],
  ["prize", ""],
  ["winner", ""],
  ["make money fast", ""],
  ["double your money", ""]
];

function riskCheck(message) {
  var flags = [];
  var score = 100;
  var text = String(message || "");

  var links = (text.match(/(https?:\/\/|www\.)/gi) || []).length;
  if (links > 1) {
    score -= 12 * (links - 1);
    flags.push(links + " links in one message — carriers and WhatsApp treat link-heavy messages as spam.");
  }

  var words = text.split(/\s+/).filter(Boolean);
  var capsWords = words.filter(function (w) { return w.length >= 3 && w === w.toUpperCase() && /[A-Z]/.test(w); });
  if (words.length > 0 && capsWords.length >= 3 && capsWords.length / words.length > 0.3) {
    score -= 25;
    flags.push("Lots of ALL-CAPS words — it reads as shouting and trips spam filters.");
  }

  var lower = text.toLowerCase();
  var found = [];
  for (var i = 0; i < SPAM_PHRASES.length; i++) {
    if (lower.indexOf(SPAM_PHRASES[i][0]) !== -1) found.push(SPAM_PHRASES[i][0]);
  }
  if (found.length) {
    score -= Math.min(45, 15 * found.length);
    flags.push("Spam-trigger / urgency phrases found: " + found.map(function (f) { return "\u201C" + f + "\u201D"; }).join(", ") + ".");
  }

  var bangs = (text.match(/!/g) || []).length;
  if (bangs > 2) {
    score -= 10;
    flags.push(bangs + " exclamation marks — one is plenty.");
  }
  if (/[!$]{3,}/.test(text)) {
    score -= 5;
    flags.push("Repeated punctuation like !!! or $$$ looks like a scam template.");
  }
  if (text.length > 480) {
    score -= 5;
    flags.push("Very long message (" + text.length + " characters) — shorter messages get read.");
  }

  var hasOptOut = /\bstop\b/i.test(text);
  if (!hasOptOut) {
    score -= 15;
    flags.push("No opt-out line — always tell people how to stop (e.g. “Reply STOP to stop messages”). WhatsApp and carriers expect it.");
  }

  score = Math.max(0, Math.min(100, score));
  var rating = score >= 80 ? "Looks safe" : score >= 50 ? "Review carefully" : "High risk — likely to be flagged or get the number banned";

  var suggestion = text;
  for (var j = 0; j < SPAM_PHRASES.length; j++) {
    var phrase = SPAM_PHRASES[j][0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    suggestion = suggestion.replace(new RegExp(phrase, "gi"), SPAM_PHRASES[j][1]);
  }
  suggestion = suggestion.replace(/\b[A-Z]{3,}\b/g, function (w) { return w.charAt(0) + w.slice(1).toLowerCase(); });
  suggestion = suggestion.replace(/!{2,}/g, ".");
  suggestion = suggestion.replace(/\s{2,}/g, " ").trim();
  if (!/\bstop\b/i.test(suggestion) && suggestion) suggestion += " Reply STOP to stop messages.";
  suggestion = suggestion.replace(/\s{2,}/g, " ").trim();

  return { score: score, rating: rating, flags: flags, suggestion: suggestion };
}

/* ---------------- flash ---------------- */

var flashMsg = "";
function flash(msg) { flashMsg = msg || ""; renderFlash(); }
function renderFlash() {
  var el = $("flash");
  if (!flashMsg) { el.style.display = "none"; el.textContent = ""; return; }
  el.style.display = "block";
  el.textContent = flashMsg;
}

/* ---------------- tabs ---------------- */

var currentTab = "overview";
function showTab(name) {
  currentTab = name;
  var tabs = ["overview", "contacts", "campaigns", "audit"];
  for (var i = 0; i < tabs.length; i++) {
    $("tab-" + tabs[i]).style.display = tabs[i] === name ? "block" : "none";
    $("nav-" + tabs[i]).classList.toggle("active", tabs[i] === name);
  }
  render();
}

/* ---------------- contacts ---------------- */

function findByAddress(address) {
  var a = String(address || "").toLowerCase();
  for (var i = 0; i < state.contacts.length; i++) {
    if (state.contacts[i].address.toLowerCase() === a) return state.contacts[i];
  }
  return null;
}

function handleSubscribe(ev) {
  ev.preventDefault();
  var name = $("f-name").value.trim();
  var address = $("f-address").value.trim();
  var channel = $("f-channel").value;
  var consent = $("f-consent").checked;
  if (!name || !address) { flash("Please add a name and a mobile number or email."); return; }
  if (!consent) { flash("The consent box must be ticked — that is the whole point of Opt-In Message."); return; }

  var existing = findByAddress(address);
  if (existing && existing.status === "stopped") {
    flash("This contact has stopped messages. STOP is permanent in Opt-In Message, so " + address + " cannot be re-added.");
    audit("contact.blocked", address + " tried to subscribe again after STOP — blocked (STOP is permanent).");
    saveState(state); render();
    return;
  }
  if (existing && existing.status === "subscribed") {
    flash(address + " is already subscribed, with a saved consent proof.");
    return;
  }
  if (existing && existing.status === "pending") {
    flash(address + " is already waiting for confirmation. Use the Confirm button next to their name in the list below.");
    showTab("contacts");
    return;
  }
  state.contacts.push({
    id: uid(), name: name, address: address, channel: channel,
    status: "pending", demo: false, created_at: now(), confirmed_at: null, stopped_at: null, proof: null
  });
  audit("contact.pending", name + " (" + address + ", " + channel + ") filled the opt-in form and ticked consent " + CONSENT_VERSION + " — waiting for confirmation. Nothing can be sent yet.");
  saveState(state);
  $("subscribe-form").reset();
  flash("Saved. " + name + " is now Pending — in real life they would get a confirmation message. Press Confirm next to their name to simulate them replying YES.");
  render();
}

function confirmContact(id) {
  for (var i = 0; i < state.contacts.length; i++) {
    var c = state.contacts[i];
    if (c.id === id && c.status === "pending") {
      c.status = "subscribed";
      c.confirmed_at = now();
      c.proof = { at: c.confirmed_at, method: "double opt-in (form + confirmation)", consent_version: CONSENT_VERSION, consent_text: CONSENT_TEXT };
      audit("contact.confirmed", c.name + " (" + c.address + ") confirmed — consent proof saved (" + CONSENT_VERSION + ").");
      saveState(state);
      flash(c.name + " confirmed. Consent proof saved — they can now receive approved campaigns.");
      render();
      return;
    }
  }
}

function stopContact(id) {
  for (var i = 0; i < state.contacts.length; i++) {
    var c = state.contacts[i];
    if (c.id === id && c.status !== "stopped") {
      c.status = "stopped";
      c.stopped_at = now();
      audit("contact.stopped", c.name + " (" + c.address + ") stopped — can never be messaged again.");
      saveState(state);
      flash(c.name + " is now STOPPED — permanent. They will be skipped by every campaign.");
      render();
      return;
    }
  }
}

function handleStopForm(ev) {
  ev.preventDefault();
  var address = $("stop-address").value.trim();
  var c = findByAddress(address);
  if (c && c.status !== "stopped") {
    c.status = "stopped";
    c.stopped_at = now();
    audit("contact.stopped", c.name + " (" + c.address + ") stopped via the STOP form — can never be messaged again.");
    saveState(state);
    flash("Done — " + address + " has been stopped and can never be messaged again.");
  } else if (c && c.status === "stopped") {
    flash(address + " is already stopped.");
  } else {
    flash("No contact found for " + address + " — nothing to stop.");
  }
  $("stop-form").reset();
  render();
}

function resetDemo() {
  state = freshState(true);
  saveState(state);
  flash("Demo data reset — the 3 labelled Dhaka Threads demo contacts are back, campaigns and audit log cleared.");
  render();
}

function clearAll() {
  if (!window.confirm("Clear ALL data on this device? Contacts, campaigns and the audit log will be emptied. The demo contacts will not come back until you choose “Reset demo data”.")) return;
  state = freshState(false);
  audit("system.cleared", "All local data cleared by the user.");
  saveState(state);
  flash("All data cleared. The app is empty — add your first contact with the opt-in form.");
  render();
}

/* ---------------- campaigns ---------------- */

function updateRiskPreview() {
  var msg = $("c-message").value;
  var box = $("risk-preview");
  if (!msg.trim()) { box.style.display = "none"; return; }
  var r = riskCheck(msg);
  box.style.display = "block";
  var html = "<div class=\"risk\">Risk score: " + r.score + "/100 — " + esc(r.rating) + "</div>";
  if (r.flags.length) {
    html += "<ul>";
    for (var i = 0; i < r.flags.length; i++) html += "<li>" + esc(r.flags[i]) + "</li>";
    html += "</ul>";
    if (r.suggestion && r.suggestion !== msg) html += "<p><strong>Safer way to say it:</strong> " + esc(r.suggestion) + "</p>";
  } else {
    html += "<p>No problems found by the rule check.</p>";
  }
  box.innerHTML = html;
}

function handleCreateCampaign(ev) {
  ev.preventDefault();
  var message = $("c-message").value.trim();
  if (!message) { flash("Write a message first."); return; }
  var risk = riskCheck(message);
  state.campaigns.unshift({
    id: uid(), message: message, risk: risk, status: "waiting",
    created_at: now(), approved_at: null, sent_at: null, deliveries: []
  });
  audit("campaign.created", "Campaign saved with risk score " + risk.score + "/100 (" + risk.rating + "). Waiting for a human to approve it — sending is blocked until then.");
  saveState(state);
  $("campaign-form").reset();
  updateRiskPreview();
  flash("Campaign saved. Risk score " + risk.score + "/100 — " + risk.rating + ". It is waiting for approval below; it cannot be sent yet.");
  render();
}

function approveCampaign(id) {
  for (var i = 0; i < state.campaigns.length; i++) {
    var c = state.campaigns[i];
    if (c.id === id && c.status === "waiting") {
      c.status = "approved";
      c.approved_at = now();
      audit("campaign.approved", "A human approved the campaign: \u201C" + c.message.slice(0, 80) + "\u201D");
      saveState(state);
      flash("Campaign approved by a human. You can now send it (simulated).");
      render();
      return;
    }
  }
}

function sendCampaign(id) {
  for (var i = 0; i < state.campaigns.length; i++) {
    var c = state.campaigns[i];
    if (c.id === id) {
      if (c.status !== "approved") {
        flash("Only an approved campaign can be sent — the approval gate is doing its job.");
        return;
      }
      var at = now();
      var targets = state.contacts.filter(function (x) { return x.status === "subscribed"; });
      c.deliveries = targets.map(function (t) {
        return { contact_id: t.id, name: t.name, address: t.address, channel: t.channel, status: "simulated", at: at };
      });
      c.status = "sent";
      c.sent_at = at;
      audit("campaign.sent_simulated", "Campaign \u201C" + c.message.slice(0, 80) + "\u201D simulated-sent to " + targets.length + " subscribed contact(s). Pending and stopped contacts were skipped. No real messages sent.");
      saveState(state);
      flash("Simulated send complete: " + targets.length + " subscribed contact(s) logged. Pending and stopped contacts received nothing, as designed.");
      render();
      return;
    }
  }
}

function useSuggestion(id) {
  for (var i = 0; i < state.campaigns.length; i++) {
    var c = state.campaigns[i];
    if (c.id === id && c.risk && c.risk.suggestion && c.risk.suggestion !== c.message) {
      c.message = c.risk.suggestion;
      c.risk = riskCheck(c.message);
      audit("campaign.edited", "Campaign text replaced with the safer suggestion. New risk score " + c.risk.score + "/100.");
      saveState(state);
      flash("Safer text applied. New risk score " + c.risk.score + "/100.");
      render();
      return;
    }
  }
}

/* ---------------- rendering ---------------- */

function statusPill(status) {
  if (status === "subscribed") return "<span class=\"pill sub\">Subscribed</span>";
  if (status === "pending") return "<span class=\"pill pen\">Pending</span>";
  if (status === "stopped") return "<span class=\"pill sto\">Stopped</span>";
  if (status === "waiting") return "<span class=\"pill wait\">Waiting for approval</span>";
  if (status === "approved") return "<span class=\"pill appr\">Approved</span>";
  if (status === "sent") return "<span class=\"pill sent\">Sent (simulated)</span>";
  return esc(status);
}

function renderOverview() {
  var nSub = state.contacts.filter(function (c) { return c.status === "subscribed"; }).length;
  var nPen = state.contacts.filter(function (c) { return c.status === "pending"; }).length;
  var nSto = state.contacts.filter(function (c) { return c.status === "stopped"; }).length;
  $("ov-stats").innerHTML =
    "<div class=\"card statcard\"><div class=\"stat\" style=\"color:#0EA371\">" + nSub + "</div><p class=\"muted\">Subscribed<br>(with consent proof)</p></div>" +
    "<div class=\"card statcard\"><div class=\"stat\" style=\"color:#D97706\">" + nPen + "</div><p class=\"muted\">Pending<br>confirmation</p></div>" +
    "<div class=\"card statcard\"><div class=\"stat\" style=\"color:#DC2626\">" + nSto + "</div><p class=\"muted\">Stopped — never<br>messaged again</p></div>" +
    "<div class=\"card statcard\"><div class=\"stat\">" + state.campaigns.length + "</div><p class=\"muted\">Campaigns<br>created</p></div>";
}

function renderContacts() {
  var q = ($("contact-search").value || "").toLowerCase();
  var f = $("contact-filter").value;
  var rows = "";
  var shown = 0;
  for (var i = 0; i < state.contacts.length; i++) {
    var c = state.contacts[i];
    if (f !== "all" && c.status !== f) continue;
    if (q && (c.name + " " + c.address + " " + c.channel).toLowerCase().indexOf(q) === -1) continue;
    shown++;
    var proof = c.proof
      ? esc(fmtTime(c.proof.at)) + "<br><span class=\"muted\">" + esc(c.proof.method) + " · consent text " + esc(c.proof.consent_version) + "</span>"
      : "<span class=\"muted\">not confirmed yet</span>";
    var actions = "";
    if (c.status === "pending") actions += "<button class=\"btn small\" data-act=\"confirm\" data-id=\"" + esc(c.id) + "\">Confirm (simulate YES)</button> ";
    if (c.status !== "stopped") actions += "<button class=\"btn stop small\" data-act=\"stop\" data-id=\"" + esc(c.id) + "\">STOP</button>";
    rows += "<tr><td><strong>" + esc(c.name) + "</strong>" + (c.demo ? " <span class=\"demo\">DEMO</span>" : "") +
      "</td><td>" + esc(c.address) + "<br><span class=\"muted\">" + esc(c.channel) + "</span></td><td>" +
      statusPill(c.status) + "</td><td class=\"small\">" + proof + "</td><td class=\"actions\">" + actions + "</td></tr>";
  }
  if (!shown) rows = "<tr><td colspan=\"5\" class=\"muted\">No contacts match. Add one with the opt-in form above.</td></tr>";
  $("contacts-body").innerHTML = rows;
}

function renderCampaigns() {
  var box = $("campaigns-list");
  if (!state.campaigns.length) {
    box.innerHTML = "<p class=\"muted\">No campaigns yet. Write the first one above.</p>";
    return;
  }
  var html = "";
  for (var i = 0; i < state.campaigns.length; i++) {
    var c = state.campaigns[i];
    html += "<div class=\"campaign\">";
    html += "<p><strong>" + esc(c.message) + "</strong></p>";
    html += "<p class=\"small\">Status: " + statusPill(c.status) + " · Risk score: <strong>" + c.risk.score + "/100</strong> — " + esc(c.risk.rating) + " · Created " + esc(fmtTime(c.created_at)) + "</p>";
    if (c.risk.flags && c.risk.flags.length) {
      html += "<ul class=\"small flags\">";
      for (var j = 0; j < c.risk.flags.length; j++) html += "<li>" + esc(c.risk.flags[j]) + "</li>";
      html += "</ul>";
      if (c.risk.suggestion && c.risk.suggestion !== c.message) {
        html += "<p class=\"small\"><strong>Safer way to say it:</strong> " + esc(c.risk.suggestion) + " <button class=\"btn secondary small\" data-act=\"suggest\" data-id=\"" + esc(c.id) + "\">Use safer text</button></p>";
      }
    }
    if (c.status === "waiting") {
      html += "<p><button class=\"btn secondary small\" data-act=\"approve\" data-id=\"" + esc(c.id) + "\">Approve this campaign</button> " +
        "<button class=\"btn small\" disabled title=\"Approval first\">Send — locked until approved</button></p>" +
        "<p class=\"small muted\">The human approval gate: sending stays locked until a person approves.</p>";
    } else if (c.status === "approved") {
      html += "<p><button class=\"btn small\" data-act=\"send\" data-id=\"" + esc(c.id) + "\">Send (simulated)</button></p>";
    } else if (c.status === "sent") {
      html += "<p class=\"small muted\">Simulated delivery log — " + (c.deliveries ? c.deliveries.length : 0) + " subscribed contact(s) at " + esc(fmtTime(c.sent_at)) + ". No real messages were sent:</p>";
      if (c.deliveries && c.deliveries.length) {
        html += "<ul class=\"small\">";
        for (var k = 0; k < c.deliveries.length; k++) {
          var d = c.deliveries[k];
          html += "<li>" + esc(d.name) + " — " + esc(d.address) + " (" + esc(d.channel) + ") · simulated</li>";
        }
        html += "</ul>";
      }
    }
    html += "</div>";
  }
  box.innerHTML = html;
}

function renderAudit() {
  var rows = "";
  var items = state.audit.slice(0, 100);
  if (!items.length) rows = "<tr><td colspan=\"3\" class=\"muted\">Nothing yet.</td></tr>";
  for (var i = 0; i < items.length; i++) {
    var a = items[i];
    rows += "<tr><td class=\"small\">" + esc(fmtTime(a.at)) + "</td><td><strong>" + esc(a.event) + "</strong></td><td class=\"small\">" + esc(a.detail) + "</td></tr>";
  }
  $("audit-body").innerHTML = rows;
}

function render() {
  renderFlash();
  renderOverview();
  renderContacts();
  renderCampaigns();
  renderAudit();
}

/* ---------------- wire up ---------------- */

document.addEventListener("DOMContentLoaded", function () {
  $("consent-text").textContent = CONSENT_TEXT + " (consent text " + CONSENT_VERSION + ")";
  $("subscribe-form").addEventListener("submit", handleSubscribe);
  $("stop-form").addEventListener("submit", handleStopForm);
  $("campaign-form").addEventListener("submit", handleCreateCampaign);
  $("c-message").addEventListener("input", updateRiskPreview);
  $("contact-search").addEventListener("input", renderContacts);
  $("contact-filter").addEventListener("change", renderContacts);
  $("reset-demo").addEventListener("click", resetDemo);
  $("clear-all").addEventListener("click", clearAll);

  var navs = ["overview", "contacts", "campaigns", "audit"];
  for (var i = 0; i < navs.length; i++) {
    (function (name) {
      $("nav-" + name).addEventListener("click", function () { showTab(name); });
    })(navs[i]);
  }

  // One delegated handler for every per-row action button.
  document.body.addEventListener("click", function (ev) {
    var btn = ev.target && ev.target.closest ? ev.target.closest("[data-act]") : null;
    if (!btn) return;
    var id = btn.getAttribute("data-id");
    var act = btn.getAttribute("data-act");
    if (act === "confirm") confirmContact(id);
    else if (act === "stop") stopContact(id);
    else if (act === "approve") approveCampaign(id);
    else if (act === "send") sendCampaign(id);
    else if (act === "suggest") useSuggestion(id);
  });

  render();
});
