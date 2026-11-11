/* gitsync web UI — core: auth gate, API client, SSE, app shell, helpers.
   Vanilla JS, no build step. Each page registers a render function with
   Gitsync.mount(name, fn); this file renders the shell (icon sidebar + top
   bar) and calls it. */
(function () {
  "use strict";

  // ---- inline SVG icons (no emoji, per the design brief) ----
  var I = {
    logo: '<svg class="mark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="12" r="2.2"/><path d="M6 8.2v7.6M8.2 6h4.3a3 3 0 0 1 3 3v0a3 3 0 0 1-3 3H8.2"/></svg>',
    dashboard: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>',
    platforms: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
    routes: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M6 16.5V11a4 4 0 0 1 4-4h5M18 7.5v9"/></svg>',
    repos: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
    stars: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 3l2.6 5.6 6.1.8-4.5 4.2 1.2 6-5.4-3-5.4 3 1.2-6L3.3 9.4l6.1-.8z"/></svg>',
    log: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    history: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    tools: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M14.7 6.3a4 4 0 0 0 5 5L21 21H3l7.7-7.7a4 4 0 0 1 4-6.9z"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>',
    sync: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    logout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 6L9 17l-5-5"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/></svg>'
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
  function apiError(e) {
    return (e && (e.error || e.message)) || "request failed";
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
  function startSync(opts) {
    opts = opts || {};
    var dry = !!opts.dry_run;
    var body = { dry_run: dry };
    if (opts.route) body.route = opts.route;
    if (opts.to) body.to = opts.to;
    if (opts.repos) body.repos = opts.repos;
    return api("POST", "/api/sync", body)
      .then(function (r) {
        _activeJob = r.job_id;
        syncListeners.forEach(function (cb) { try { cb(r.job_id, opts); }
                                              catch (e) {} });
        return r;
      })
      .catch(function (e) {
        if (e.status !== 409) toast(apiError(e), "bad");
        throw e;
      });
  }
  function onSync(cb) {
    syncListeners.push(cb);
    if (_activeJob) { try { cb(_activeJob, {}); } catch (e) {} }
  }

  // ---- toast (aria-live; never empty) ----
  function toast(msg, kind) {
    if (msg === null || msg === undefined || msg === "") {
      msg = kind === "bad" ? "Something went wrong" : "Done";
    }
    var host = document.getElementById("toast");
    if (!host) {
      host = document.createElement("div");
      host.id = "toast";
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      host.setAttribute("aria-atomic", "true");
      document.body.appendChild(host);
    }
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    var ic = kind === "bad" ? I.alert : kind === "warn" ? I.info : I.check;
    el.innerHTML = ic + "<span>" + esc(msg) + "</span>";
    host.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0"; el.style.transition = "opacity .3s";
      setTimeout(function () { el.remove(); }, 300);
    }, kind === "bad" ? 6000 : 4000);
  }

  // ---- disable a button while its action runs (no double-submit) ----
  function busy(btn, fn) {
    if (btn) { btn.disabled = true; btn.setAttribute("aria-busy", "true"); }
    var done = function () {
      if (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); }
    };
    return Promise.resolve(fn()).then(function (r) { done(); return r; },
      function (e) { done(); throw e; });
  }

  // ---- accessible confirmation modal (replaces native window.confirm) ----
  function modalConfirm(opts) {
    opts = (typeof opts === "string") ? { message: opts } : (opts || {});
    return new Promise(function (resolve) {
      var title = opts.title || "Confirm";
      var message = opts.message || "Are you sure?";
      var confirmLabel = opts.confirmText || "Confirm";
      var cancelLabel = opts.cancelText || "Cancel";
      var danger = (opts.danger !== undefined) ? !!opts.danger
        : /remove|delete|disable|rewrite|prune/i.test(message + " " + title);

      var prevFocus = document.activeElement;
      var backdrop = document.createElement("div");
      backdrop.className = "modal-backdrop";
      backdrop.setAttribute("role", "presentation");

      var modal = document.createElement("div");
      modal.className = "modal";
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-modal", "true");
      modal.setAttribute("aria-labelledby", "gs-modal-title");
      modal.setAttribute("aria-describedby", "gs-modal-desc");

      modal.innerHTML =
        '<div class="modal-head">' +
          '<h2 class="modal-title" id="gs-modal-title">' + esc(title) + "</h2>" +
        "</div>" +
        '<div class="modal-body" id="gs-modal-desc">' + esc(message) + "</div>" +
        '<div class="modal-foot">' +
          '<button class="btn" id="gs-modal-cancel" type="button">' + esc(cancelLabel) + "</button>" +
          '<button class="btn ' + (danger ? "danger" : "primary") + '" id="gs-modal-ok" type="button">' + esc(confirmLabel) + "</button>" +
        "</div>";

      backdrop.appendChild(modal);
      document.body.appendChild(backdrop);

      var okBtn = modal.querySelector("#gs-modal-ok");
      var cancelBtn = modal.querySelector("#gs-modal-cancel");

      function cleanup(result) {
        document.removeEventListener("keydown", onKey);
        backdrop.remove();
        if (prevFocus && typeof prevFocus.focus === "function") prevFocus.focus();
        resolve(result);
      }

      function onKey(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          cleanup(false);
        } else if (e.key === "Tab") {
          var focusable = [cancelBtn, okBtn];
          var idx = focusable.indexOf(document.activeElement);
          if (e.shiftKey) {
            if (idx <= 0) { focusable[focusable.length - 1].focus(); e.preventDefault(); }
          } else {
            if (idx === -1 || idx >= focusable.length - 1) { focusable[0].focus(); e.preventDefault(); }
          }
        }
      }

      cancelBtn.addEventListener("click", function () { cleanup(false); });
      okBtn.addEventListener("click", function () { cleanup(true); });
      backdrop.addEventListener("click", function (e) {
        if (e.target === backdrop) cleanup(false);
      });
      document.addEventListener("keydown", onKey);
      (danger ? cancelBtn : okBtn).focus();
    });
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
    if (typeof Intl !== "undefined" && Intl.RelativeTimeFormat) {
      try {
        var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
        if (sec < 60) return rtf.format(-sec, "second");
        var m = Math.floor(sec / 60); if (m < 60) return rtf.format(-m, "minute");
        var h = Math.floor(m / 60); if (h < 24) return rtf.format(-h, "hour");
        return rtf.format(-Math.floor(h / 24), "day");
      } catch (e) {}
    }
    if (sec < 60) return sec + "s ago";
    var min = Math.floor(sec / 60); if (min < 60) return min + "m ago";
    var hr = Math.floor(min / 60); if (hr < 24) return hr + "h ago";
    return Math.floor(hr / 24) + "d ago";
  }
  function formatDate(isoStr) {
    if (!isoStr) return "—";
    try {
      var d = new Date(isoStr);
      if (!isNaN(d.getTime()) && typeof Intl !== "undefined" && Intl.DateTimeFormat) {
        return new Intl.DateTimeFormat(undefined, {
          dateStyle: "short",
          timeStyle: "medium"
        }).format(d);
      }
    } catch (e) {}
    return String(isoStr);
  }
  function pct(done, total) {
    return total ? Math.min(100, Math.round(100 * done / total)) : 0;
  }
  function spark(vals) {                       // one shared sparkline
    if (!vals || !vals.length) return "";
    var blocks = " ▁▂▃▄▅▆▇";
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = (hi - lo) || 1;
    return vals.map(function (v) {
      return blocks[Math.min(7, Math.floor((v - lo) / span * 7) || 0)];
    }).join("");
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
    var fails = 0;
    es.onmessage = function (ev) {
      var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      fails = 0;
      onEvent(d, es);
      // a finished run is terminal: close, or EventSource reconnects and
      // re-streams the same done job forever
      if (d && d.done) { try { es.close(); } catch (e) {} }
    };
    es.onerror = function () {
      // the job is gone (server restarted) or the socket dropped: stop
      // hammering the server and tell the caller once
      if (++fails >= 3 || es.readyState === EventSource.CLOSED) {
        try { es.close(); } catch (e) {}
        if (onEvent._onlost) onEvent._onlost();
      }
    };
    return es;
  }
  // watch one streamed job; hooks: onLine(line,cls) onProgress(prog)
  // onDone(evt) onLost()
  function watchJob(path, hooks) {
    hooks = hooks || {};
    var wrapped = function (d, es) {
      if (d.line && hooks.onLine) hooks.onLine(d.line, stateClass(d.line));
      if (d.progress && hooks.onProgress) hooks.onProgress(d.progress);
      if (d.done && hooks.onDone) hooks.onDone(d);
    };
    wrapped._onlost = hooks.onLost;
    return sse(path, wrapped);
  }

  // ---- DOM helpers ----
  function el(html) {
    var t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstChild;
  }
  function bar(title, subtitle, actionsHtml, subId, rawSub) {
    var sub = "";
    if (subtitle) {
      sub = '<div class="muted sub"' + (subId ? ' id="' + subId + '"' : "") +
        ">" + (rawSub ? subtitle : esc(subtitle)) + "</div>";
    }
    return '<div class="bar"><div><h1>' + esc(title) + "</h1>" + sub +
      '</div><div class="actions">' + (actionsHtml || "") + "</div></div>";
  }
  // field(): auto-emits <label for> from the first id="..." in `inner`, so
  // every existing call site gets an accessible name for free
  function field(label, inner, hint) {
    var m = /id="([^"]+)"/.exec(inner);
    var forAttr = m ? ' for="' + m[1] + '"' : "";
    return '<div class="field"><label' + forAttr + ">" + esc(label) +
      "</label>" + inner +
      (hint ? '<div class="hint">' + esc(hint) + "</div>" : "") + "</div>";
  }
  // select(): one escaped builder for every dropdown
  function select(id, value, options) {
    var opts = options.map(function (o) {
      var v = (o && typeof o === "object") ? o.v : o;
      var l = (o && typeof o === "object") ? o.l : o;
      return '<option value="' + esc(v) + '"' +
        (String(value) === String(v) ? " selected" : "") + ">" + esc(l) +
        "</option>";
    }).join("");
    return '<select id="' + esc(id) + '" name="' + esc(id) + '">' + opts + "</select>";
  }
  function loading(msg) {
    return '<div class="skel-card" role="status" aria-busy="true">' +
      '<div class="skel title"></div><div class="skel skel-row w80"></div>' +
      '<div class="skel skel-row w60"></div><div class="skel skel-row w40"></div>' +
      (msg ? '<div class="muted" style="margin-top:8px;font-size:12.5px">' +
        esc(msg) + "</div>" : "") + "</div>";
  }
  function emptyState(title, msg, icon) {
    return '<div class="empty-state">' + (I[icon || "box"] || I.box) +
      '<div class="t">' + esc(title) + "</div>" +
      (msg ? "<div>" + esc(msg) + "</div>" : "") + "</div>";
  }
  function errorBox(err) {
    return '<div class="state error">' + esc(apiError(err)) + "</div>";
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
      '<form id="login-form" novalidate>' + loading() + "</form>" +
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
      html = field("Username", '<input type="text" id="lu" name="username" ' +
        'autocomplete="username" spellcheck="false" placeholder="' +
        esc((info.username || "admin") + "…") + '">') +
        field("Password", '<input type="password" id="lp" name="password" ' +
        'autocomplete="current-password" spellcheck="false" placeholder="password…">') +
        '<button class="btn primary" id="loginbtn" type="submit" ' +
        'style="width:100%">Sign in</button>' +
        '<p class="muted mt" style="font-size:12.5px;text-align:center">' +
        '<button class="btn ghost sm" id="use-token" type="button">Use access token instead</button></p>';
    } else {
      html = field("Access token", '<input type="password" id="tok" name="token" ' +
        'autocomplete="current-password" spellcheck="false" placeholder="paste the token…">') +
        '<button class="btn primary" id="loginbtn" type="submit" ' +
        'style="width:100%">Sign in</button>' +
        '<p class="muted mt" style="font-size:12.5px;text-align:center">' +
        "Tip: after signing in, set a username &amp; password in Settings.</p>";
    }
    html += '<p class="muted mt" id="loginerr" role="alert" ' +
      'style="min-height:16px;font-size:12.5px;text-align:center"></p>';
    host.innerHTML = html;

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
    host.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
    var ut = document.getElementById("use-token");
    if (ut) ut.addEventListener("click", function (e) {
      e.preventDefault(); renderLoginForm({ password_set: false });
    });
    var isTouch = ("ontouchstart" in window) || (navigator.maxTouchPoints > 0) ||
      (window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
    var first = host.querySelector("input");
    if (!isTouch && first) first.focus();
  }

  // ---- shell (icon sidebar + top bar) ----
  function renderShell(page) {
    document.body.dataset.login = "";
    var navHtml = NAV.map(function (n) {
      return '<a class="nav' + (n.id === page ? " on" : "") + '" href="' +
        n.href + '"' + (n.id === page ? ' aria-current="page"' : "") + ">" +
        I[n.icon] + "<span>" + esc(n.label) + "</span></a>";
    }).join("") +
    '<button class="nav mobile-logout" id="side-logout" type="button" title="Sign out" aria-label="Sign out" style="background:transparent;border:0;cursor:pointer;white-space:nowrap;font:inherit">' +
      I.logout + "<span>Sign out</span></button>";
    var app = document.getElementById("app");
    app.innerHTML =
      '<a class="skip" href="#main">Skip to content</a>' +
      '<div class="shell">' +
      '<nav class="side" aria-label="Sections">' +
        '<div class="logo">' + I.logo + "<span>gitsync</span></div>" + navHtml +
        '<div class="foot">' +
          '<div class="meta" id="side-version">gitsync</div>' +
          '<button class="nav" id="logout" type="button" style="background:transparent;border:0;cursor:pointer;width:100%;text-align:left;font:inherit">' +
            I.logout + "<span>Sign out</span></button>" +
        "</div>" +
      "</nav>" +
      '<div class="content">' +
        '<header class="top">' +
          '<div class="brand">' + I.logo + "<span>gitsync</span></div>" +
          '<span class="status" id="top-status"><span class="pip"></span>' +
            '<span id="top-status-text" role="status">connecting…</span></span>' +
          '<div class="spacer"></div>' +
          '<button class="btn ghost" id="top-dry" title="Dry run" aria-label="Dry run">' + I.eye +
            "<span>Dry run</span></button>" +
          '<button class="btn primary" id="top-sync" title="Sync now" aria-label="Sync now">' + I.sync +
            "<span>Sync now</span></button>" +
          '<button class="btn icon" id="top-logout" title="Sign out" ' +
            'aria-label="Sign out">' + I.logout + "</button>" +
          '<div class="topbar-progress" id="top-progress" role="progressbar" ' +
            'aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"></div>' +
        "</header>" +
        '<main class="main" id="main" tabindex="-1"></main>' +
      "</div></div>";

    document.getElementById("logout").addEventListener("click", doLogout);
    var sideLogout = document.getElementById("side-logout");
    if (sideLogout) sideLogout.addEventListener("click", doLogout);
    document.getElementById("top-logout").addEventListener("click", doLogout);
    wireTopbar();
    return document.getElementById("main");
  }

  function doLogout(e) {
    if (e) e.preventDefault();
    api("GET", "/api/logout").then(function () { location.href = "index.html"; });
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
    var bar = document.getElementById("top-progress");
    function run(d, btn) {
      busy(btn, function () { return startSync({ dry_run: d }); })
        .catch(function (e) {
          if (e.status === 409) toast(e.error || "a sync is already running", "warn");
        });
    }
    if (dry) dry.addEventListener("click", function () { run(true, dry); });
    if (sync) sync.addEventListener("click", function () { run(false, sync); });

    onSync(function (jobId) {
      setStatus("syncing…", "live");
      if (bar) { bar.style.width = "4%"; bar.setAttribute("aria-valuenow", "4"); }
      watchJob("/api/sync/stream?job_id=" + encodeURIComponent(jobId), {
        onProgress: function (p) {
          if (p.total) {
            setStatus("[" + p.done + "/" + p.total + "] " + (p.repo || ""), "live");
            var pc = pct(p.done, p.total);
            if (bar) { bar.style.width = pc + "%"; bar.setAttribute("aria-valuenow", String(pc)); }
          }
        },
        onDone: function (d) {
          if (bar) bar.style.width = "100%";
          setTimeout(function () { if (bar) bar.style.width = "0%"; }, 800);
          setStatus(d.exit === 0 ? "sync complete" :
                    "sync finished (exit " + d.exit + ")", d.exit === 0 ? "ok" : "");
          toast(d.exit === 0 ? "Sync finished" :
                "Sync finished (exit " + d.exit + ")", d.exit === 0 ? "ok" : "bad");
          _activeJob = null;
          refreshStatus();
        }
      });
    });

    refreshStatus();
    api("GET", "/api/sync/state").then(function (s) {
      if (s.syncing && s.job_id) {
        _activeJob = s.job_id;
        syncListeners.forEach(function (cb) { try { cb(s.job_id, {}); } catch (e) {} });
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
    I: I, esc: esc, apiError: apiError, api: api, checkSession: checkSession,
    toast: toast, busy: busy, modalConfirm: modalConfirm, bytes: bytes,
    ago: ago, formatDate: formatDate, pct: pct, spark: spark,
    tokenStateBadge: tokenStateBadge, stateClass: stateClass, sse: sse,
    watchJob: watchJob, showLogin: showLogin, renderShell: renderShell,
    el: el, bar: bar, field: field, select: select, loading: loading,
    emptyState: emptyState, errorBox: errorBox, startSync: startSync,
    onSync: onSync, refreshStatus: refreshStatus,
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
