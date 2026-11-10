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
    var live = state.list.filter(function (r) { return r.enabled && r.ok; }).length;
    main.innerHTML =
      G.bar("Platforms", state.list.length + " sites, " + live + " connected · " +
        '<a href="settings.html#scopes">token scopes</a>',
        '<button class="btn primary" id="add-btn">Add platform</button>') +
      '<div id="form-host"></div>' +
      '<section class="card"><div id="plats-table">' + G.loading("reading sites…") + "</div></section>";
    document.getElementById("add-btn").addEventListener("click", function () {
      state.showForm = true; state.editing = null; state.formType = "github";
      renderForm(); document.getElementById("f-name").focus();
    });
    load();
  }

  function load() {
    G.api("GET", "/api/platforms").then(function (d) {
      state.list = d.platforms || [];
      renderTable();
    }).catch(function (e) {
      document.getElementById("plats-table").innerHTML = G.errorBox(e);
    });
  }

  function renderTable() {
    var host = document.getElementById("plats-table");
    if (!state.list.length) {
      host.innerHTML = '<div class="state">No platforms yet. ' +
        '<button class="btn sm primary" id="add-btn2">Add one</button></div>';
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
      "<table><thead><tr><th>Name</th><th>Type</th><th>Status</th>" +
      "<th>Token</th><th>Repos</th><th>URL / path</th><th></th></tr></thead>" +
      "<tbody>" + rows + "</tbody></table>";
    host.querySelectorAll("button[data-act]").forEach(function (b) {
      b.addEventListener("click", function () { act(b.dataset.act, b.dataset.n); });
    });
  }

  function act(a, name) {
    var r = state.list.filter(function (x) { return x.name === name; })[0];
    if (a === "test") {
      G.toast("testing " + name + "…");
      G.api("POST", "/api/platforms/" + encodeURIComponent(name) + "/test")
        .then(function (d) { G.toast(name + ": " + d.info, d.ok ? "ok" : "bad"); load(); })
        .catch(function (e) { G.toast(e.error, "bad"); });
    } else if (a === "toggle") {
      G.api("POST", "/api/platforms/" + encodeURIComponent(name) + "/toggle")
        .then(function () { load(); })
        .catch(function (e) { G.toast(e.error, "bad"); });
    } else if (a === "edit") {
      state.editing = r; state.formType = r.type; state.showForm = true;
      renderForm();
    } else if (a === "del") {
      if (!confirm("Remove platform '" + name + "'? This cannot be undone.")) return;
      G.api("DELETE", "/api/platforms/" + encodeURIComponent(name))
        .catch(function (e) {
          if (e.status === 409 && confirm(e.error + "\n\nRemove anyway (force)?")) {
            return G.api("DELETE", "/api/platforms/" + encodeURIComponent(name) + "?force=1");
          }
          throw e;
        })
        .then(function () { G.toast(name + " removed", "ok"); load(); })
        .catch(function (e) { if (e.error) G.toast(e.error, "bad"); });
    }
  }

  function field(label, inner, hint) {
    return '<div class="field"><label>' + G.esc(label) + "</label>" + inner +
      (hint ? '<div class="hint">' + G.esc(hint) + "</div>" : "") + "</div>";
  }

  function renderForm() {
    var host = document.getElementById("form-host");
    var editing = state.editing;
    var t = editing ? editing.type : state.formType;
    var isLocal = t === "local";
    var f = "";
    if (!editing) {
      f += field("Name", '<input type="text" id="f-name" placeholder="e.g. gitlab, backup">');
      f += field("Type", '<select id="f-type">' + TYPES.map(function (x) {
        return '<option value="' + x + '"' + (x === t ? " selected" : "") + ">" + x + "</option>";
      }).join("") + "</select>");
    }
    if (isLocal) {
      f += field("Folder path", '<input type="text" id="f-path" placeholder="~/git-mirrors" value="' +
        G.esc(editing ? editing.path : "") + '">', "holds the bare *.git mirrors");
    } else {
      if (t === "generic") {
        f += field("URL template", '<input type="text" id="f-urltpl" placeholder="https://git.example.com/{owner}/{name}.git" value="' +
          G.esc(editing ? editing.url_template : "") + '">');
      } else {
        f += field("Instance URL", '<input type="text" id="f-url" value="' +
          G.esc(editing ? editing.url : "") + '">');
      }
      f += field("Token", '<input type="password" id="f-token" autocomplete="new-password" placeholder="' +
        (editing ? "leave blank to keep current" : (t === "github" ? "blank = use gh CLI" : "API token")) + '">',
        "write-only: never sent back to the browser");
      f += field("Owner", '<input type="text" id="f-owner" placeholder="blank = auto-detect" value="' +
        G.esc(editing ? editing.owner : "") + '">', "username / workspace / namespace");
      if (t === "github") {
        f += field("Affiliation", '<input type="text" id="f-affil" placeholder="owner" value="' +
          G.esc(editing ? editing.affiliation : "") + '">', "add organization_member to mirror org repos");
      }
      if (t === "gitlab") {
        f += field("Namespace id", '<input type="text" id="f-nsid" placeholder="blank = personal namespace" value="' +
          G.esc(editing ? editing.ns_id : "") + '">');
      }
      if (t === "generic") {
        f += field("Git user", '<input type="text" id="f-user" placeholder="git" value="' +
          G.esc(editing ? editing.user : "") + '">');
      }
      f += field("Protocol", '<select id="f-proto">' +
        '<option value="http"' + (!editing || editing.protocol !== "ssh" ? " selected" : "") + ">http</option>" +
        '<option value="ssh"' + (editing && editing.protocol === "ssh" ? " selected" : "") + ">ssh</option></select>");
      f += field("SSH port", '<input type="number" id="f-sshport" value="' +
        G.esc(editing && editing.ssh_port ? editing.ssh_port : 22) + '">', "only used with ssh");
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
    });
    if (!editing) {
      document.getElementById("f-type").addEventListener("change", function (e) {
        state.formType = e.target.value; renderForm();
      });
    }
    document.getElementById("f-save").addEventListener("click", save);
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
    req.then(function (d) {
      G.toast((editing ? "saved " : "added ") + (editing ? editing.name : body.name), "ok");
      state.showForm = false; state.editing = null;
      document.getElementById("form-host").innerHTML = "";
      load();
    }).catch(function (e) { G.toast(e.error, "bad"); });
  }

  G.mount("platforms", render);
})();
