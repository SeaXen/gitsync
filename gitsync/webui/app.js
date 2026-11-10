/* gitsync web UI — core: auth gate, API client, SSE, shell, helpers.
   Vanilla JS, no build step. Every page file registers a render function
   with Gitsync.mount(name, fn); this file renders the shell and calls it. */
(function () {
  "use strict";

  var NAV = [
    { id: "dashboard", href: "index.html", label: "Dashboard" },
    { id: "platforms", href: "platforms.html", label: "Platforms" },
    { id: "routes", href: "routes.html", label: "Routes" },
    { id: "repos", href: "repos.html", label: "Repos" },
    { id: "stars", href: "stars.html", label: "Stars" },
    { id: "log", href: "log.html", label: "Log" },
    { id: "history", href: "history.html", label: "History" },
    { id: "tools", href: "tools.html", label: "Tools" },
    { id: "settings", href: "settings.html", label: "Settings" }
  ];

  var pages = {};

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

  // ---- toast ----
  function toast(msg, kind) {
    var host = document.getElementById("toast");
    if (!host) {
      host = document.createElement("div"); host.id = "toast";
      document.body.appendChild(host);
    }
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === "bad" ? 6000 : 4000);
  }

  // ---- formatting ----
  function bytes(n) {
    if (n === null || n === undefined) return "—";
    var u = ["B", "K", "M", "G", "T", "P"], i = 0;
    n = Number(n);
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
    if (!total) return 0;
    return Math.min(100, Math.round(100 * done / total));
  }
  function tokenStateBadge(state) {
    var map = {
      set: ["ok", "token"], missing: ["warn", "no token"], gh: ["ok", "gh CLI"],
      glab: ["ok", "glab CLI"], local: ["muted", "local"]
    };
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

  // ---- shell + login ----
  function showLogin() {
    if (document.body.dataset.login) return;
    document.body.dataset.login = "1";
    var app = document.getElementById("app");
    app.innerHTML =
      '<div class="login-wrap"><div class="login">' +
      '<div class="logo">gitsync</div>' +
      '<p class="muted sub">Sign in to the dashboard.</p>' +
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
        '<p class="muted mt" style="font-size:12px">' +
        '<a href="#" id="use-token">use access token instead</a></p>';
    } else {
      html = '<div class="field"><label for="tok">Access token</label>' +
        '<input type="password" id="tok" autocomplete="current-password" ' +
        'placeholder="paste token"></div>' +
        '<button class="btn primary" id="loginbtn" style="width:100%">' +
        "Sign in</button>" +
        '<p class="muted mt" style="font-size:12px">Tip: after signing in, ' +
        "set a username &amp; password in Settings.</p>";
    }
    html += '<p class="muted mt" id="loginerr" style="min-height:18px"></p>';
    host.innerHTML = html;

    var btn = document.getElementById("loginbtn");
    function submit() {
      var body;
      var lu = document.getElementById("lu");
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
      e.preventDefault();
      renderLoginForm({ password_set: false });
    });
    var first = host.querySelector("input");
    if (first) first.focus();
  }

  function renderShell(page) {
    document.body.dataset.login = "";
    var navHtml = NAV.map(function (n) {
      var on = n.id === page ? " on" : "";
      var cur = n.id === page ? ' aria-current="page"' : "";
      return '<a class="nav' + on + '" href="' + n.href + '"' + cur + ">" +
        esc(n.label) + "</a>";
    }).join("");
    var app = document.getElementById("app");
    app.innerHTML =
      '<aside class="side">' +
      '<div class="logo">gitsync</div>' + navHtml +
      '<div style="margin-top:auto"><a class="nav" href="#" id="logout">' +
      'Sign out</a></div></aside>' +
      '<main class="main" id="main"></main>';
    document.getElementById("logout").addEventListener("click", function (e) {
      e.preventDefault();
      api("GET", "/api/logout").then(function () { location.href = "index.html"; });
    });
    return document.getElementById("main");
  }

  // ---- small DOM helpers used by pages ----
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
  function loading(msg) {
    return '<div class="state"><span class="spinner"></span>' +
      esc(msg || "Loading…") + "</div>";
  }
  function errorBox(err) {
    return '<div class="state error">' +
      esc((err && err.error) || err && err.message || err || "error") +
      "</div>";
  }

  window.Gitsync = {
    NAV: NAV, esc: esc, api: api, checkSession: checkSession, toast: toast,
    bytes: bytes, ago: ago, pct: pct, tokenStateBadge: tokenStateBadge,
    stateClass: stateClass, sse: sse, showLogin: showLogin,
    renderShell: renderShell, el: el, bar: bar, loading: loading,
    errorBox: errorBox,
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
