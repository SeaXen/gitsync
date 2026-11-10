/* Repos — browse an account's repos, pick some, sync just those
   (all targets / one site / local only), or save the selection to a file. */
(function () {
  var G = window.Gitsync;
  var state = { platform: "", platforms: [], repos: [], sel: {}, q: "" };

  function render(main) {
    main.innerHTML =
      G.bar("Repos", "Browse an account and sync a selection.",
        '<button class="btn" id="r-none">Select none</button>' +
        '<button class="btn" id="r-all">Select all</button>') +
      '<section class="card">' +
        '<div class="inline mb">' +
          '<div class="field" style="margin:0;min-width:200px"><label>Platform</label>' +
          '<select id="r-plat"></select></div>' +
          '<div class="field grow" style="margin:0"><label>Filter</label>' +
          '<input type="text" id="r-q" placeholder="type to filter…"></div>' +
          '<label class="check"><input type="checkbox" id="r-forks"> include forks</label>' +
        "</div>" +
        '<div id="r-list">' + G.loading("listing repos…") + "</div>" +
      "</section>" +
      '<section class="card" id="r-actions" style="display:none">' +
        '<div class="bar"><span class="route-title">Sync <span id="r-count">0</span> selected</span>' +
        '<div class="actions">' +
          '<select id="r-to" style="min-height:44px"></select>' +
          '<button class="btn" id="r-local">Local only</button>' +
          '<button class="btn primary" id="r-sync">Sync selected</button>' +
          '<button class="btn" id="r-save">Save to file</button>' +
        "</div></div>" +
        '<div class="muted mt">“Sync selected” pushes to every target on the route; ' +
        "pick a site to push to just one.</div>" +
      "</section>" +
      '<div id="r-live"></div>';

    G.api("GET", "/api/platforms?probe=0").then(function (d) {
      state.platforms = d.platforms || [];
      var sel = document.getElementById("r-plat");
      sel.innerHTML = state.platforms.map(function (p) {
        return '<option value="' + G.esc(p.name) + '"' + (p.enabled ? "" : " disabled") + ">" +
          G.esc(p.name) + " (" + G.esc(p.type) + ")</option>";
      }).join("");
      var firstEnabled = state.platforms.filter(function (p) { return p.enabled; })[0];
      state.platform = firstEnabled ? firstEnabled.name : "";
      // target dropdown: every platform
      var to = document.getElementById("r-to");
      to.innerHTML = state.platforms.filter(function (p) { return p.enabled; })
        .map(function (p) { return '<option value="' + G.esc(p.name) + '">to ' + G.esc(p.name) + "</option>"; }).join("");
      loadRepos();
      sel.addEventListener("change", function () { state.platform = sel.value; state.sel = {}; loadRepos(); });
    }).catch(function (e) { document.getElementById("r-list").innerHTML = G.errorBox(e); });

    document.getElementById("r-q").addEventListener("input", function (e) { state.q = e.target.value.toLowerCase(); renderList(); });
    document.getElementById("r-forks").addEventListener("change", loadRepos);
    document.getElementById("r-all").addEventListener("click", function () {
      filtered().forEach(function (r) { state.sel[r.name] = true; }); renderList(); updateActions();
    });
    document.getElementById("r-none").addEventListener("click", function () { state.sel = {}; renderList(); updateActions(); });
    document.getElementById("r-sync").addEventListener("click", function () { doSync(null); });
    document.getElementById("r-local").addEventListener("click", function () {
      var local = state.platforms.filter(function (p) { return p.type === "local" && p.enabled; })[0];
      if (!local) return G.toast("no local-folder platform configured", "warn");
      doSync(local.name);
    });
    document.getElementById("r-to").addEventListener("change", function () {});
    document.getElementById("r-save").addEventListener("click", saveSelection);
  }

  function loadRepos() {
    document.getElementById("r-list").innerHTML = G.loading("listing repos…");
    var q = "?platform=" + encodeURIComponent(state.platform) +
      (document.getElementById("r-forks").checked ? "&forks=1" : "");
    G.api("GET", "/api/repos" + q).then(function (d) {
      state.repos = d.repos || []; renderList(); updateActions();
    }).catch(function (e) { document.getElementById("r-list").innerHTML = G.errorBox(e); });
  }

  function filtered() {
    return state.repos.filter(function (r) {
      return !state.q || r.name.toLowerCase().indexOf(state.q) >= 0;
    });
  }

  function renderList() {
    var host = document.getElementById("r-list");
    var rows = filtered();
    if (!rows.length) { host.innerHTML = '<div class="empty">no matching repos</div>'; return; }
    host.innerHTML = rows.map(function (r) {
      var on = !!state.sel[r.name];
      return '<label class="check" style="justify-content:flex-start">' +
        '<input type="checkbox" data-repo="' + G.esc(r.name) + '"' + (on ? " checked" : "") + "> " +
        '<span class="mono">' + G.esc(r.name) + "</span> " +
        '<span class="muted" style="font-size:12px">' + (r.private ? "priv" : "pub") +
        (r.fork ? " fork" : "") + "</span></label>";
    }).join("");
    host.querySelectorAll("input[data-repo]").forEach(function (c) {
      c.addEventListener("change", function () {
        if (c.checked) state.sel[c.dataset.repo] = true; else delete state.sel[c.dataset.repo];
        updateActions();
      });
    });
  }

  function selectedNames() { return Object.keys(state.sel); }

  function updateActions() {
    var n = selectedNames().length;
    document.getElementById("r-count").textContent = n;
    document.getElementById("r-actions").style.display = n ? "" : "none";
  }

  function doSync(to) {
    var names = selectedNames();
    if (!names.length) return;
    var body = { repos: names.join(",") };
    if (to) body.to = to;
    G.api("POST", "/api/sync", body).then(function (r) {
      showLive(names.length + " repo(s)", r.job_id);
    }).catch(function (e) {
      if (e.status === 409) G.toast(e.error || "already running", "warn");
      else G.toast(e.error || "could not start", "bad");
    });
  }

  function saveSelection() {
    var names = selectedNames();
    if (!names.length) return G.toast("select some repos first", "warn");
    G.api("POST", "/api/repos/selection-file", { platform: state.platform, repos: names })
      .then(function (d) {
        G.toast(names.length + " repos saved to " + d.path + " → " + d.routes.join(", "), "ok");
      }).catch(function (e) { G.toast(e.error, "bad"); });
  }

  function showLive(label, jobId) {
    var host = document.getElementById("r-live");
    host.innerHTML =
      '<section class="card"><div class="bar"><span class="route-title">Syncing ' +
      G.esc(label) + '</span><span class="muted mono" id="rl-pct"></span></div>' +
      '<div class="progress mt"><span id="rl-fill" style="width:0%"></span></div>' +
      '<div class="viewer mt" id="rl-lines"></div></section>';
    var lines = document.getElementById("rl-lines");
    G.sse("/api/sync/stream?job_id=" + encodeURIComponent(jobId), function (d) {
      if (d.progress && d.progress.total) {
        var pc = G.pct(d.progress.done, d.progress.total);
        document.getElementById("rl-pct").textContent = d.progress.done + "/" + d.progress.total;
        document.getElementById("rl-fill").style.width = pc + "%";
      }
      if (d.line) {
        var div = document.createElement("div"); div.className = G.stateClass(d.line);
        div.textContent = d.line; lines.appendChild(div); lines.scrollTop = lines.scrollHeight;
      }
      if (d.done) G.toast("sync " + (d.exit === 0 ? "finished" : "exit " + d.exit), d.exit === 0 ? "ok" : "bad");
    });
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  G.mount("repos", render);
})();
