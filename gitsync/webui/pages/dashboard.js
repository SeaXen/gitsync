/* Dashboard — mirrors the design's index.html, fed by the API.
   Live sync progress arrives via the global sync coordinator (the top bar
   owns Sync/Dry-run); here we reflect the run in the card + log. */
(function () {
  var G = window.Gitsync;

  function lineHtml(line, cls) {
    return '<div class="' + cls + '">' + G.esc(line) + "</div>";
  }

  function attachSync(jobId) {
    var card = document.getElementById("sync-card");
    var label = document.getElementById("sync-label");
    var pctEl = document.getElementById("sync-pct");
    var fill = document.getElementById("sync-fill");
    var logTail = document.getElementById("logtail");
    if (card) card.style.display = "";
    G.watchJob("/api/sync/stream?job_id=" + encodeURIComponent(jobId), {
      onProgress: function (p) {
        if (p.total && label) {
          label.textContent = "[" + p.done + "/" + p.total + "] " +
            (p.repo ? p.repo + " " + (p.status || "") : "syncing…");
          var pc = G.pct(p.done, p.total);
          if (pctEl) pctEl.textContent = pc + "%";
          if (fill) fill.style.width = pc + "%";
        }
      },
      onLine: function (line, cls) {
        if (!logTail) return;
        var div = document.createElement("div");
        div.className = cls; div.textContent = line;
        logTail.appendChild(div);
      },
      onDone: function () {
        if (card) card.style.display = "none";
        refresh();
      }
    });
  }

  function refresh() {
    G.api("GET", "/api/status").then(fillStatus).catch(function (e) {
      var s = document.getElementById("dash-sub");
      if (s) s.textContent = G.apiError(e);
    });
    G.api("GET", "/api/platforms").then(fillPlatforms).catch(fillPlatErr);
    G.api("GET", "/api/history?n=40").then(fillLastRun).catch(function (e) {
      fillLastRun({ error: e });
    });
    G.api("GET", "/api/health").then(fillHealth).catch(fillHealthErr);
    G.api("GET", "/api/log?tail=60").then(fillLog).catch(function (e) {
      fillLog({ error: e });
    });
  }

  function fillStatus(s) {
    var sub = document.getElementById("dash-sub");
    if (sub) {
      sub.textContent = "Timer: " + s.schedule + ", next run " +
        (s.next_run || "—") + " · scheduler: " + s.scheduler;
    }
  }

  function fillPlatforms(d) {
    var host = document.getElementById("plats-body");
    var rows = d.platforms || [];
    var live = rows.filter(function (r) { return r.enabled && r.ok; }).length;
    var lbl = document.getElementById("plats-label");
    if (lbl) lbl.textContent = "Platforms: " + rows.length + " sites, " + live + " connected";
    if (!rows.length) {
      host.innerHTML = G.emptyState("No platforms", "Add one on the Platforms page.", "platforms");
      return;
    }
    host.innerHTML = rows.map(function (r) {
      var dot = !r.enabled ? "off" : (r.ok ? "" : "bad");
      var right = r.enabled
        ? (r.ok ? (r.repo_count === null ? "?" : r.repo_count + " repos")
                : '<span class="bad">' + G.esc(r.info.slice(0, 40)) + "</span>")
        : '<span class="muted">disabled</span>';
      return '<div class="row"><span><i class="dot ' + dot + '"></i>' +
        G.esc(r.name) + '</span><span class="mono muted">' + right + "</span></div>";
    }).join("");
  }
  function fillPlatErr(e) {
    document.getElementById("plats-body").innerHTML = G.errorBox(e);
  }

  function fillLastRun(d) {
    var host = document.getElementById("lastrun-body");
    if (d.error) { host.innerHTML = G.errorBox(d.error); return; }
    var recs = d.records || [];
    var last = recs[recs.length - 1];
    if (!last) {
      host.innerHTML = '<div class="big muted">—</div>' +
        '<div class="muted" style="margin-top:6px">no runs yet</div>';
      return;
    }
    var bad = (last.failed || 0) + (last.partial || 0);
    host.innerHTML =
      '<div class="big ' + (bad ? "bad" : "ok") + '">' + (last.ok || 0) + " OK</div>" +
      '<div class="muted" style="margin-top:6px">' + (last.failed || 0) + " failed" +
      (last.partial ? ", " + last.partial + " partial" : "") + " of " +
      (last.repos || 0) + ", in " + (last.elapsed_sec || "?") + "s</div>" +
      '<div class="mono spark">' + G.esc(G.spark(d.spark || [])) + "</div>" +
      '<div class="muted" style="font-size:12px">last ' + (d.spark || []).length + " runs</div>";
  }

  function fillHealth(d) {
    var host = document.getElementById("health-body");
    var disk = d.disk || {};
    var tokClass = d.token_bad ? "warn" : "ok";
    var rows = (d.tokens || []).map(function (t) {
      var cls = (t.level === "expired" || t.level === "critical") ? "bad"
        : (t.level === "warning" ? "warn" : "muted");
      var detail = t.days_left === null || t.days_left === undefined
        ? "" : (t.days_left < 0 ? "expired " + (-t.days_left) + "d ago"
                                : t.days_left + "d left");
      return '<div class="row" style="padding:1px 0"><span class="mono">' +
        G.esc(t.platform) + '</span><span class="' + cls + '" style="font-size:12px">' +
        G.esc(detail || t.level) + "</span></div>";
    }).join("");
    host.innerHTML =
      '<div class="mono" style="line-height:1.9">hub size <span class="muted">' +
      G.esc(d.disk_human.size) + '</span> · mirrors <span class="muted">' +
      (disk.mirrors || 0) + '</span> · disk <span class="muted">' +
      G.esc(disk.pct === null ? "—" : disk.pct + "% full") + "</span><br>" +
      'tokens <span class="' + tokClass + '">' + G.esc(d.token_summary) + "</span></div>" +
      (rows ? '<div class="mt" style="font-size:12.5px">' + rows + "</div>" : "");
  }
  function fillHealthErr(e) {
    document.getElementById("health-body").innerHTML = G.errorBox(e);
  }

  function fillLog(d) {
    var host = document.getElementById("logtail");
    if (d.error) { host.innerHTML = G.errorBox(d.error); return; }
    var lines = d.lines || [];
    if (!lines.length) {
      host.innerHTML = '<div class="empty">no log lines yet</div>';
      return;
    }
    host.innerHTML = lines.map(function (l) {
      return lineHtml(l, G.stateClass(l));
    }).join("");
  }

  G.mount("dashboard", function (main) {
    main.innerHTML =
      G.bar("Dashboard", "…", "", "dash-sub") +
      '<section class="card" id="sync-card" style="display:none">' +
        '<p class="label">Syncing Now</p>' +
        '<div class="row" style="margin-bottom:8px">' +
          '<span class="mono" id="sync-label">starting…</span>' +
          '<span class="muted" id="sync-pct">0%</span></div>' +
        '<div class="progress"><span id="sync-fill" style="width:0%"></span></div>' +
      "</section>" +
      '<div class="grid">' +
        '<section class="card"><p class="label" id="plats-label">Platforms</p>' +
          '<div class="stack" id="plats-body">' + G.loading() + "</div></section>" +
        '<section class="card"><p class="label" id="lastrun-label">Last Run</p>' +
          '<div id="lastrun-body">' + G.loading() + "</div></section>" +
        '<section class="card"><p class="label">Disk &amp; Health</p>' +
          '<div id="health-body">' + G.loading() + "</div></section>" +
      "</div>" +
      '<section class="card"><p class="label">Log ' +
        '<a href="log.html" style="text-transform:none;letter-spacing:0">View full log →</a></p>' +
        '<div class="mono log" id="logtail">' + G.loading() + "</div></section>";

    refresh();
    G.onSync(function (jobId) { attachSync(jobId); });
  });
})();
