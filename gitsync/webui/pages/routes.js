/* Routes — list, add, run/dry-run (live), pause/resume (route or target),
   edit filters + per-route options, remove. */
(function () {
  var G = window.Gitsync;
  var state = { routes: [], platforms: [], showAdd: false };
  var SCHED = ["inherit", "hourly", "6h", "12h", "daily", "weekly", "off"];

  function chips(r) {
    var c = [];
    c.push('<span class="chip">' + (r.direction === "mesh" ? "two-way mesh" : "one-way") + "</span>");
    var mode = r.mode || "safe";
    c.push('<span class="chip ' + (mode === "full" ? "warn" : "") + '">mode: ' + G.esc(mode) + "</span>");
    c.push('<span class="chip">' + G.esc(r.effective_schedule) +
      (r.schedule ? "" : " (inherited)") + "</span>");
    if (r.direction === "mesh" && r.mesh_conflict) {
      c.push('<span class="chip">conflict: ' + G.esc(r.mesh_conflict) + "</span>");
    }
    if (!r.enabled) c.push('<span class="chip paused">paused</span>');
    return c.join("");
  }

  function flow(r) {
    if (r.direction === "mesh") {
      var peers = [r.source].concat(r.targets).map(function (n) {
        var paused = r.paused_targets.indexOf(n) >= 0;
        return '<span class="chip' + (paused ? " paused" : "") + '">' + G.esc(n) +
          (paused ? " (paused)" : "") + "</span>";
      });
      return '<div class="flow mono">' + peers.join("⇄") + "</div>";
    }
    var tchips = r.targets.map(function (t) {
      var paused = r.paused_targets.indexOf(t) >= 0;
      return '<span class="chip' + (paused ? " paused" : "") + '">' + G.esc(t) +
        (paused ? " (paused)" : "") + "</span>";
    }).join("");
    return '<div class="mono u-mt14 u-mb6">' + G.esc(r.source) +
      ' <span class="muted">→</span></div>' + tchips;
  }

  function routeCard(r) {
    var filters = (r.include && r.include.length ? "include " + r.include.join(",") : "") +
      (r.exclude && r.exclude.length ? " exclude " + r.exclude.join(",") : "") +
      (r.include_file ? " file " + r.include_file : "");
    return '<section class="card" data-route="' + G.esc(r.name) + '">' +
      '<div class="bar"><div><span class="route-title">' + G.esc(r.name) + "</span> " +
        chips(r) + '</div><div class="actions u-row6">' +
        '<button class="btn sm" data-a="run" data-n="' + G.esc(r.name) + '" aria-label="Run route ' + G.esc(r.name) + '">Run</button>' +
        '<button class="btn sm" data-a="dry" data-n="' + G.esc(r.name) + '" aria-label="Dry run route ' + G.esc(r.name) + '">Dry run</button>' +
        '<details class="more-menu u-anchor">' +
          '<summary class="btn sm ghost u-plainitem" aria-label="More actions">⋯</summary>' +
          '<div class="menu-pop u-menu">' +
            '<button class="btn ghost sm u-left u-textleft" data-a="pause" data-n="' + G.esc(r.name) + '">' + (r.enabled ? "Pause" : "Resume") + '</button>' +
            '<button class="btn ghost sm u-left u-textleft" data-a="manage" data-n="' + G.esc(r.name) + '">Targets</button>' +
            '<button class="btn ghost sm u-left u-textleft" data-a="edit" data-n="' + G.esc(r.name) + '">Edit</button>' +
            '<button class="btn ghost sm danger u-left u-textleft" data-a="del" data-n="' + G.esc(r.name) + '">Delete</button>' +
          '</div>' +
        '</details>' +
      "</div></div>" + flow(r) +
      '<div class="muted u-mt8">next: ' + G.esc(r.next) +
        (filters ? ' · <span class="mono">' + G.esc(filters) + "</span>" : "") + "</div>" +
      '<div class="panel-host" data-panel="' + G.esc(r.name) + '"></div>' +
      "</section>";
  }

  function render(main) {
    main.innerHTML =
      G.bar("Routes", "Any direction between sites.",
        '<button class="btn primary" id="add-btn">Add route</button>') +
      '<div id="add-host"></div>' +
      '<div id="routes-host">' + G.loading() + "</div>" +
      '<div id="live-host"></div>';
    document.getElementById("add-btn").addEventListener("click", function () {
      state.showAdd = !state.showAdd; renderAdd();
    });
    loadPlatforms();
    loadRoutes();
  }

  function loadPlatforms() {
    G.api("GET", "/api/platforms?probe=0").then(function (d) {
      state.platforms = d.platforms || [];
      if (state.showAdd) renderAdd();
    }).catch(function () {});
  }

  function loadRoutes() {
    G.api("GET", "/api/routes").then(function (d) {
      state.routes = d.routes || [];
      renderRoutes();
    }).catch(function (e) {
      document.getElementById("routes-host").innerHTML = G.errorBox(e);
    });
  }

  function renderRoutes() {
    var host = document.getElementById("routes-host");
    if (!state.routes.length) {
      host.innerHTML = G.emptyState("No routes yet", "add one to start syncing", "routes") +
        '<div class="u-center"><button class="btn primary" id="add-btn2">Add one</button></div>';
      document.getElementById("add-btn2").addEventListener("click", function () {
        document.getElementById("add-btn").click();
      });
      return;
    }
    host.innerHTML = state.routes.map(routeCard).join("");
    host.querySelectorAll("button[data-a]").forEach(function (b) {
      b.addEventListener("click", function () { routeAct(b.dataset.a, b.dataset.n, b); });
    });
  }

  function closePanels() {
    document.querySelectorAll(".panel-host").forEach(function (p) {
      p.innerHTML = ""; p.dataset.open = "";
    });
  }
  function panelFor(name) {
    var el = null;
    document.querySelectorAll(".panel-host").forEach(function (p) {
      if (p.dataset.panel === name) el = p;
    });
    return el;
  }

  function routeAct(a, name, btn) {
    var r = state.routes.filter(function (x) { return x.name === name; })[0];
    var panel = panelFor(name);
    if (a === "run" || a === "dry") {
      runRoute(name, a === "dry");
    } else if (a === "pause") {
      G.api("POST", "/api/routes/" + encodeURIComponent(name) + "/" +
        (r.enabled ? "pause" : "resume"), {})
        .then(loadRoutes).catch(function (e) { G.toast(G.apiError(e), "bad"); });
    } else if (a === "manage") {
      var open = panel.dataset.open === "1";
      closePanels();
      if (!open) { panel.dataset.open = "1"; renderManage(panel, r); }
    } else if (a === "edit") {
      var open2 = panel.dataset.open === "1";
      closePanels();
      if (!open2) { panel.dataset.open = "1"; renderEdit(panel, r); }
    } else if (a === "del") {
      G.modalConfirm({
        title: "Remove route",
        message: "Remove route '" + name + "'? This cannot be undone.",
        confirmText: "Remove Route",
        danger: true
      }).then(function (ok) {
        if (!ok) return;
        G.busy(btn, function () {
          return G.api("DELETE", "/api/routes/" + encodeURIComponent(name))
            .then(function () { G.toast(name + " removed", "ok"); loadRoutes(); })
            .catch(function (e) { G.toast(G.apiError(e), "bad"); });
        });
      });
    }
  }

  function renderManage(panel, r) {
    var rows = ['<div class="row u-py8"><span><strong>' +
      G.esc(r.name) + '</strong> <span class="muted">whole route</span></span>' +
      '<button class="btn sm" data-t="">' + (r.enabled ? "Pause" : "Resume") + "</button></div>"];
    r.targets.forEach(function (t) {
      var paused = r.paused_targets.indexOf(t) >= 0;
      rows.push('<div class="row u-py8"><span class="mono">' +
        G.esc(t) + '</span><button class="btn sm' + (paused ? "" : " danger") +
        '" data-t="' + G.esc(t) + '">' + (paused ? "Resume" : "Pause") + "</button></div>");
    });
    panel.innerHTML = '<div class="mt u-topline">' +
      '<p class="label mt">Pause / resume</p>' + rows.join("") + "</div>";
    panel.querySelectorAll("button[data-t]").forEach(function (b) {
      b.addEventListener("click", function () {
        var t = b.dataset.t;
        var url = "/api/routes/" + encodeURIComponent(r.name) + "/" +
          (t === "" ? (r.enabled ? "pause" : "resume")
                    : (r.paused_targets.indexOf(t) >= 0 ? "resume" : "pause"));
        G.api("POST", url, t === "" ? {} : { targets: [t] })
          .then(loadRoutes).catch(function (e) { G.toast(G.apiError(e), "bad"); });
      });
    });
  }

  function renderEdit(panel, r) {
    var origInc = (r.include || []).join(",");
    var origExc = (r.exclude || []).join(",");
    var fileNote = r.include_file
      ? G.field("Selection file", '<input type="text" value="' +
          G.esc(r.include_file) + '" readonly>',
          "written by Repos → Save to File; typing an include list replaces it")
      : "";
    var f = fileNote +
      G.field("Include globs", '<input type="text" id="e-inc" name="include" placeholder="blank = all…" value="' +
        G.esc(origInc) + '">') +
      G.field("Exclude globs", '<input type="text" id="e-exc" name="exclude" placeholder="comma-separated globs…" value="' +
        G.esc(origExc) + '">') +
      G.field("Workers", G.select("e-workers", r.workers || "inherit", ["inherit", "1", "2", "4", "8"])) +
      G.field("Mirror mode", G.select("e-mode", r.mode || "inherit", ["inherit", "full", "safe"])) +
      (r.direction === "mesh"
        ? G.field("Conflicts", G.select("e-conflict", r.mesh_conflict || "inherit", ["inherit", "newest", "skip"]))
        : "") +
      G.field("Schedule", G.select("e-schedule", r.schedule || "inherit", SCHED)) +
      G.field("Notify on", G.select("e-notify", r.notify_on || "inherit",
        ["inherit", "failure", "success", "always", "never"]),
        "Overrides the global Notification mode for this route only") +
      G.field("Forked repos", G.select("e-forks",
        r.include_forks === null || r.include_forks === undefined ? "inherit"
          : (r.include_forks ? "include" : "skip"),
        ["inherit", "include", "skip"])) +
      '<div class="field"><label>Skip partners</label><div class="u-col6">' +
        '<label class="check"><input type="checkbox" id="e-archived"' +
          (r.skip_archived === false ? "" : " checked") +
          "> Skip archived repos (read-only on every host)</label>" +
        '<label class="check"><input type="checkbox" id="e-empty"' +
          (r.skip_empty ? " checked" : "") +
          "> Skip empty repos (local folders with no commits)</label>" +
        "</div></div>" +
      '<div class="field"><label>Delete extra repos</label>' +
        '<div class="u-col6">' +
        r.targets.map(function (t) {
          var on = (r.prune_repos || []).indexOf(t) >= 0;
          return '<label class="check"><input type="checkbox" name="prune_repos" ' +
            'value="' + G.esc(t) + '"' + (on ? " checked" : "") + "> " +
            G.esc(t) + "</label>";
        }).join("") +
        "</div>" +
        '<div class="hint">Permanently delete repositories on these targets ' +
        "that the source no longer has. Off by default, never run by the " +
        "timer, and only in full mode — see the Repos page to preview " +
        "first.</div></div>";
    panel.innerHTML =
      '<div class="mt u-topline">' +
      '<p class="label mt">Filters &amp; options</p>' +
      '<div class="form-grid">' + f + "</div>" +
      '<div class="actions"><button class="btn primary" id="e-save">Save Route</button> ' +
      '<button class="btn" id="e-clear">Clear Filters</button></div></div>';
    document.getElementById("e-save").addEventListener("click", function () {
      var body = {
        workers: document.getElementById("e-workers").value,
        mode: document.getElementById("e-mode").value,
        schedule: document.getElementById("e-schedule").value
      };
      // only send the filter fields when they changed: an untouched form
      // must never replace an include_file selection with an empty list
      var inc = document.getElementById("e-inc").value;
      var exc = document.getElementById("e-exc").value;
      if (inc !== origInc) body.include = splitList(inc);
      if (exc !== origExc) body.exclude = splitList(exc);
      if (r.direction === "mesh") body.mesh_conflict = document.getElementById("e-conflict").value;
      body.notify_on = document.getElementById("e-notify").value;
      var fk = document.getElementById("e-forks").value;
      body.include_forks = fk === "inherit" ? "inherit" : (fk === "include");
      body.skip_archived = document.getElementById("e-archived").checked;
      body.skip_empty = document.getElementById("e-empty").checked;
      body.prune_repos = Array.prototype.slice.call(
        panel.querySelectorAll("input[name=prune_repos]:checked"))
        .map(function (c) { return c.value; });
      G.busy(this, function () {
        return G.api("PUT", "/api/routes/" + encodeURIComponent(r.name), body)
          .then(function () { G.toast(r.name + " saved", "ok"); loadRoutes(); })
          .catch(function (e) { G.toast(G.apiError(e), "bad"); });
      });
    });
    document.getElementById("e-clear").addEventListener("click", function () {
      G.api("PUT", "/api/routes/" + encodeURIComponent(r.name), { clear_filters: true })
        .then(function () { G.toast("filters cleared", "ok"); loadRoutes(); })
        .catch(function (e) { G.toast(G.apiError(e), "bad"); });
    });
  }

  function splitList(s) {
    return (s || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
  }

  function renderAdd() {
    var host = document.getElementById("add-host");
    if (!state.showAdd) { host.innerHTML = ""; return; }
    var live = state.platforms.filter(function (p) { return p.enabled; });
    var names = live.map(function (p) { return p.name; });
    if (names.length < 2) {
      host.innerHTML = '<section class="card mb">' +
        G.emptyState("Add at least two platforms",
          "a route needs a source and a target", "routes") + "</section>";
      return;
    }
    var tchecks = names.map(function (n, i) {
      return '<label class="check"><input type="checkbox" class="t-target" name="route_targets" value="' +
        G.esc(n) + '"' + (i > 0 ? " checked" : "") + "> " + G.esc(n) + "</label>";
    }).join("");
    var f = G.field("Name", '<input type="text" id="a-name" name="route_name" placeholder="e.g. all, gh-to-gl…">') +
      G.field("Direction", G.select("a-dir", "one-way", ["one-way", "mesh"])) +
      G.field("Source", G.select("a-src", names[0], names)) +
      '<div class="field"><label>Targets</label>' + tchecks + "</div>" +
      G.field("Workers", G.select("a-workers", "inherit", ["inherit", "1", "2", "4", "8"])) +
      G.field("Mirror mode", G.select("a-mode", "inherit", ["inherit", "full", "safe"])) +
      G.field("Conflicts", G.select("a-conflict", "inherit", ["inherit", "newest", "skip"])) +
      G.field("Schedule", G.select("a-sched", "inherit", SCHED));
    host.innerHTML =
      '<section class="card mb"><div class="bar"><span class="route-title">Add Route</span>' +
      '<div class="actions"><button class="btn" id="a-cancel">Cancel</button>' +
      '<button class="btn primary" id="a-save">Add Route</button></div></div>' +
      '<div class="form-grid mt">' + f + "</div></section>";
    document.getElementById("a-cancel").addEventListener("click", function () {
      state.showAdd = false; host.innerHTML = "";
      document.getElementById("add-btn").focus();
    });
    document.getElementById("a-save").addEventListener("click", function () {
      G.busy(this, saveAdd);
    });
  }

  function saveAdd() {
    var name = document.getElementById("a-name").value.trim();
    var source = document.getElementById("a-src").value;
    var targets = Array.prototype.slice.call(
      document.querySelectorAll(".t-target:checked")).map(function (c) {
        return c.value;
      }).filter(function (t) { return t !== source; });
    if (!name) { G.toast("a route needs a name", "warn"); return Promise.resolve(); }
    if (!targets.length) { G.toast("pick at least one target", "warn"); return Promise.resolve(); }
    var body = {
      name: name, source: source, targets: targets,
      direction: document.getElementById("a-dir").value,
      workers: document.getElementById("a-workers").value,
      mode: document.getElementById("a-mode").value,
      mesh_conflict: document.getElementById("a-conflict").value,
      schedule: document.getElementById("a-sched").value
    };
    G.api("POST", "/api/routes", body).then(function () {
      G.toast("route added: " + name, "ok");
      state.showAdd = false;
      document.getElementById("add-host").innerHTML = "";
      loadRoutes();
    }).catch(function (e) { G.toast(G.apiError(e), "bad"); });
  }

  function runRoute(name, dry) {
    return G.startSync({ route: name, dry_run: dry })
      .then(function (r) { showLive(name, r.job_id, dry); })
      .catch(function (e) {
        if (e.status === 409) G.toast(e.error || "already running", "warn");
        else G.toast(G.apiError(e), "bad");
      });
  }

  function showLive(name, jobId, dry) {
    var host = document.getElementById("live-host");
    host.innerHTML =
      '<section class="card"><div class="bar"><span class="route-title">' +
      (dry ? "Dry run" : "Running") + ": " + G.esc(name) + "</span>" +
      '<span class="muted mono" id="live-pct"></span></div>' +
      '<div class="progress mt"><span id="live-fill" class="u-w0"></span></div>' +
      '<div class="viewer mt" id="live-lines" role="log" aria-live="polite">running…</div></section>';
    var lines = document.getElementById("live-lines");
    lines.textContent = "running…";
    G.watchJob("/api/sync/stream?job_id=" + encodeURIComponent(jobId), {
      onProgress: function (p) {
        if (p.total) {
          document.getElementById("live-pct").textContent =
            p.done + "/" + p.total + " (" + G.pct(p.done, p.total) + "%)";
          document.getElementById("live-fill").style.width = G.pct(p.done, p.total) + "%";
        }
      },
      onLine: function (line, cls) {
        var div = document.createElement("div");
        div.className = cls; div.textContent = line;
        lines.appendChild(div); lines.scrollTop = lines.scrollHeight;
      },
      onDone: function (d) {
        G.toast(name + (d.exit === 0 ? " finished" : " finished (exit " + d.exit + ")"),
          d.exit === 0 ? "ok" : "bad");
        loadRoutes();
      }
    });
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  G.mount("routes", render);
})();
