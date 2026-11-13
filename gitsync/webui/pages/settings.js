/* Settings — global options, scheduler, notifications, webhooks, scopes. */
(function () {
  var G = window.Gitsync;
  var S = null;   // loaded settings

  function f(label, inner, hint) { return G.field(label, inner, hint); }

  function render(main) {
    main.innerHTML = G.bar("Settings", "Global defaults — a route can override most of these.") +
      '<section class="card"><div class="bar"><span class="route-title">Options <span class="chip warn hide u-fs11" id="settings-dirty">● Unsaved changes</span></span>' +
        '<div class="actions"><button class="btn primary" id="save-settings">Save</button></div></div>' +
        '<div class="form-grid mt" id="settings-form">' + G.loading() + "</div></section>" +

      '<section class="card"><div class="bar"><span class="route-title">Appearance</span></div>' +
        '<div class="form-grid mt">' +
        f("Web UI theme",
          G.select("s-apptheme", G.theme(),
            [{ v: "auto", l: "Auto — follow my system" },
             { v: "light", l: "Light" }, { v: "dark", l: "Dark" }]),
          "Saved in this browser only, so it never changes anyone else's view") +
        "</div>" +
        '<div class="muted mt">Prefer the terminal? The <span class="mono">theme</span> above (TUI theme) colours the curses dashboard instead.</div>' +
        "</section>" +

      '<section class="card"><div class="bar"><span class="route-title">Sign-in</span>' +
        '<div class="actions">' +
        '<button class="btn danger" id="pw-disable">Disable Password</button>' +
        '<button class="btn primary" id="pw-save">Set Password</button></div></div>' +
        '<div class="muted sub" id="pw-state">' + G.loading() + "</div>" +
        '<div class="form-grid mt">' +
        f("Username", '<input type="text" id="pw-user" name="username" autocomplete="username" spellcheck="false" placeholder="e.g. admin…">') +
        f("New password", '<input type="password" id="pw-pass" name="new_password" autocomplete="new-password" spellcheck="false" placeholder="at least 8 characters…">') +
        f("Confirm password", '<input type="password" id="pw-confirm" name="confirm_password" autocomplete="new-password" spellcheck="false" placeholder="repeat password…">') +
        "</div>" +
        '<div class="muted mt">Stored as a PBKDF2 hash (mode 0600) — never sent back to the browser. The access token still works for API / curl sign-in.</div>' +
        '<p class="label mt">Active sessions</p>' +
        '<div id="sessions-body" class="stack">' + G.loading() + "</div>" +
        '<div class="muted mt u-fs125">A session signs itself out after 12 hours of inactivity or 30 days. Changing the password signs out every other browser.</div>' +
        '<div class="mt"><button class="btn danger" id="sessions-revoke-all">Sign out everywhere</button> ' +
        '<button class="btn ghost" id="sessions-refresh">Refresh</button></div>' +
        "</section>" +

      '<section class="card" id="scheduler"><div class="bar"><span class="route-title">Scheduler</span>' +
        '<div class="actions"><button class="btn primary" id="install-sched">Install / Update</button></div></div>' +
        '<div class="muted" id="sched-status">—</div>' +
        '<div class="muted mt u-fs125">Uses the Schedule chosen in Options above. Runs <span class="mono">gitsync sync --scheduled</span> on a systemd timer (or cron).</div>' +
        "</section>" +

      '<section class="card"><div class="bar"><span class="route-title">Notifications</span>' +
        '<div class="actions">' +
        '<button class="btn sm" id="n-clear-discord" title="Remove the stored Discord webhook">Clear Discord</button>' +
        '<button class="btn sm" id="n-clear-telegram" title="Remove the stored Telegram token">Clear Telegram</button>' +
        '<button class="btn" id="notify-test">Send Test</button></div></div>' +
        '<div class="form-grid mt">' +
        f("Notify on", G.select("n-on", "", ["failure", "success", "always", "never"])) +
        f("Discord webhook", '<input type="password" id="n-discord" name="discord_webhook" autocomplete="off" spellcheck="false" placeholder="leave blank to keep…">', "write-only; validated on save") +
        f("Telegram bot token", '<input type="password" id="n-tgtoken" name="telegram_token" autocomplete="off" spellcheck="false" placeholder="leave blank to keep…">', "write-only") +
        f("Telegram chat id", '<input type="text" id="n-tgchat" name="telegram_chat_id" placeholder="required with token…">') +
        "</div></section>" +

      '<section class="card"><div class="bar"><span class="route-title">Webhook receiver</span>' +
        '<div class="actions">' +
        '<button class="btn" id="wh-rotate">Rotate Secret</button>' +
        '<button class="btn" id="wh-install">Install Service</button>' +
        '<button class="btn danger" id="wh-stop">Stop</button></div></div>' +
        '<div class="mono mt" id="wh-status">—</div>' +
        '<div class="muted mt">Point each platform at ' +
        '<span class="mono">http://&lt;this-box&gt;:8791/hook/&lt;platform&gt;</span>. ' +
        "Push events then trigger an instant sync.</div></section>" +

      '<section class="card" id="scopes" tabindex="-1"><div class="bar"><span class="route-title">Token scopes</span></div>' +
        '<div id="scopes-body" class="mt">' + G.loading() + "</div></section>" +

      '<section class="card"><p class="label">Paths</p><div class="mono" id="paths">—</div></section>';

    document.getElementById("save-settings").addEventListener("click", function () { G.busy(this, saveSettings); });
    document.getElementById("install-sched").addEventListener("click", function () { G.busy(this, installSched); });
    document.getElementById("notify-test").addEventListener("click", notifyTest);
    document.getElementById("n-clear-discord").addEventListener("click", function () { G.busy(this, function () { return clearNotify(["discord"]); }); });
    document.getElementById("n-clear-telegram").addEventListener("click", function () { G.busy(this, function () { return clearNotify(["telegram"]); }); });
    document.getElementById("s-apptheme").addEventListener("change", function () {
      G.setTheme(this.value);
    });
    document.getElementById("sessions-refresh").addEventListener("click", function () {
      G.busy(this, loadSessions);
    });
    document.getElementById("sessions-revoke-all").addEventListener("click", signOutEverywhere);
    document.getElementById("pw-save").addEventListener("click", function () { G.busy(this, savePassword); });
    document.getElementById("pw-disable").addEventListener("click", disablePassword);
    document.getElementById("wh-rotate").addEventListener("click", function () { wh("rotate"); });
    document.getElementById("wh-install").addEventListener("click", function () { wh("install"); });
    document.getElementById("wh-stop").addEventListener("click", function () { wh("stop"); });

    load();
    loadWebhooks();
    loadScopes();
    loadSessions();
  }

  // ---- sessions ----
  // The cookie is an opaque server-side id, so these rows *are* the live
  // logins — signing one out is instant and cannot be undone by the old
  // cookie being replayed.
  function when(ts) {
    if (!ts) return "—";
    try {
      return new Date(Number(ts) * 1000).toLocaleString();
    } catch (e) { return "—"; }
  }

  function uaLabel(ua) {
    ua = String(ua || "");
    var os = /Windows/i.test(ua) ? "Windows"
      : /Android/i.test(ua) ? "Android"
      : /iPhone|iPad|iOS/i.test(ua) ? "iOS"
      : /Mac OS X/i.test(ua) ? "macOS"
      : /Linux/i.test(ua) ? "Linux" : "";
    var br = /Edg\//i.test(ua) ? "Edge"
      : /OPR\//i.test(ua) ? "Opera"
      : /Firefox\//i.test(ua) ? "Firefox"
      : /Chrome\//i.test(ua) ? "Chrome"
      : /Safari\//i.test(ua) ? "Safari"
      : /curl\//i.test(ua) ? "curl" : "";
    if (br && os) return br + " on " + os;
    return br || os || (ua ? ua.slice(0, 40) : "unknown client");
  }

  function loadSessions() {
    var host = document.getElementById("sessions-body");
    if (!host) return Promise.resolve();
    return G.api("GET", "/api/sessions").then(function (d) {
      var rows = d.sessions || [];
      if (!rows.length) {
        host.innerHTML = '<div class="muted">No active sessions.</div>';
        return;
      }
      host.innerHTML = rows.map(function (s) {
        var age = s.last_seen ? Math.max(0, Math.floor(Date.now() / 1000 - Number(s.last_seen))) : null;
        return '<div class="row"><span>' + G.esc(uaLabel(s.ua)) +
          '<span class="mono muted u-fs12"> · ' +
          G.esc(s.ip || "unknown ip") + "</span></span>" +
          '<span class="muted u-fs12" title="signed in ' +
          G.esc(when(s.created)) + '">' +
          (age === null ? "—" : G.esc(G.ago(age)) + " ago") + "</span></div>";
      }).join("");
    }).catch(function (e) {
      host.innerHTML = G.errorBox(e);
    });
  }

  function signOutEverywhere() {
    return G.modalConfirm({
      title: "Sign out everywhere?",
      message: "Every browser and device signed in with a session cookie will be signed out, including this one. The access token keeps working for scripts.",
      confirmText: "Sign out everywhere",
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      return G.api("POST", "/api/sessions/revoke-all", {})
        .then(function (d) {
          G.toast("signed out " + (d.revoked || 0) + " session(s)", "ok");
          setTimeout(function () { location.href = "index.html"; }, 600);
        }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
    });
  }

  function load() {
    G.api("GET", "/api/settings").then(function (d) {
      S = d.settings;
      var scheduleOpts = Object.keys(S.schedules || {}).map(function (k) {
        return { v: k, l: S.schedules[k] };
      });
      document.getElementById("settings-form").innerHTML =
        f("Parallel workers", G.select("s-workers", S.workers, [1, 2, 4, 8]), "repos synced in parallel") +
        f("Mirror mode", G.select("s-mode", S.mode, [{ v: "safe", l: "safe (never delete)" }, { v: "full", l: "full (prunes)" }])) +
        f("Mesh conflicts", G.select("s-conflict", S.mesh_conflict, [{ v: "newest", l: "newest wins" }, { v: "skip", l: "skip (report)" }])) +
        f("Schedule", G.select("s-schedule", S.schedule, scheduleOpts), "The timer fires at the finest route schedule") +
        f("Include forks", '<label class="check"><input type="checkbox" id="s-forks" name="include_forks"' + (S.include_forks ? " checked" : "") + "> Mirror forked repos</label>", "Include repositories you have forked") +
        f("Pause between repos", G.select("s-pace", S.pace || 0, [0, 1, 2, 5, 10]), "Seconds — spare laptop heat / disk") +
        f("Token warning", G.select("s-warn", S.token_warn_days || 30, [7, 14, 30, 60, 90]), "Days before a credential warns") +
        f("TUI theme", G.select("s-theme", S.theme, (S.themes || [])), "Colours the terminal TUI only") +
        f("Trusted proxies",
          '<input type="text" id="s-proxies" name="trusted_proxies" spellcheck="false" placeholder="e.g. 10.0.0.0/8, 192.168.0.0/16" value="' +
          G.esc((S.trusted_proxies || []).join(", ")) + '">',
          "CIDRs allowed to set X-Forwarded-For / X-Forwarded-Proto. Leave empty unless gitsync sits behind nginx or Caddy — without it, throttling sees the proxy address, not the client.") +
        f("Relax protection", '<label class="check danger-check"><input type="checkbox" id="s-relax" name="relax_protection"' + (S.relax_protection ? " checked" : "") + "> Allow force-push over branch protection</label>", "Advanced: allows overwriting protected branches. Off by default.");
      document.getElementById("sched-status").textContent = S.scheduler || "—";
      document.getElementById("n-on").value = (S.notify && S.notify.on) || "failure";
      document.getElementById("paths").innerHTML =
        "config " + G.esc(S.paths.config) + "<br>mirrors " + G.esc(S.paths.mirror) +
        "<br>log " + G.esc(S.paths.log) + "<br>data " + G.esc(S.paths.data);
      renderSignin(S.auth || {});

      var relaxEl = document.getElementById("s-relax");
      if (relaxEl) {
        relaxEl.addEventListener("change", function () {
          if (this.checked) {
            var self = this;
            G.modalConfirm({
              title: "Relax branch protection?",
              message: "Advanced setting: this allows force-pushing over branch protection rules on target platforms. Turning this on can overwrite protected branches.",
              confirmText: "Enable relax protection",
              danger: true
            }).then(function (ok) {
              if (!ok) self.checked = false;
            });
          }
        });
      }

      var dirtyPill = document.getElementById("settings-dirty");
      var setDirty = function () {
        if (dirtyPill) dirtyPill.classList.remove("hide");
      };
      var formEl = document.getElementById("settings-form");
      if (formEl && !formEl.dataset.wired) {
        formEl.dataset.wired = "1";
        formEl.addEventListener("change", setDirty);
        formEl.addEventListener("input", setDirty);
      }
    }).catch(function (e) {
      document.getElementById("settings-form").innerHTML = G.errorBox(e);
    });
  }

  function renderSignin(auth) {
    var el = document.getElementById("pw-state");
    el.innerHTML = auth.password_set
      ? 'Password sign-in is <span class="ok">on</span> for user ' +
        '<span class="mono">' + G.esc(auth.username) + "</span>."
      : 'Password sign-in is <span class="warn">off</span> — you are using ' +
        "the access token. Set a username &amp; password to enable it.";
    if (auth.password_set && auth.username) {
      document.getElementById("pw-user").value = auth.username;
    }
  }

  function saveSettings() {
    if (!S) { G.toast("settings not loaded — retry", "warn"); return Promise.resolve(); }
    var form = document.getElementById("settings-form");
    if (!document.getElementById("s-workers")) {
      G.toast("settings form not ready — reload the page", "warn");
      return Promise.resolve();
    }
    var body = {
      workers: document.getElementById("s-workers").value,
      mode: document.getElementById("s-mode").value,
      mesh_conflict: document.getElementById("s-conflict").value,
      schedule: document.getElementById("s-schedule").value,
      include_forks: document.getElementById("s-forks").checked,
      pace: document.getElementById("s-pace").value,
      token_warn_days: document.getElementById("s-warn").value,
      theme: document.getElementById("s-theme").value,
      trusted_proxies: document.getElementById("s-proxies").value,
      relax_protection: document.getElementById("s-relax").checked,
      on: document.getElementById("n-on").value
    };
    var dv = document.getElementById("n-discord").value.trim();
    if (dv) body.discord_webhook = dv;
    var tv = document.getElementById("n-tgtoken").value.trim();
    var cv = document.getElementById("n-tgchat").value.trim();
    if (tv) { body.telegram_token = tv; body.telegram_chat_id = cv; }
    return G.api("PUT", "/api/settings", body).then(function () {
      G.toast("settings saved", "ok");
      var dirtyPill = document.getElementById("settings-dirty");
      if (dirtyPill) dirtyPill.classList.add("hide");
      document.getElementById("n-discord").value = "";
      document.getElementById("n-tgtoken").value = "";
      G.refreshStatus();
    }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function installSched() {
    var sel = document.getElementById("s-schedule");
    var sched = sel ? sel.value : undefined;
    return G.api("POST", "/api/scheduler/install", sched ? { schedule: sched } : {})
      .then(function (d) {
        G.toast(d.status, d.ok ? "ok" : "warn");
        document.getElementById("sched-status").textContent = d.status;
        G.refreshStatus();
      }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function clearNotify(what) {
    return G.modalConfirm({
      title: "Remove notification credentials",
      message: "Remove the stored " + what.join(" + ") + " credentials?",
      confirmText: "Remove",
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      return G.api("PUT", "/api/settings", { clear_notify: what })
        .then(function () { G.toast("removed", "ok"); load(); })
        .catch(function (e) { G.toast(G.apiError(e), "bad"); });
    });
  }

  function notifyTest() {
    return G.api("POST", "/api/notify/test").then(function (d) {
      G.toast(d.message, d.ok ? "ok" : "bad");
    }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function savePassword() {
    var u = document.getElementById("pw-user").value.trim();
    var p = document.getElementById("pw-pass").value;
    var c = document.getElementById("pw-confirm").value;
    if (!u) return G.toast("enter a username", "warn");
    if (p.length < 8) return G.toast("password must be at least 8 characters", "warn");
    if (p !== c) return G.toast("passwords do not match", "warn");
    return G.api("POST", "/api/auth/password", { username: u, password: p })
      .then(function (d) {
        G.toast(d.message || "password set", "ok");
        document.getElementById("pw-pass").value = "";
        document.getElementById("pw-confirm").value = "";
        load();
      }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function disablePassword() {
    return G.modalConfirm({
      title: "Disable password sign-in",
      message: "Disable password sign-in? The access token will be the only way in.",
      confirmText: "Disable Password",
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      return G.api("POST", "/api/auth/password", { disable: true })
        .then(function (d) { G.toast(d.message || "disabled", "ok"); load(); })
        .catch(function (e) { G.toast(G.apiError(e), "bad"); });
    });
  }

  function wh(action) {
    return G.api("POST", "/api/webhooks", { action: action }).then(function (d) {
      G.toast(d.note || d.status || action, d.ok ? "ok" : "warn");
      loadWebhooks();
    }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function loadWebhooks() {
    G.api("GET", "/api/webhooks").then(function (d) {
      var eps = (d.endpoints || []).map(function (e) {
        return "<div>http://&lt;this-box&gt;:8791" + G.esc(e.path) +
          " <span class='muted'>(" + G.esc(e.type) + ")</span></div>";
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
        return '<div class="mb"><strong>' + G.esc(g.title) + "</strong> " +
          '<span class="badge muted">min: ' + G.esc(g.minimum) + "</span>" +
          (g.env ? ' <span class="badge muted">env ' + G.esc(g.env) + "</span>" : "") +
          (g.token_url ? '<div class="muted u-fs12">create at ' + G.esc(g.token_url) + "</div>" : "") +
          scopes + "</div>";
      }).join("");
    }).catch(function (e) {
      document.getElementById("scopes-body").innerHTML = G.errorBox(e);
    });
  }

  G.mount("settings", render);
})();
