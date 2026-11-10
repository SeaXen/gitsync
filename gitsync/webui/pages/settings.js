/* Settings — global options, scheduler, notifications, webhooks, scopes. */
(function () {
  var G = window.Gitsync;
  var S = null;   // loaded settings

  function opt(id, cur, opts) {
    return '<select id="' + id + '">' + opts.map(function (o) {
      var v = (o && typeof o === "object") ? o.v : o;
      var l = (o && typeof o === "object") ? o.l : o;
      return '<option value="' + G.esc(v) + '"' + (String(cur) === String(v) ? " selected" : "") + ">" +
        G.esc(l) + "</option>";
    }).join("") + "</select>";
  }
  function f(label, inner, hint) {
    return '<div class="field"><label>' + G.esc(label) + "</label>" + inner +
      (hint ? '<div class="hint">' + G.esc(hint) + "</div>" : "") + "</div>";
  }

  function render(main) {
    main.innerHTML = G.bar("Settings", "Global defaults — a route can override most of these.") +
      '<section class="card"><div class="bar"><span class="route-title">Options</span>' +
        '<div class="actions"><button class="btn primary" id="save-settings">Save</button></div></div>' +
        '<div class="form-grid mt" id="settings-form">' + G.loading() + "</div></section>" +

      '<section class="card"><div class="bar"><span class="route-title">Sign-in</span>' +
        '<div class="actions">' +
        '<button class="btn danger" id="pw-disable">Disable</button>' +
        '<button class="btn primary" id="pw-save">Set password</button></div></div>' +
        '<div class="muted sub" id="pw-state">' + G.loading() + "</div>" +
        '<div class="form-grid mt">' +
        f("Username", '<input type="text" id="pw-user" autocomplete="off" placeholder="e.g. admin">') +
        f("New password", '<input type="password" id="pw-pass" autocomplete="new-password" placeholder="at least 8 characters">') +
        f("Confirm password", '<input type="password" id="pw-confirm" autocomplete="new-password" placeholder="repeat">') +
        "</div>" +
        '<div class="muted mt">Stored as a PBKDF2 hash (mode 0600) — never sent back to the browser. The access token still works for API / curl sign-in.</div>' +
        "</section>" +

      '<section class="card"><div class="bar"><span class="route-title">Scheduler</span>' +
        '<div class="actions"><button class="btn primary" id="install-sched">Install / update</button></div></div>' +
        '<div class="form-grid mt">' +
        f("Schedule", opt("sched-schedule", "", []), "how often the timer runs") +
        f("Status", '<div class="mono" id="sched-status">—</div>', "systemd timer (or cron)") +
        "</div></section>" +

      '<section class="card"><div class="bar"><span class="route-title">Notifications</span>' +
        '<div class="actions"><button class="btn" id="notify-test">Send test</button></div></div>' +
        '<div class="form-grid mt">' +
        f("Notify on", opt("n-on", "", ["failure", "success", "always", "never"])) +
        f("Discord webhook", '<input type="password" id="n-discord" autocomplete="off" placeholder="leave blank to keep">', "write-only; validated on save") +
        f("Telegram bot token", '<input type="password" id="n-tgtoken" autocomplete="off" placeholder="leave blank to keep">', "write-only") +
        f("Telegram chat id", '<input type="text" id="n-tgchat" placeholder="required with token">') +
        "</div></section>" +

      '<section class="card"><div class="bar"><span class="route-title">Webhook receiver</span>' +
        '<div class="actions">' +
        '<button class="btn" id="wh-rotate">Rotate secret</button>' +
        '<button class="btn" id="wh-install">Install service</button>' +
        '<button class="btn danger" id="wh-stop">Stop</button></div></div>' +
        '<div class="mono mt" id="wh-status">—</div>' +
        '<div class="muted mt">Point each platform at ' +
        '<span class="mono">http://&lt;this-box&gt;:8791/hook/&lt;platform&gt;</span>. ' +
        "Push events then trigger an instant sync.</div></section>" +

      '<section class="card" id="scopes"><div class="bar"><span class="route-title">Token scopes</span></div>' +
        '<div id="scopes-body" class="mt">' + G.loading() + "</div></section>" +

      '<section class="card"><p class="label">Paths</p><div class="mono" id="paths">—</div></section>';

    document.getElementById("save-settings").addEventListener("click", saveSettings);
    document.getElementById("install-sched").addEventListener("click", installSched);
    document.getElementById("notify-test").addEventListener("click", notifyTest);
    document.getElementById("pw-save").addEventListener("click", savePassword);
    document.getElementById("pw-disable").addEventListener("click", disablePassword);
    document.getElementById("wh-rotate").addEventListener("click", function () { wh("rotate"); });
    document.getElementById("wh-install").addEventListener("click", function () { wh("install"); });
    document.getElementById("wh-stop").addEventListener("click", function () { wh("stop"); });

    load();
    loadSignin();
    loadWebhooks();
    loadScopes();
  }

  function load() {
    G.api("GET", "/api/settings").then(function (d) {
      S = d.settings;
      var scheduleOpts = Object.keys(S.schedules || {}).map(function (k) {
        return { v: k, l: S.schedules[k] };
      });
      document.getElementById("settings-form").innerHTML =
        f("Parallel workers", opt("s-workers", S.workers, [1, 2, 4, 8]), "repos synced in parallel") +
        f("Mirror mode", opt("s-mode", S.mode, [{ v: "safe", l: "safe (never delete)" }, { v: "full", l: "full (prunes)" }])) +
        f("Mesh conflicts", opt("s-conflict", S.mesh_conflict, [{ v: "newest", l: "newest wins" }, { v: "skip", l: "skip (report)" }])) +
        f("Schedule", opt("s-schedule", S.schedule, scheduleOpts)) +
        f("Include forks", '<label class="check"><input type="checkbox" id="s-forks"' + (S.include_forks ? " checked" : "") + "> mirror forked repos</label>") +
        f("Pause between repos", opt("s-pace", S.pace || 0, [0, 1, 2, 5, 10]), "seconds — spare laptop heat / disk") +
        f("Token warning", opt("s-warn", S.token_warn_days || 30, [7, 14, 30, 60, 90]), "days before a credential warns") +
        f("Theme", opt("s-theme", S.theme, (S.themes || []))) +
        f("Relax protection", '<label class="check"><input type="checkbox" id="s-relax"' + (S.relax_protection ? " checked" : "") + "> allow force-push over branch protection</label>", "hand-edit territory — off by default");
      // scheduler card mirrors the chosen schedule (its own select)
      var schedSel = document.getElementById("sched-schedule");
      schedSel.innerHTML = scheduleOpts.map(function (o) {
        return '<option value="' + G.esc(o.v) + '"' + (String(S.schedule) === String(o.v) ? " selected" : "") + ">" +
          G.esc(o.l) + "</option>";
      }).join("");
      document.getElementById("sched-status").textContent = S.scheduler || "—";
      document.getElementById("n-on").value = (S.notify && S.notify.on) || "failure";
      document.getElementById("paths").innerHTML =
        "config " + G.esc(S.paths.config) + "<br>mirrors " + G.esc(S.paths.mirror) +
        "<br>log " + G.esc(S.paths.log) + "<br>data " + G.esc(S.paths.data);
    }).catch(function (e) { document.getElementById("settings-form").innerHTML = G.errorBox(e); });
  }

  function saveSettings() {
    var body = {
      workers: document.getElementById("s-workers").value,
      mode: document.getElementById("s-mode").value,
      mesh_conflict: document.getElementById("s-conflict").value,
      schedule: document.getElementById("s-schedule").value,
      include_forks: document.getElementById("s-forks").checked,
      pace: document.getElementById("s-pace").value,
      token_warn_days: document.getElementById("s-warn").value,
      theme: document.getElementById("s-theme").value,
      relax_protection: document.getElementById("s-relax").checked,
      on: document.getElementById("n-on").value
    };
    var dv = document.getElementById("n-discord").value.trim();
    if (dv) body.discord_webhook = dv;
    var tv = document.getElementById("n-tgtoken").value.trim();
    var cv = document.getElementById("n-tgchat").value.trim();
    if (tv) { body.telegram_token = tv; body.telegram_chat_id = cv; }
    G.api("PUT", "/api/settings", body).then(function () {
      G.toast("settings saved", "ok");
      document.getElementById("n-discord").value = "";
      document.getElementById("n-tgtoken").value = "";
    }).catch(function (e) { G.toast(e.error, "bad"); });
  }

  function installSched() {
    var sched = document.getElementById("sched-schedule").value;
    G.api("POST", "/api/scheduler/install", { schedule: sched })
      .then(function (d) {
        G.toast(d.status, "ok");
        document.getElementById("sched-status").textContent = d.status;
      }).catch(function (e) { G.toast(e.error, "bad"); });
  }

  function notifyTest() {
    G.api("POST", "/api/notify/test").then(function (d) {
      G.toast(d.message, d.ok ? "ok" : "bad");
    }).catch(function (e) { G.toast(e.error, "bad"); });
  }

  function loadSignin() {
    G.api("GET", "/api/auth").then(function (d) {
      var el = document.getElementById("pw-state");
      el.innerHTML = d.password_set
        ? 'Password sign-in is <span class="ok">on</span> for user ' +
          '<span class="mono">' + G.esc(d.username) + "</span>."
        : 'Password sign-in is <span class="warn">off</span> — you are using ' +
          "the access token. Set a username &amp; password to enable it.";
      if (d.password_set && d.username) {
        document.getElementById("pw-user").value = d.username;
      }
    }).catch(function () {});
  }

  function savePassword() {
    var u = document.getElementById("pw-user").value.trim();
    var p = document.getElementById("pw-pass").value;
    var c = document.getElementById("pw-confirm").value;
    if (!u) return G.toast("enter a username", "warn");
    if (p.length < 8) return G.toast("password must be at least 8 characters", "warn");
    if (p !== c) return G.toast("passwords do not match", "warn");
    G.api("POST", "/api/auth/password", { username: u, password: p })
      .then(function (d) {
        G.toast(d.message || "password set", "ok");
        document.getElementById("pw-pass").value = "";
        document.getElementById("pw-confirm").value = "";
        loadSignin();
      }).catch(function (e) { G.toast(e.error, "bad"); });
  }

  function disablePassword() {
    if (!confirm("Disable password sign-in? The access token will be the only way in.")) return;
    G.api("POST", "/api/auth/password", { disable: true })
      .then(function (d) { G.toast(d.message || "disabled", "ok"); loadSignin(); })
      .catch(function (e) { G.toast(e.error, "bad"); });
  }

  function wh(action) {
    G.api("POST", "/api/webhooks", { action: action }).then(function (d) {
      G.toast(d.note || d.status || action, d.ok ? "ok" : "bad");
      loadWebhooks();
    }).catch(function (e) { G.toast(e.error, "bad"); });
  }

  function loadWebhooks() {
    G.api("GET", "/api/webhooks").then(function (d) {
      var eps = (d.endpoints || []).map(function (e) {
        return '<div>http://&lt;this-box&gt;:8791' + G.esc(e.path) + " <span class='muted'>(" + G.esc(e.type) + ")</span></div>";
      }).join("");
      document.getElementById("wh-status").innerHTML =
        "secret: " + (d.secret_set ? '<span class="ok">set</span>' : '<span class="warn">not set</span>') +
        " · receiver: " + (d.active ? '<span class="ok">live</span>' : '<span class="muted">not running</span>') +
        (eps ? '<div class="mt">' + eps + "</div>" : "");
    }).catch(function () {});
  }

  function loadScopes() {
    G.api("GET", "/api/scopes").then(function (d) {
      document.getElementById("scopes-body").innerHTML = (d.scopes || []).map(function (g) {
        var scopes = (g.scopes || []).map(function (s) {
          return '<div class="mono muted">· ' + G.esc(s.scope) + " — " + G.esc(s.why) + "</div>";
        }).join("");
        return '<div class="mb"><strong>' + G.esc(g.title) + '</strong> ' +
          '<span class="badge muted">min: ' + G.esc(g.minimum) + "</span>" +
          (g.env ? ' <span class="badge muted">env ' + G.esc(g.env) + "</span>" : "") +
          (g.token_url ? '<div class="muted" style="font-size:12px">create at ' + G.esc(g.token_url) + "</div>" : "") +
          scopes + "</div>";
      }).join("");
    }).catch(function (e) { document.getElementById("scopes-body").innerHTML = G.errorBox(e); });
  }

  G.mount("settings", render);
})();
