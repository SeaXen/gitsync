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
        '<button class="btn primary" id="add-btn">Add platform</button>', "plats-sub") +
      '<div id="form-host"></div>' +
      '<section class="card"><div id="plats-table">' + G.loading() + "</div></section>";
    document.getElementById("add-btn").addEventListener("click", function () {
      state.showForm = true; state.editing = null; state.formType = "github";
      renderForm();
      var n = document.getElementById("f-name"); if (n) n.focus();
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
        '<div style="text-align:center"><button class="btn primary" id="add-btn2">Add one</button></div>';
      document.getElementById("add-btn2").addEventListener("click", function () {
        document.getElementById("add-btn").click();
      });
      return;
    }
    var rows = state.list.map(function (r) {
      var extra = r.path || r.url || "";
      return "<tr>" +
        "<td><i class='dot " + (!r.enabled ? "off" : r.ok ? "" : "bad") + "'></i>" +
          "<strong>" + G.esc(r.name) + "</strong></td>" +
        "<td><span class='mono muted'>" + G.esc(r.type) + "</span></td>" +
        "<td>" + statusBadge(r) + "</td>" +
        "<td>" + G.tokenStateBadge(r.token_state) + "</td>" +
        "<td class='mono muted'>" + (r.repo_count === null ? "—" : r.repo_count) + "</td>" +
        "<td class='mono muted nowrap'>" + G.esc(extra) + "</td>" +
        "<td class='actions nowrap'>" +
          "<button class='btn sm' data-act='test' data-n='" + G.esc(r.name) + "'>Test</button>" +
          "<button class='btn sm' data-act='edit' data-n='" + G.esc(r.name) + "'>Edit</button>" +
          "<button class='btn sm' data-act='toggle' data-n='" + G.esc(r.name) + "'>" +
            (r.enabled ? "Disable" : "Enable") + "</button>" +
          "<button class='btn sm danger' data-act='del' data-n='" + G.esc(r.name) + "'>Del</button>" +
        "</td></tr>";
    }).join("");
    host.innerHTML =
      '<div style="overflow-x:auto"><table><thead><tr><th>Name</th><th>Type</th>' +
      "<th>Status</th><th>Token</th><th>Repos</th><th>URL / path</th><th></th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>";
    host.querySelectorAll("button[data-act]").forEach(function (b) {
      b.addEventListener("click", function () { act(b.dataset.act, b.dataset.n, b); });
    });
  }

  function act(a, name, btn) {
    var r = state.list.filter(function (x) { return x.name === name; })[0];
    if (a === "test") {
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
      if (!confirm("Remove platform '" + name + "'? This cannot be undone.")) return;
      G.busy(btn, function () {
        return G.api("DELETE", "/api/platforms/" + encodeURIComponent(name))
          .catch(function (e) {
            if (e.status === 409 && confirm(e.error + "\n\nRemove anyway (force)?")) {
              return G.api("DELETE", "/api/platforms/" +
                encodeURIComponent(name) + "?force=1");
            }
            throw e;
          })
          .then(function () { G.toast(name + " removed", "ok"); load(); })
          .catch(function (e) { if (e.error) G.toast(G.apiError(e), "bad"); });
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
      f += G.field("Name", '<input type="text" id="f-name" placeholder="e.g. gitlab, backup" value="' +
        G.esc(val("f-name")) + '">');
      f += G.field("Type", G.select("f-type", t, TYPES));
    }
    if (isLocal) {
      f += G.field("Folder path", '<input type="text" id="f-path" placeholder="~/git-mirrors" value="' +
        G.esc(val("f-path", editing ? editing.path : "")) + '">',
        "holds the bare *.git mirrors");
    } else {
      if (t === "generic") {
        f += G.field("URL template", '<input type="text" id="f-urltpl" placeholder="https://git.example.com/{owner}/{name}.git" value="' +
          G.esc(val("f-urltpl", editing ? editing.url_template : "")) + '">');
      } else {
        f += G.field("Instance URL", '<input type="text" id="f-url" value="' +
          G.esc(val("f-url", editing ? editing.url : "")) + '">');
      }
      f += G.field("Token", '<input type="password" id="f-token" autocomplete="new-password" placeholder="' +
        (editing ? "leave blank to keep current" : (t === "github" ? "blank = use gh CLI" : "API token")) + '">',
        "write-only: never sent back to the browser");
      f += G.field("Owner", '<input type="text" id="f-owner" placeholder="blank = auto-detect" value="' +
        G.esc(val("f-owner", editing ? editing.owner : "")) + '">',
        "username / workspace / namespace");
      if (t === "github") {
        f += G.field("Affiliation", '<input type="text" id="f-affil" placeholder="owner" value="' +
          G.esc(val("f-affil", editing ? editing.affiliation : "")) + '">',
          "add organization_member to mirror org repos");
      }
      if (t === "gitlab") {
        f += G.field("Namespace id", '<input type="text" id="f-nsid" placeholder="blank = personal namespace" value="' +
          G.esc(val("f-nsid", editing ? editing.ns_id : "")) + '">');
      }
      if (t === "generic") {
        f += G.field("Git user", '<input type="text" id="f-user" placeholder="git" value="' +
          G.esc(val("f-user", editing ? editing.user : "")) + '">');
      }
      f += G.field("Protocol", G.select("f-proto", editing && editing.protocol === "ssh" ? "ssh" : "http", ["http", "ssh"]));
      f += G.field("SSH port", '<input type="number" id="f-sshport" value="' +
        G.esc(val("f-sshport", editing && editing.ssh_port ? editing.ssh_port : 22)) + '">',
        "only used with ssh");
    }
    host.innerHTML =
      '<section class="card mb"><div class="bar"><span class="route-title">' +
      (editing ? "Edit " + G.esc(editing.name) : "Add platform") + "</span>" +
      '<div class="actions">' +
      '<button class="btn" id="f-cancel">Cancel</button>' +
      '<button class="btn primary" id="f-save">' + (editing ? "Save" : "Add") + "</button>" +
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

  G.mount("platforms", render);
})();
