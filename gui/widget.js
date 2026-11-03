(function () {
  var el = document.getElementById('optin-widget');
  if (!el) return;
  el.innerHTML =
    '<style>' +
    '.oim{background:#fff;border:1px solid #e7e0d4;border-radius:16px;padding:18px;font-family:system-ui,sans-serif;max-width:520px;box-shadow:0 1px 3px rgba(22,35,46,.07),0 4px 14px rgba(22,35,46,.05)}' +
    '.oim label{display:block;font-size:13px;color:#6f7a72;margin:10px 0 4px}' +
    '.oim input[type=text],.oim select{width:100%;padding:10px;border:1px solid #e7e0d4;border-radius:8px;font:inherit;box-sizing:border-box;background:#fffdf9}' +
    '.oim .consent{display:flex;gap:8px;align-items:flex-start;font-size:13px;color:#334155;margin-top:12px}' +
    '.oim button{margin-top:14px;width:100%;background:#10b981;color:#fff;border:0;border-radius:9px;padding:11px;font-weight:700;cursor:pointer;font-size:15px}' +
    '.oim button:hover{background:#059669}.oim .msg{font-size:14px;margin-top:10px;color:#065f46}' +
    '</style>' +
    '<form class="oim" id="oim-f">' +
    '<label>Your name</label><input type="text" name="name" placeholder="Your name">' +
    '<label>Phone number or email</label><input type="text" name="identifier" required placeholder="+8801XXXXXXXXX or you@email.com">' +
    '<label>Channel</label><select name="channel"><option value="sms">SMS</option><option value="whatsapp">WhatsApp</option><option value="email">Email</option></select>' +
    '<label>Tags (optional, comma separated)</label><input type="text" name="tags" placeholder="launch, dhaka">' +
    '<div class="consent"><input type="checkbox" required id="oim-c"><label for="oim-c" style="margin:0;color:#334155">I agree to receive messages. I can reply STOP anytime to opt out.</label></div>' +
    '<button type="submit">✅ Subscribe</button><p class="msg" id="oim-m"></p></form>';
  el.querySelector('#oim-f').onsubmit = function (e) {
    e.preventDefault();
    var f = e.target;
    var d = { name: f.name.value, identifier: f.identifier.value, channel: f.channel.value, tags: f.tags.value, source: location.href };
    fetch('/api/optin', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var m = el.querySelector('#oim-m');
        if (j.error) { m.style.color = '#991b1b'; m.textContent = '⚠️ ' + j.error; return; }
        m.style.color = '#065f46';
        m.textContent = '⏳ ' + (j.message || 'Pending confirmation.');
        if (j.confirm_url) m.innerHTML = '⏳ Almost there — <a href="' + j.confirm_url + '"><b>click to confirm your opt-in</b></a> (demo link in dry-run mode).';
      })
      .catch(function () { el.querySelector('#oim-m').textContent = '⚠️ Something went wrong — please try again.'; });
  };
})();
