/* gitsync web UI — core: auth gate, API client, SSE, app shell, helpers.
   Vanilla JS, no build step. Each page registers a render function with
   Gitsync.mount(name, fn); this file renders the shell (icon sidebar + top
   bar) and calls it. */
(function () {
  "use strict";

  // ---- inline SVG icons (no emoji, per the design brief) ----
  var I = {
    logo: '<svg class="mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="12" r="2.2"/><path d="M6 8.2v7.6M8.2 6h4.3a3 3 0 0 1 3 3v0a3 3 0 0 1-3 3H8.2"/></svg>',
    dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>',
    platforms: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
    routes: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M6 16.5V11a4 4 0 0 1 4-4h5M18 7.5v9"/></svg>',
    repos: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
    stars: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.6 5.6 6.1.8-4.5 4.2 1.2 6-5.4-3-5.4 3 1.2-6L3.3 9.4l6.1-.8z"/></svg>',
    log: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    tools: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0 5 5L21 21H3l7.7-7.7a4 4 0 0 1 4-6.9z"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
    sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/></svg>'
  };

  var NAV = [
    { id: "dashboard", href: "index.html", label: "Dashboard", icon: "dashboard" },
    { id: "platforms", href: "platforms.html", label: "Platforms", icon: "platforms" },
    { id: "routes", href: "routes.html", label: "Routes", icon: "routes" },
    { id: "repos", href: "repos.html", label: "Repos", icon: "repos" },
    { id: "stars", href: "stars.html", label: "Stars", icon: "stars" },
    { id: "log", href: "log.html", label: "Log", icon: "log" },
    { id: "history", href: "history.html", label: "History", icon: "history" },
    { id: "tools", href: "tools.html", label: "Tools", icon: "tools" },
    { id: "settings", href: "settings.html", label: "Settings", icon: "settings" }
  ];

  var pages = {};
  var syncListeners = [];
  var _activeJob = null;

  // ---- escaping: all dynamic data goes through here before innerHTML ----
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
               "'": "&#39;" }[c];
    });
  }

  // ---- api client ----
  function api(method, path, body) {
    var opt = { method: method, credentials: "same-origin",
                headers: { "X-Gitsync-Request": "1" } };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    return fetch(path, opt).then(function (res) {
      if (res.status === 401 && path !== "/api/session") {
        showLogin();
        var e = new Error("unauthorized"); e.status = 401; throw e;
      }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var e = new Error((data && data.error) || res.statusText);
          e.status = res.status; e.data = data; throw e;
        }
        return data;
      });
    });
  }

  function checkSession() {
    return api("GET", "/api/session").then(function () { return true; })
      .catch(function () { return false; });
  }

  // ---- global sync control (top bar drives it; pages may listen) ----
  function startSync(dry) {
    return api("POST", "/api/sync", { dry_run: !!dry })
      .then(function (r) {
        _activeJob = r.job_id;
        syncListeners.forEach(function (cb) { try { cb(r.job_id, !!dry); }
                                              catch (e) {} });
        return r;
      })
      .catch(function (e) {
        if (e.status !== 409) toast(e.error || "could not start sync", "bad");
        throw e;
      });
  }
  function onSync(cb) {
    syncListeners.push(cb);
    if (_activeJob) { try { cb(_activeJob, false); } catch (e) {} }
  }
  function clearActiveJob() { _activeJob = null; }

  // ---- toast ----
  function toast(msg, kind) {
    var host = document.getElementById("toast");
    if (!host) {
      host = document.createElement("div"); host.id = "toast";
      document.body.appendChild(host);
    }
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    var ic = kind === "bad" ? I.alert : kind === "warn" ? I.info : I.check;
    el.innerHTML = ic + "<span>" + esc(msg) + "</span>";
    host.appendChild(el);
    setTimeout(function () { el.style.opacity = "0"; el.style.transition =
      "opacity .3s"; setTimeout(function () { el.remove(); }, 300); },
      kind === "bad" ? 6000 : 4000);
  }

  // ---- formatting ----
  function bytes(n) {
    if (n === null || n === undefined) return "—";
    var u = ["B", "K", "M", "G", "T", "P"], i = 0; n = Number(n);
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + u[i];
  }
  function ago(sec) {
    if (sec === null || sec === undefined) return "—";
    sec = Math.max(0, Math.floor(sec));
    if (sec < 60) return sec + "s ago";
    var m = Math.floor(sec / 60); if (m < 60) return m + "m ago";
    var h = Math.floor(m / 60); if (h < 24) return h + "h ago";
    return Math.floor(h / 24) + "d ago";
  }
  function pct(done, total) {
    return total ? Math.min(100, Math.round(100 * done / total)) : 0;
  }
  function tokenStateBadge(state) {
    var map = { set: ["ok", "token"], missing: ["warn", "no token"],
                gh: ["ok", "gh CLI"], glab: ["ok", "glab CLI"],
                local: ["muted", "local"] };
    var m = map[state] || ["muted", state];
    return '<span class="badge ' + m[0] + '">' + esc(m[1]) + "</span>";
  }
  function stateClass(line) {
    if (/FAIL|failed|error|DEAD|CRIT/i.test(line)) return "l-bad";
    if (/WARN|partial|PART|skip/i.test(line)) return "l-warn";
    if (/ OK |===|done|ok\b/i.test(line)) return "l-ok";
    if (/^\[.*?\]\s+===/.test(line)) return "l-head";
    return "";
  }

  // ---- SSE ----
  function sse(path, onEvent) {
    var es = new EventSource(path, { withCredentials: true });
    es.onmessage = function (ev) {
      var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      onEvent(d);
    };
    return es;
  }

  // ---- DOM helpers ----
  function el(html) {
    var t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstChild;
  }
  function bar(title, subtitle, actionsHtml, subId) {
    return '<div class="bar"><div><h1>' + esc(title) + "</h1>" +
      (subtitle ? '<div class="muted sub"' +
        (subId ? ' id="' + subId + '"' : "") + ">" + esc(subtitle) +
        "</div>" : "") +
      '</div><div class="actions">' + (actionsHtml || "") + "</div></div>";
  }
  function loading() {
    return '<div class="skel-card"><div class="skel title"></div>' +
      '<div class="skel skel-row w80"></div>' +
      '<div class="skel skel-row w60"></div>' +
      '<div class="skel skel-row w40"></div></div>';
  }
  function emptyState(title, msg, icon) {
    return '<div class="empty-state">' + (I[icon || "box"] || I.box) +
      '<div class="t">' + esc(title) + "</div>" +
      (msg ? '<div>' + esc(msg) + "</div>" : "") + "</div>";
  }
  function errorBox(err) {
    return '<div class="state error">' +
      esc((err && err.error) || (err && err.message) || err || "error") +
      "</div>";
  }

  // ---- login gate ----
  function showLogin() {
    if (document.body.dataset.login) return;
    document.body.dataset.login = "1";
    var app = document.getElementById("app");
    app.innerHTML =
      '<div class="login-wrap"><div class="login">' +
      '<div class="logo">' + I.logo + "gitsync</div>" +
      '<p class="lead">Sign in to your sync dashboard.</p>' +
      '<div id="login-form">' + loading() + "</div>" +
      "</div></div>";
    fetch("/api/auth").then(function (r) { return r.json(); })
      .then(renderLoginForm)
      .catch(function () { renderLoginForm({ password_set: false }); });
  }

  function renderLoginForm(info) {
    var host = document.getElementById("login-form");
    if (!host) return;
    var html = "";
    if (info.password_set) {
      html = '<div class="field"><label for="lu">Username</label>' +
        '<input type="text" id="lu" autocomplete="username" placeholder="' +
        esc(info.username || "") + '"></div>' +
        '<div class="field"><label for="lp">Password</label>' +
        '<input type="password" id="lp" autocomplete="current-password" ' +
        'placeholder="password"></div>' +
        '<button class="btn primary" id="loginbtn" style="width:100%">' +
        "Sign in</button>" +
        '<p class="muted mt" style="font-size:12.5px;text-align:center">' +
        '<a href="#" id="use-token">use access token instead</a></p>';
    } else {
      html = '<div class="field"><label for="tok">Access token</label>' +
        '<input type="password" id="tok" autocomplete="current-password" ' +
        'placeholder="paste the token from the server"></div>' +
        '<button class="btn primary" id="loginbtn" style="width:100%">' +
        "Sign in</button>" +
        '<p class="muted mt" style="font-size:12.5px;text-align:center">' +
        "Tip: after signing in, set a username &amp; password in Settings.</p>";
    }
    html += '<p class="muted mt" id="loginerr" style="min-height:16px;' +
      'font-size:12.5px;text-align:center"></p>';
    host.innerHTML = html;

    var btn = document.getElementById("loginbtn");
    function submit() {
      var body, lu = document.getElementById("lu");
      if (info.password_set && lu) {
        body = { username: lu.value.trim(),
                 password: document.getElementById("lp").value };
      } else {
        var t = document.getElementById("tok");
        body = { token: t ? t.value.trim() : "" };
      }
      api("POST", "/api/session", body)
        .then(function () { location.reload(); })
        .catch(function (e) {
          document.getElementById("loginerr").textContent =
            (e && e.status === 429) ? "Too many attempts — wait a minute."
            : "Sign-in failed — check your credentials.";
        });
    }
    btn.addEventListener("click", submit);
    host.querySelectorAll("input").forEach(function (inp) {
      inp.addEventListener("keydown", function (e) {
        if (e.key === "Enter") submit();
      });
    });
    var ut = document.getElementById("use-token");
    if (ut) ut.addEventListener("click", function (e) {
      e.preventDefault(); renderLoginForm({ password_set: false });
    });
    var first = host.querySelector("input");
    if (first) first.focus();
  }

  // ---- shell (icon sidebar + top bar) ----
  function renderShell(page) {
    document.body.dataset.login = "";
    var navHtml = NAV.map(function (n) {
      return '<a class="nav' + (n.id === page ? " on" : "") + '" href="' +
        n.href + '"' + (n.id === page ? ' aria-current="page"' : "") + ">" +
        I[n.icon] + "<span>" + esc(n.label) + "</span></a>";
    }).join("");
    var app = document.getElementById("app");
    app.innerHTML =
      '<div class="shell">' +
      '<aside class="side">' +
        '<div class="logo">' + I.logo + "gitsync</div>" + navHtml +
        '<div class="foot">' +
          '<div class="meta" id="side-version">gitsync</div>' +
          '<a class="nav" href="#" id="logout">' + I.logout +
            "<span>Sign out</span></a>" +
        "</div>" +
      "</aside>" +
      '<div class="content">' +
        '<header class="top">' +
          '<div class="brand">' + I.logo + "gitsync</div>" +
          '<span class="status" id="top-status"><span class="pip"></span>' +
            "<span id=\"top-status-text\">connecting…</span></span>" +
          '<div class="spacer"></div>' +
          '<button class="btn ghost" id="top-dry">' + I.eye +
            "<span>Dry run</span></button>" +
          '<button class="btn primary" id="top-sync">' + I.sync +
            "<span>Sync now</span></button>" +
          '<div class="topbar-progress" id="top-progress"></div>' +
        "</header>" +
        '<main class="main" id="main"></main>' +
      "</div></div>";

    document.getElementById("logout").addEventListener("click", function (e) {
      e.preventDefault();
      api("GET", "/api/logout").then(function () {
        location.href = "index.html";
      });
    });
    wireTopbar();
    return document.getElementById("main");
  }

  function setStatus(text, cls) {
    var s = document.getElementById("top-status");
    var t = document.getElementById("top-status-text");
    if (!s || !t) return;
    t.textContent = text;
    s.className = "status" + (cls ? " " + cls : "");
  }

  function wireTopbar() {
    var dry = document.getElementById("top-dry");
    var sync = document.getElementById("top-sync");
    function run(d) {
      startSync(d).catch(function (e) {
        if (e.status === 409) toast(e.error || "a sync is already running", "warn");
      });
    }
    if (dry) dry.addEventListener("click", function () { run(true); });
    if (sync) sync.addEventListener("click", function () { run(false); });

    // compact live progress in the top bar
    var bar = document.getElementById("top-progress");
    onSync(function (jobId) {
      setStatus("syncing…", "live");
      bar.style.width = "4%";
      sse("/api/sync/stream?job_id=" + encodeURIComponent(jobId), function (d) {
        if (d.progress && d.progress.total) {
          setStatus("[" + d.progress.done + "/" + d.progress.total + "] " +
            (d.progress.repo || ""), "live");
          bar.style.width = pct(d.progress.done, d.progress.total) + "%";
        }
        if (d.done) {
          _activeJob = null;
          bar.style.width = "100%";
          setTimeout(function () { bar.style.width = "0%"; }, 600);
          setStatus(d.exit === 0 ? "sync complete" : "sync finished (exit " +
            d.exit + ")", d.exit === 0 ? "ok" : "");
          refreshStatus();
        }
      });
    });

    refreshStatus();
    // if a sync is already running, attach to it
    api("GET", "/api/sync/state").then(function (s) {
      if (s.syncing && s.job_id) {
        _activeJob = s.job_id;
        syncListeners.forEach(function (cb) { try { cb(s.job_id, false); }
                                              catch (e) {} });
      }
    }).catch(function () {});
  }

  function refreshStatus() {
    api("GET", "/api/status").then(function (s) {
      var v = document.getElementById("side-version");
      if (v) v.textContent = "gitsync " + s.version;
      if (s.syncing) { setStatus("syncing…", "live"); return; }
      var next = s.next_run ? " · next " + s.next_run : "";
      setStatus("idle · " + s.schedule + next);
    }).catch(function () { setStatus("offline"); });
  }

  window.Gitsync = {
    I: I, NAV: NAV, esc: esc, api: api, checkSession: checkSession, toast: toast,
    bytes: bytes, ago: ago, pct: pct, tokenStateBadge: tokenStateBadge,
    stateClass: stateClass, sse: sse, showLogin: showLogin, renderShell:
    renderShell, el: el, bar: bar, loading: loading, emptyState: emptyState,
    errorBox: errorBox, startSync: startSync, onSync: onSync, refreshStatus:
    refreshStatus,
    mount: function (name, fn) { pages[name] = fn; }
  };

  // ---- boot ----
  document.addEventListener("DOMContentLoaded", function () {
    var page = document.body.dataset.page || "dashboard";
    checkSession().then(function (authed) {
      if (!authed) { showLogin(); return; }
      var main = renderShell(page);
      var fn = pages[page];
      if (fn) { fn(main); } else { main.innerHTML = errorBox("no such page"); }
    });
  });
})();
