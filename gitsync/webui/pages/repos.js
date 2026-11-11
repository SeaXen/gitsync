/* Repos — browse an account's repos, pick some, sync just those
   (all targets / one site / local only), or save the selection to a file. */
(function () {
  var G = window.Gitsync;
  var state = { platform: "", platforms: [], repos: [], sel: {}, q: "", to: "" };

  function render(main) {
    main.innerHTML =
      G.bar("Repos", "Browse an account and sync a selection.",
        '<button class="btn" id="r-none">Select None</button>' +
        '<button class="btn" id="r-all">Select All</button>') +
      '<section class="card">' +
        '<div class="inline mb">' +
          '<div style="min-width:200px">' +
            G.field("Platform", '<select id="r-plat" name="platform"></select>') + "</div>" +
          '<div class="grow">' +
            G.field("Filter", '<input type="text" id="r-q" name="filter" autocomplete="off" placeholder="type to filter…">') + "</div>" +
          '<label class="check" style="align-self:flex-end;margin-bottom:14px"><input type="checkbox" id="r-forks" name="include_forks"> include forks</label>' +
        "</div>" +
        '<div id="r-list">' + G.loading("listing repos…") + "</div>" +
      "</section>" +
      '<section class="card" id="r-actions" style="display:none">' +
        '<div class="bar"><span class="route-title">Sync <span id="r-count">0</span> selected</span>' +
        '<div class="actions">' +
          '<div>' +
            G.field("Target", '<select id="r-to" name="target"></select>') + "</div>" +
          '<button class="btn" id="r-local">Local Only</button>' +
          '<button class="btn primary" id="r-sync">Sync Selected</button>' +
          '<button class="btn" id="r-save">Save to File</button>' +
        "</div></div>" +
        '<div class="muted mt">“Sync selected” pushes to the chosen target, ' +
        "or every target on the route if you leave it on “all targets”.</div>" +
      "</section>" +
      '<div id="r-live"></div>';

    G.api("GET", "/api/platforms?probe=0").then(function (d) {
      state.platforms = d.platforms || [];
      var enabled = state.platforms.filter(function (p) { return p.enabled; });
      document.getElementById("r-plat").innerHTML = state.platforms.map(function (p) {
        return '<option value="' + G.esc(p.name) + '"' +
          (p.enabled ? "" : " disabled") + ">" + G.esc(p.name) +
          " (" + G.esc(p.type) + ")</option>";
      }).join("");
      var first = enabled[0];
      state.platform = first ? first.name : "";
      var to = document.getElementById("r-to");
      to.innerHTML = '<option value="">all targets</option>' + enabled.map(function (p) {
        return '<option value="' + G.esc(p.name) + '">' + G.esc(p.name) + "</option>";
      }).join("");
      state.to = "";
      to.addEventListener("change", function () { state.to = to.value; });
      loadRepos();
      document.getElementById("r-plat").addEventListener("change", function () {
        state.platform = document.getElementById("r-plat").value;
        state.sel = {}; loadRepos();
      });
    }).catch(function (e) {
      document.getElementById("r-list").innerHTML = G.errorBox(e);
    });

    document.getElementById("r-q").addEventListener("input", function (e) {
      state.q = e.target.value.toLowerCase(); renderList(); updateActions();
    });
    document.getElementById("r-forks").addEventListener("change", loadRepos);
    document.getElementById("r-all").addEventListener("click", function () {
      filtered().forEach(function (r) { state.sel[r.name] = true; });
      renderList(); updateActions();
    });
    document.getElementById("r-none").addEventListener("click", function () {
      state.sel = {}; renderList(); updateActions();
    });
    document.getElementById("r-sync").addEventListener("click", function () {
      G.busy(this, function () { return doSync(null); });
    });
    document.getElementById("r-local").addEventListener("click", function () {
      var local = state.platforms.filter(function (p) {
        return p.type === "local" && p.enabled;
      })[0];
      if (!local) return G.toast("no local-folder platform configured", "warn");
      doSync(local.name);
    });
    document.getElementById("r-save").addEventListener("click", saveSelection);
  }

  function loadRepos() {
    document.getElementById("r-list").innerHTML = G.loading("listing repos…");
    var q = "?platform=" + encodeURIComponent(state.platform) +
      (document.getElementById("r-forks").checked ? "&forks=1" : "");
    G.api("GET", "/api/repos" + q).then(function (d) {
      state.repos = d.repos || []; renderList(); updateActions();
    }).catch(function (e) {
      document.getElementById("r-list").innerHTML = G.errorBox(e);
    });
  }

  function filtered() {
    return state.repos.filter(function (r) {
      return !state.q || r.name.toLowerCase().indexOf(state.q) >= 0;
    });
  }

  function renderList() {
    var host = document.getElementById("r-list");
    var rows = filtered();
    if (!rows.length) {
      host.innerHTML = G.emptyState(
        state.q ? "No matching repos" : "No repos",
        state.q ? "clear the filter to see all" : "this platform has none", "repos");
      return;
    }
    host.innerHTML = rows.map(function (r) {
      var on = !!state.sel[r.name];
      var mirrorBadge = r.mirrored ? ' <span class="badge ok" style="font-size:11px">mirrored</span>' : '';
      return '<label class="check" style="justify-content:flex-start">' +
        '<input type="checkbox" name="selected_repo" data-repo="' + G.esc(r.name) + '"' +
        (on ? " checked" : "") + "> " +
        '<span class="mono">' + G.esc(r.name) + "</span> " +
        '<span class="muted" style="font-size:12px">' + (r.private ? "priv" : "pub") +
        (r.fork ? " fork" : "") + "</span>" + mirrorBadge + "</label>";
    }).join("");
    host.querySelectorAll("input[data-repo]").forEach(function (c) {
      c.addEventListener("change", function () {
        if (c.checked) state.sel[c.dataset.repo] = true;
        else delete state.sel[c.dataset.repo];
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
    if (!names.length) return Promise.resolve();
    return G.startSync({ repos: names.join(","), to: to || state.to || undefined })
      .then(function (r) { showLive(names.length + " repo(s)", r.job_id); })
      .catch(function (e) {
        if (e.status === 409) G.toast(e.error || "already running", "warn");
        else G.toast(G.apiError(e), "bad");
      });
  }

  function saveSelection() {
    var names = selectedNames();
    if (!names.length) return G.toast("select some repos first", "warn");
    G.api("POST", "/api/repos/selection-file", { platform: state.platform, repos: names })
      .then(function (d) {
        G.toast(names.length + " repos saved to " + d.path + " → " +
          (d.routes || []).join(", "), "ok");
      }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function showLive(label, jobId) {
    var host = document.getElementById("r-live");
    host.innerHTML =
      '<section class="card"><div class="bar"><span class="route-title">Syncing ' +
      G.esc(label) + '</span><span class="muted mono" id="rl-pct"></span></div>' +
      '<div class="progress mt"><span id="rl-fill" style="width:0%"></span></div>' +
      '<div class="viewer mt" id="rl-lines" role="log" aria-live="polite"></div></section>';
    var lines = document.getElementById("rl-lines");
    lines.textContent = "running…";
    G.watchJob("/api/sync/stream?job_id=" + encodeURIComponent(jobId), {
      onProgress: function (p) {
        if (p.total) {
          document.getElementById("rl-pct").textContent =
            p.done + "/" + p.total;
          document.getElementById("rl-fill").style.width = G.pct(p.done, p.total) + "%";
        }
      },
      onLine: function (line, cls) {
        var div = document.createElement("div");
        div.className = cls; div.textContent = line;
        lines.appendChild(div); lines.scrollTop = lines.scrollHeight;
      },
      onDone: function (d) {
        G.toast("sync " + (d.exit === 0 ? "finished" : "exit " + d.exit),
          d.exit === 0 ? "ok" : "bad");
      }
    });
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  G.mount("repos", render);
})();
