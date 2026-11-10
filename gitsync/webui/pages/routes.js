/* Routes — list, add, run/dry-run (live), pause/resume (route or target),
   edit filters + per-route options, remove. */
(function () {
  var G = window.Gitsync;
  var state = { routes: [], platforms: [], showAdd: false };
  var SCHED = ["inherit", "hourly", "6h", "12h", "daily", "weekly", "off"];

  function platName(n) { return n; }

  function chips(r) {
    var c = [];
    c.push('<span class="chip">' + (r.direction === "mesh" ? "two-way mesh" : "one-way") + "</span>");
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
    return '<div class="mono" style="margin:14px 0 6px">' + G.esc(r.source) +
      ' <span class="muted">→</span></div>' + tchips;
  }

  function routeCard(r) {
    var filters = (r.include && r.include.length ? "include " + r.include.join(",") : "") +
      (r.exclude && r.exclude.length ? " exclude " + r.exclude.join(",") : "") +
      (r.include_file ? " file " + r.include_file : "");
    return '<section class="card" data-route="' + G.esc(r.name) + '">' +
      '<div class="bar"><div><span class="route-title">' + G.esc(r.name) + "</span> " +
        chips(r) + '</div><div class="actions">' +
        '<button class="btn sm" data-a="run" data-n="' + G.esc(r.name) + '">Run</button>' +
        '<button class="btn sm" data-a="dry" data-n="' + G.esc(r.name) + '">Dry run</button>' +
        '<button class="btn sm" data-a="pause" data-n="' + G.esc(r.name) + '">' +
          (r.enabled ? "Pause" : "Resume") + "</button>" +
        '<button class="btn sm" data-a="manage" data-n="' + G.esc(r.name) + '">Targets</button>' +
        '<button class="btn sm" data-a="edit" data-n="' + G.esc(r.name) + '">Edit</button>' +
        '<button class="btn sm danger" data-a="del" data-n="' + G.esc(r.name) + '">Delete</button>' +
      "</div></div>" + flow(r) +
      '<div class="muted" style="margin-top:8px">next: ' + G.esc(r.next) +
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
      host.innerHTML = '<div class="state">No routes yet. ' +
        '<button class="btn sm primary" id="add-btn2">Add one</button></div>';
      document.getElementById("add-btn2").addEventListener("click", function () {
        document.getElementById("add-btn").click();
      });
      return;
    }
    host.innerHTML = state.routes.map(routeCard).join("");
    host.querySelectorAll("button[data-a]").forEach(function (b) {
      b.addEventListener("click", function () { routeAct(b.dataset.a, b.dataset.n); });
    });
  }

  function routeAct(a, name) {
    var r = state.routes.filter(function (x) { return x.name === name; })[0];
    var panel = document.querySelector('.panel-host[data-panel="' + cssEsc(name) + '"]');
    if (a === "run" || a === "dry") {
      runRoute(name, a === "dry");
    } else if (a === "pause") {
      G.api("POST", "/api/routes/" + encodeURIComponent(name) + "/" +
        (r.enabled ? "pause" : "resume"), {})
        .then(loadRoutes).catch(function (e) { G.toast(e.error, "bad"); });
    } else if (a === "manage") {
      var open = panel.dataset.open === "1";
      document.querySelectorAll(".panel-host").forEach(function (p) { p.innerHTML = ""; p.dataset.open = ""; });
      if (!open) { panel.dataset.open = "1"; renderManage(panel, r); }
    } else if (a === "edit") {
      var open2 = panel.dataset.open === "1";
      document.querySelectorAll(".panel-host").forEach(function (p) { p.innerHTML = ""; p.dataset.open = ""; });
      if (!open2) { panel.dataset.open = "1"; renderEdit(panel, r); }
    } else if (a === "del") {
      if (!confirm("Remove route '" + name + "'?")) return;
      G.api("DELETE", "/api/routes/" + encodeURIComponent(name))
        .then(function () { G.toast(name + " removed", "ok"); loadRoutes(); })
        .catch(function (e) { G.toast(e.error, "bad"); });
    }
  }

  function cssEsc(s) { return String(s).replace(/"/g, '\\"'); }

  function renderManage(panel, r) {
    var rows = ['<div class="row" style="padding:8px 0"><span><strong>' + G.esc(r.name) +
      '</strong> <span class="muted">whole route</span></span>' +
      '<button class="btn sm" data-t="">' + (r.enabled ? "Pause" : "Resume") + "</button></div>"];
    r.targets.forEach(function (t) {
      var paused = r.paused_targets.indexOf(t) >= 0;
      rows.push('<div class="row" style="padding:8px 0"><span class="mono">' + G.esc(t) + "</span>" +
        '<button class="btn sm' + (paused ? "" : " danger") + '" data-t="' + G.esc(t) + '">' +
        (paused ? "Resume" : "Pause") + "</button></div>");
    });
    panel.innerHTML = '<div class="mt" style="border-top:1px solid var(--line)">' +
      '<p class="label mt">Pause / resume</p>' + rows.join("") + "</div>";
    panel.querySelectorAll("button[data-t]").forEach(function (b) {
      b.addEventListener("click", function () {
        var t = b.dataset.t;
        var url = "/api/routes/" + encodeURIComponent(r.name) + "/" +
          (t === "" ? (r.enabled ? "pause" : "resume")
                    : (r.paused_targets.indexOf(t) >= 0 ? "resume" : "pause"));
        G.api("POST", url, t === "" ? {} : { targets: [t] })
          .then(loadRoutes).catch(function (e) { G.toast(e.error, "bad"); });
      });
    });
  }

  function renderEdit(panel, r) {
    var sel = function (name, cur, opts) {
      return '<select id="e-' + name + '">' + opts.map(function (o) {
        return '<option value="' + o + '"' + (String(cur) === String(o) || (cur == null && o === "inherit") ? " selected" : "") + ">" + o + "</option>";
      }).join("") + "</select>";
    };
    panel.innerHTML =
      '<div class="mt" style="border-top:1px solid var(--line)">' +
      '<p class="label mt">Filters &amp; options</p>' +
      '<div class="form-grid">' +
      field("Include globs", '<input type="text" id="e-inc" placeholder="blank = all" value="' + G.esc((r.include || []).join(",")) + '">') +
      field("Exclude globs", '<input type="text" id="e-exc" value="' + G.esc((r.exclude || []).join(",")) + '">') +
      field("Workers", sel("workers", r.workers, ["inherit", "1", "2", "4", "8"])) +
      field("Mirror mode", sel("mode", r.mode, ["inherit", "full", "safe"])) +
      (r.direction === "mesh" ? field("Conflicts", sel("conflict", r.mesh_conflict, ["inherit", "newest", "skip"])) : "") +
      field("Schedule", sel("schedule", r.schedule, SCHED)) +
      "</div>" +
      '<div class="actions"><button class="btn primary" id="e-save">Save</button> ' +
      '<button class="btn" id="e-clear">Clear filters</button></div></div>';
    document.getElementById("e-save").addEventListener("click", function () {
      var body = {
        include: splitList(document.getElementById("e-inc").value),
        exclude: splitList(document.getElementById("e-exc").value),
        workers: document.getElementById("e-workers").value,
        mode: document.getElementById("e-mode").value,
        schedule: document.getElementById("e-schedule").value
      };
      if (r.direction === "mesh") body.mesh_conflict = document.getElementById("e-conflict").value;
      G.api("PUT", "/api/routes/" + encodeURIComponent(r.name), body)
        .then(function () { G.toast(r.name + " saved", "ok"); loadRoutes(); })
        .catch(function (e) { G.toast(e.error, "bad"); });
    });
    document.getElementById("e-clear").addEventListener("click", function () {
      G.api("PUT", "/api/routes/" + encodeURIComponent(r.name), { clear_filters: true })
        .then(function () { G.toast("filters cleared", "ok"); loadRoutes(); })
        .catch(function (e) { G.toast(e.error, "bad"); });
    });
  }

  function splitList(s) {
    return (s || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean);
  }

  function field(label, inner) {
    return '<div class="field"><label>' + G.esc(label) + "</label>" + inner + "</div>";
  }

  function renderAdd() {
    var host = document.getElementById("add-host");
    if (!state.showAdd) { host.innerHTML = ""; return; }
    var live = state.platforms.filter(function (p) { return p.enabled; });
    var names = live.map(function (p) { return p.name; });
    if (names.length < 2) {
      host.innerHTML = '<section class="card mb"><div class="state">Add at least two ' +
        "platforms before creating a route.</div></section>";
      return;
    }
    var tchecks = names.map(function (n, i) {
      return '<label class="check"><input type="checkbox" class="t-target" value="' +
        G.esc(n) + '"' + (i > 0 ? " checked" : "") + "> " + G.esc(n) + "</label>";
    }).join("");
    host.innerHTML =
      '<section class="card mb"><div class="bar"><span class="route-title">Add route</span>' +
      '<div class="actions"><button class="btn" id="a-cancel">Cancel</button>' +
      '<button class="btn primary" id="a-save">Add</button></div></div>' +
      '<div class="form-grid mt">' +
      field("Name", '<input type="text" id="a-name" placeholder="e.g. all, gh-to-gl">') +
      field("Direction", '<select id="a-dir"><option value="one-way">one-way</option>' +
        '<option value="mesh">two-way mesh</option></select>') +
      field("Source", '<select id="a-src">' + names.map(function (n) {
        return '<option value="' + G.esc(n) + '">' + G.esc(n) + "</option>";
      }).join("") + "</select>") +
      '<div class="field"><label>Targets</label>' + tchecks + "</div>" +
      field("Workers", optSel("a-workers", "inherit", ["inherit", "1", "2", "4", "8"])) +
      field("Mirror mode", optSel("a-mode", "inherit", ["inherit", "full", "safe"])) +
      field("Conflicts", optSel("a-conflict", "inherit", ["inherit", "newest", "skip"])) +
      field("Schedule", optSel("a-sched", "inherit", SCHED)) +
      "</div></section>";
    document.getElementById("a-cancel").addEventListener("click", function () {
      state.showAdd = false; host.innerHTML = "";
    });
    document.getElementById("a-save").addEventListener("click", saveAdd);
    function saveAdd() {
      var name = document.getElementById("a-name").value.trim();
      var source = document.getElementById("a-src").value;
      var targets = Array.prototype.slice.call(
        document.querySelectorAll(".t-target:checked")).map(function (c) { return c.value; })
        .filter(function (t) { return t !== source; });
      if (!name) return G.toast("a route needs a name", "warn");
      if (!targets.length) return G.toast("pick at least one target", "warn");
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
        state.showAdd = false; host.innerHTML = ""; loadRoutes();
      }).catch(function (e) { G.toast(e.error, "bad"); });
    }
  }

  function optSel(id, cur, opts) {
    return '<select id="' + id + '">' + opts.map(function (o) {
      return '<option value="' + o + '"' + (o === cur ? " selected" : "") + ">" + o + "</option>";
    }).join("") + "</select>";
  }

  function runRoute(name, dry) {
    G.api("POST", "/api/sync", { route: name, dry_run: dry })
      .then(function (r) { showLive(name, r.job_id, dry); })
      .catch(function (e) {
        if (e.status === 409) G.toast(e.error || "already running", "warn");
        else G.toast(e.error || "could not start", "bad");
      });
  }

  function showLive(name, jobId, dry) {
    var host = document.getElementById("live-host");
    host.innerHTML =
      '<section class="card"><div class="bar"><span class="route-title">' +
      (dry ? "Dry run" : "Running") + ": " + G.esc(name) + "</span>" +
      '<span class="muted mono" id="live-pct"></span></div>' +
      '<div class="progress mt"><span id="live-fill" style="width:0%"></span></div>' +
      '<div class="viewer mt" id="live-lines"></div></section>';
    var lines = document.getElementById("live-lines");
    G.sse("/api/sync/stream?job_id=" + encodeURIComponent(jobId), function (d) {
      if (d.progress && d.progress.total) {
        var pc = G.pct(d.progress.done, d.progress.total);
        document.getElementById("live-pct").textContent =
          d.progress.done + "/" + d.progress.total + " (" + pc + "%)";
        document.getElementById("live-fill").style.width = pc + "%";
      }
      if (d.line) {
        var div = document.createElement("div");
        div.className = G.stateClass(d.line);
        div.textContent = d.line;
        lines.appendChild(div);
        lines.scrollTop = lines.scrollHeight;
      }
      if (d.done) {
        G.toast(name + (d.exit === 0 ? " finished" : " finished (exit " + d.exit + ")"),
          d.exit === 0 ? "ok" : "bad");
        loadRoutes();
      }
    });
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  G.mount("routes", render);
})();
