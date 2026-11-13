/* Platforms — the account manager: list, add, edit, remove, test, toggle.
   Token fields are write-only (password inputs, blank = keep). */
(function () {
  var G = window.Gitsync;
  var TYPES = ["github", "gitlab", "forgejo", "gitea", "bitbucket", "generic", "local"];
  var state = { list: [], editing: null, formType: "github", showForm: false };

  function statusBadge(r) {
    if (!r.enabled) return '<span class="badge muted">disabled</span>';
    return r.ok ? '<span class="badge ok">OK</span>'
                : '<span class="badge bad">FAIL</span>';
  }

  function render(main) {
    main.innerHTML =
      G.bar("Platforms", "…",
        '<a class="btn" href="settings.html#scopes">Token scopes</a>' +
        '<button class="btn" id="gh-login-btn">Sign in with GitHub</button>' +
        '<button class="btn primary" id="add-btn">Add platform</button>', "plats-sub") +
      '<div id="form-host"></div>' +
      '<section class="card"><div id="plats-table">' + G.loading() + "</div></section>" +
      '<div id="gh-modal-host"></div>';
    document.getElementById("add-btn").addEventListener("click", function () {
      state.showForm = true; state.editing = null; state.formType = "github";
      renderForm();
      var n = document.getElementById("f-name"); if (n) n.focus();
    });
    document.getElementById("gh-login-btn").addEventListener("click", function () {
      startGitHubLogin("");
    });
    load();
  }

  function load() {
    G.api("GET", "/api/platforms").then(function (d) {
      state.list = d.platforms || [];
      var live = state.list.filter(function (r) { return r.enabled && r.ok; }).length;
      var sub = document.getElementById("plats-sub");
      if (sub) sub.textContent = state.list.length + " sites, " + live + " connected";
      renderTable();
    }).catch(function (e) {
      document.getElementById("plats-table").innerHTML = G.errorBox(e);
    });
  }

  function renderTable() {
    var host = document.getElementById("plats-table");
    if (!state.list.length) {
      host.innerHTML = G.emptyState("No platforms yet",
        "add one to start syncing", "platforms") +
        '<div class="u-center"><button class="btn primary" id="add-btn2">Add one</button></div>';
      document.getElementById("add-btn2").addEventListener("click", function () {
        document.getElementById("add-btn").click();
      });
      return;
    }
    var rows = state.list.map(function (r) {
      var extra = r.path || r.url || "";
      var rateHtml = "";
      if (r.rate_limit) {
        rateHtml = "<div class='muted u-fs12 u-mt2'>" +
          "Rate: " + r.rate_limit.remaining + "/" + r.rate_limit.limit +
          " (" + r.rate_limit.resets_in_mins + "m)</div>";
      }
      return "<tr>" +
        "<td><i class='dot " + (!r.enabled ? "off" : r.ok ? "" : "bad") + "'></i>" +
          "<strong>" + G.esc(r.name) + "</strong></td>" +
        "<td><span class='mono muted'>" + G.esc(r.type) + "</span></td>" +
        "<td>" + statusBadge(r) + "</td>" +
        "<td>" + G.tokenStateBadge(r.token_state) + rateHtml + "</td>" +
        "<td class='mono muted'>" + (r.repo_count === null ? "—" : r.repo_count) + "</td>" +
        "<td class='mono muted nowrap'>" + G.esc(extra) + "</td>" +
        "<td class='actions nowrap'>" +
          (r.type === "github" ? "<button class='btn sm' data-act='gh-login' data-n='" + G.esc(r.name) + "'>Browser Login</button>" : "") +
          "<button class='btn sm' data-act='test' data-n='" + G.esc(r.name) + "' aria-label='Test " + G.esc(r.name) + "'>Test</button>" +
          "<button class='btn sm' data-act='edit' data-n='" + G.esc(r.name) + "' aria-label='Edit " + G.esc(r.name) + "'>Edit</button>" +
          "<button class='btn sm' data-act='toggle' data-n='" + G.esc(r.name) + "' aria-label='" + (r.enabled ? "Disable " : "Enable ") + G.esc(r.name) + "'>" +
            (r.enabled ? "Disable" : "Enable") + "</button>" +
          "<button class='btn sm danger' data-act='del' data-n='" + G.esc(r.name) + "' aria-label='Delete " + G.esc(r.name) + "'>Delete</button>" +
        "</td></tr>";
    }).join("");
    host.innerHTML =
      '<div class="u-scrollx"><table aria-label="Configured platforms"><thead><tr><th>Name</th><th>Type</th>' +
      "<th>Status</th><th>Token</th><th>Repos</th><th>URL / path</th><th></th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>";
    host.querySelectorAll("button[data-act]").forEach(function (b) {
      b.addEventListener("click", function () { act(b.dataset.act, b.dataset.n, b); });
    });
  }

  function act(a, name, btn) {
    var r = state.list.filter(function (x) { return x.name === name; })[0];
    if (a === "gh-login") {
      startGitHubLogin(name);
    } else if (a === "test") {
      G.toast("testing " + name + "…");
      G.api("POST", "/api/platforms/" + encodeURIComponent(name) + "/test")
        .then(function (d) { G.toast(name + ": " + d.info, d.ok ? "ok" : "bad"); load(); })
        .catch(function (e) { G.toast(G.apiError(e), "bad"); });
    } else if (a === "toggle") {
      G.api("POST", "/api/platforms/" + encodeURIComponent(name) + "/toggle")
        .then(load).catch(function (e) { G.toast(G.apiError(e), "bad"); });
    } else if (a === "edit") {
      state.editing = r; state.formType = r.type; state.showForm = true;
      renderForm();
    } else if (a === "del") {
      G.modalConfirm({
        title: "Remove platform",
        message: "Remove platform '" + name + "'? This cannot be undone.",
        confirmText: "Remove Platform",
        danger: true
      }).then(function (ok) {
        if (!ok) return;
        G.busy(btn, function () {
          return G.api("DELETE", "/api/platforms/" + encodeURIComponent(name))
            .catch(function (e) {
              if (e.status === 409) {
                return G.modalConfirm({
                  title: "Force remove",
                  message: e.error + "\n\nRemove anyway (force)?",
                  confirmText: "Force Remove",
                  danger: true
                }).then(function (forceOk) {
                  if (forceOk) return G.api("DELETE", "/api/platforms/" + encodeURIComponent(name) + "?force=1");
                  throw { cancelled: true };
                });
              }
              throw e;
            })
            .then(function () { G.toast(name + " removed", "ok"); load(); })
            .catch(function (e) {
              if (e && e.cancelled) return;
              G.toast(G.apiError(e), "bad");
              load();
            });
        });
      });
    }
  }

  // preserve already-typed values when the Type select re-renders the form
  function currentValues() {
    var v = {};
    ["f-name", "f-url", "f-urltpl", "f-token", "f-owner", "f-affil",
     "f-nsid", "f-user", "f-path", "f-sshport"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) v[id] = el.value;
    });
    return v;
  }

  function renderForm() {
    var host = document.getElementById("form-host");
    var editing = state.editing;
    var t = editing ? editing.type : state.formType;
    var isLocal = t === "local";
    var prev = currentValues();
    var val = function (id, fallback) {
      return (id in prev) ? prev[id] : (fallback || "");
    };
    var f = "";
    if (!editing) {
      f += G.field("Name", '<input type="text" id="f-name" name="platform_name" placeholder="e.g. gitlab, backup…" value="' +
        G.esc(val("f-name")) + '">');
      f += G.field("Type", G.select("f-type", t, TYPES));
    }
    if (isLocal) {
      f += G.field("Folder path", '<input type="text" id="f-path" name="folder_path" placeholder="~/git-mirrors…" value="' +
        G.esc(val("f-path", editing ? editing.path : "")) + '">',
        "holds the bare *.git mirrors");
    } else {
      if (t === "generic") {
        f += G.field("URL template", '<input type="text" id="f-urltpl" name="url_template" placeholder="https://git.example.com/{owner}/{name}.git…" value="' +
          G.esc(val("f-urltpl", editing ? editing.url_template : "")) + '">');
      } else {
        f += G.field("Instance URL", '<input type="url" id="f-url" name="instance_url" inputmode="url" placeholder="https://…" value="' +
          G.esc(val("f-url", editing ? editing.url : "")) + '">');
      }
      f += G.field("Token", '<input type="password" id="f-token" name="token" autocomplete="new-password" spellcheck="false" placeholder="' +
        (editing ? "leave blank to keep current…" : (t === "github" ? "blank = use gh CLI…" : "API token…")) + '">',
        "write-only: never sent back to the browser");
      f += G.field("Owner", '<input type="text" id="f-owner" name="owner" placeholder="blank = auto-detect…" value="' +
        G.esc(val("f-owner", editing ? editing.owner : "")) + '">',
        "username / workspace / namespace");
      if (t === "github") {
        f += G.field("Affiliation", '<input type="text" id="f-affil" name="affiliation" placeholder="owner…" value="' +
          G.esc(val("f-affil", editing ? editing.affiliation : "")) + '">',
          "add organization_member to mirror org repos");
      }
      if (t === "gitlab") {
        f += G.field("Namespace id", '<input type="text" id="f-nsid" name="namespace_id" placeholder="blank = personal namespace…" value="' +
          G.esc(val("f-nsid", editing ? editing.ns_id : "")) + '">');
      }
      if (t === "generic") {
        f += G.field("Git user", '<input type="text" id="f-user" name="git_user" placeholder="git…" value="' +
          G.esc(val("f-user", editing ? editing.user : "")) + '">');
      }
      f += G.field("Protocol", G.select("f-proto", editing && editing.protocol === "ssh" ? "ssh" : "http", ["http", "ssh"]));
      f += G.field("SSH port", '<input type="number" id="f-sshport" name="ssh_port" min="1" max="65535" inputmode="numeric" value="' +
        G.esc(val("f-sshport", editing && editing.ssh_port ? editing.ssh_port : 22)) + '">',
        "only used with ssh");
    }
    host.innerHTML =
      '<section class="card mb"><div class="bar"><span class="route-title">' +
      (editing ? "Edit " + G.esc(editing.name) : "Add Platform") + "</span>" +
      '<div class="actions">' +
      '<button class="btn" id="f-cancel">Cancel</button>' +
      '<button class="btn primary" id="f-save">' + (editing ? "Save Platform" : "Add Platform") + "</button>" +
      "</div></div>" +
      '<div class="form-grid mt">' + f + "</div></section>";
    document.getElementById("f-cancel").addEventListener("click", function () {
      state.showForm = false; state.editing = null; host.innerHTML = "";
      var add = document.getElementById("add-btn"); if (add) add.focus();
    });
    if (!editing) {
      document.getElementById("f-type").addEventListener("change", function (e) {
        state.formType = e.target.value; renderForm();
      });
    }
    document.getElementById("f-save").addEventListener("click", function () {
      G.busy(this, save);
    });
  }

  function val(id) { var el = document.getElementById(id); return el ? el.value : ""; }

  function save() {
    var editing = state.editing;
    var t = editing ? editing.type : state.formType;
    var body = {};
    if (!editing) {
      body.name = val("f-name").trim();
      body.type = t;
      if (!body.name) return G.toast("a platform needs a name", "warn");
    }
    if (t === "local") {
      body.path = val("f-path").trim();
      if (!body.path) return G.toast("local needs a folder path", "warn");
    } else {
      if (t === "generic") body.url_template = val("f-urltpl").trim();
      else body.url = val("f-url").trim();
      var tok = val("f-token");
      if (tok) body.token = tok;            // write-only; blank = keep/add-none
      body.owner = val("f-owner").trim();
      if (t === "github") body.affiliation = val("f-affil").trim();
      if (t === "gitlab") body.ns_id = val("f-nsid").trim();
      if (t === "generic") body.user = val("f-user").trim();
      body.protocol = val("f-proto");
      body.ssh_port = val("f-sshport");
    }
    var req = editing
      ? G.api("PUT", "/api/platforms/" + encodeURIComponent(editing.name), body)
      : G.api("POST", "/api/platforms", body);
    return req.then(function (d) {
      G.toast((editing ? "saved " : "added ") + (editing ? editing.name : body.name) +
        (d.test && !d.test.ok ? " — test failed: " + d.test.info : ""),
        d.test && !d.test.ok ? "warn" : "ok");
      state.showForm = false; state.editing = null;
      document.getElementById("form-host").innerHTML = "";
      load();
    }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function startGitHubLogin(platName) {
    platName = platName || "github";
    var host = document.getElementById("gh-modal-host");
    if (!host) return;
    G.toast("Starting GitHub device authorization…");
    G.api("POST", "/api/auth/github/device/start", {}).then(function (d) {
      var flowId = d.flow_id;
      var userCode = d.user_code;
      var uri = d.verification_uri;
      host.innerHTML =
        '<div class="modal-backdrop" id="gh-backdrop" role="presentation">' +
          '<div class="modal u-max480" role="dialog" aria-modal="true" aria-labelledby="gh-modal-title">' +
            '<div class="modal-head"><h2 class="modal-title" id="gh-modal-title">GitHub Browser Login</h2></div>' +
            '<div class="modal-body">' +
              '<p>Authorize <strong>gitsync</strong> using GitHub Device Flow:</p>' +
              '<div class="u-center u-my16">' +
                '<div class="muted u-fs12 u-mb6">Your one-time code:</div>' +
                '<div class="u-codeblock">' +
                  G.esc(userCode) +
                '</div>' +
              '</div>' +
              '<div class="u-center u-mb16">' +
                '<a class="btn primary" href="' + G.esc(uri) + '" target="_blank" rel="noopener noreferrer">Open GitHub Authorization &rarr;</a>' +
              '</div>' +
              '<div id="gh-poll-status" class="muted u-center u-fs13">' +
                '<span class="pip live"></span> Waiting for approval in your browser…' +
              '</div>' +
            '</div>' +
            '<div class="modal-foot">' +
              '<button class="btn" id="gh-modal-close" type="button">Cancel</button>' +
            '</div>' +
          '</div>' +
        '</div>';

      var closed = false;
      var prevFocus = document.activeElement;
      function dismiss() {
        closed = true;
        document.removeEventListener("keydown", onModalKey);
        host.innerHTML = "";
        if (prevFocus && typeof prevFocus.focus === "function") prevFocus.focus();
      }
      function onModalKey(e) {
        if (e.key === "Escape") { dismiss(); return; }
        if (e.key === "Tab") {
          var focusables = host.querySelectorAll(
            'a[href], button:not([disabled])');
          if (!focusables.length) return;
          var list = Array.prototype.slice.call(focusables);
          var idx = list.indexOf(document.activeElement);
          if (e.shiftKey) {
            if (idx <= 0) { list[list.length - 1].focus(); e.preventDefault(); }
          } else if (idx === -1 || idx >= list.length - 1) {
            list[0].focus(); e.preventDefault();
          }
        }
      }
      document.addEventListener("keydown", onModalKey);
      document.getElementById("gh-modal-close").addEventListener("click", dismiss);
      var backdropEl = document.getElementById("gh-backdrop");
      if (backdropEl) {
        backdropEl.addEventListener("click", function (e) {
          if (e.target === backdropEl) dismiss();
        });
      }
      // move focus into the dialog so keyboard/SR users land on it
      var openLink = host.querySelector("a.btn");
      if (openLink) openLink.focus();

      var pollInterval = (d.interval || 5) * 1000;
      function poll() {
        if (closed) return;
        G.api("POST", "/api/auth/github/device/poll", { flow_id: flowId, platform_name: platName })
          .then(function (res) {
            if (closed) return;
            if (res.status === "ok") {
              host.innerHTML = "";
              G.toast("Connected to GitHub as @" + res.user + "!", "ok");
              load();
            } else if (res.status === "pending") {
              setTimeout(poll, pollInterval);
            } else if (res.status === "slow_down") {
              pollInterval = res.interval ? (res.interval * 1000) : (pollInterval + 5000);
              setTimeout(poll, pollInterval);
            } else {
              document.getElementById("gh-poll-status").innerHTML =
                '<span class="bad">Login failed: ' + G.esc(res.error || "error") + '</span>';
            }
          })
          .catch(function (e) {
            if (closed) return;
            document.getElementById("gh-poll-status").innerHTML =
              '<span class="bad">Error: ' + G.esc(G.apiError(e)) + '</span>';
          });
      }
      setTimeout(poll, pollInterval);
    }).catch(function (e) {
      G.toast(G.apiError(e), "bad");
    });
  }

  G.mount("platforms", render);
})();
