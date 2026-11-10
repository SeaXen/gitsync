/* Dashboard — mirrors the design's index.html, fed by the API.
   Shows live sync progress over SSE, platforms, last run, disk/health, log. */
(function () {
  var G = window.Gitsync;

  function spark(vals) {
    if (!vals || !vals.length) return "";
    var blocks = " ▁▂▃▄▅▆▇";
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = (hi - lo) || 1;
    return vals.map(function (v) {
      return blocks[Math.min(7, Math.floor((v - lo) / span * 7) || 0)];
    }).join("");
  }

  function lineHtml(line) {
    return '<div class="' + G.stateClass(line) + '">' + G.esc(line) + "</div>";
  }

  function attachSync(jobId) {
    var card = document.getElementById("sync-card");
    var label = document.getElementById("sync-label");
    var pctEl = document.getElementById("sync-pct");
    var fill = document.getElementById("sync-fill");
    var logTail = document.getElementById("logtail");
    card.style.display = "";
    G.sse("/api/sync/stream?job_id=" + encodeURIComponent(jobId), function (d) {
      if (d.progress) {
        var p = d.progress;
        if (p.total) {
          label.textContent = "[" + p.done + "/" + p.total + "] " +
            (p.repo ? p.repo + " " + (p.status || "") : "syncing…");
          var pc = G.pct(p.done, p.total);
          pctEl.textContent = pc + "%";
          fill.style.width = pc + "%";
        } else {
          label.textContent = "starting…";
        }
      }
      if (d.line && logTail) {
        var div = document.createElement("div");
        div.className = G.stateClass(d.line);
        div.textContent = d.line;
        logTail.appendChild(div);
      }
      if (d.done) {
        card.style.display = "none";
        G.toast(d.exit === 0 ? "Sync finished" : "Sync finished (exit " +
          d.exit + ")", d.exit === 0 ? "ok" : "bad");
        refresh();
      }
    });
  }

  function refresh() {
    G.api("GET", "/api/status").then(fillStatus).catch(function () {});
    G.api("GET", "/api/platforms").then(fillPlatforms).catch(fillPlatErr);
    G.api("GET", "/api/history?n=40").then(fillLastRun).catch(function () {});
    G.api("GET", "/api/health").then(fillHealth).catch(fillHealthErr);
    G.api("GET", "/api/log?tail=60").then(fillLog).catch(function () {});
  }

  function fillStatus(s) {
    var sub = "Timer: " + s.schedule + ", next run " +
      (s.next_run || "—") + " · scheduler: " + s.scheduler;
    document.getElementById("dash-sub").textContent = sub;
  }

  function fillPlatforms(d) {
    var host = document.getElementById("plats-body");
    var rows = d.platforms || [];
    var live = rows.filter(function (r) { return r.enabled && r.ok; }).length;
    document.getElementById("plats-label").textContent =
      "Platforms: " + rows.length + " sites, " + live + " connected";
    if (!rows.length) { host.innerHTML = '<div class="empty">no platforms</div>'; return; }
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
    var recs = d.records || [];
    var last = recs[recs.length - 1];
    document.getElementById("lastrun-label").textContent = "Last run";
    if (!last) {
      host.innerHTML = '<div class="big muted">—</div>' +
        '<div class="muted" style="margin-top:6px">no runs yet</div>';
      return;
    }
    var bad = (last.failed || 0) + (last.partial || 0);
    host.innerHTML =
      '<div class="big ' + (bad ? "bad" : "ok") + '">' + (last.ok || 0) + " ok</div>" +
      '<div class="muted" style="margin-top:6px">' +
      (last.failed || 0) + " failed" +
      (last.partial ? ", " + last.partial + " partial" : "") +
      " of " + (last.repos || 0) + ", in " + (last.elapsed_sec || "?") + "s</div>" +
      '<div class="mono spark">' + G.esc(spark(d.spark || [])) + "</div>" +
      '<div class="muted" style="font-size:12px">last ' + (d.spark || []).length +
      " runs</div>";
  }

  function fillHealth(d) {
    var host = document.getElementById("health-body");
    var disk = d.disk || {};
    var tokClass = d.token_bad ? "warn" : "ok";
    host.innerHTML =
      '<div class="mono" style="line-height:1.9">' +
      "hub size <span class='muted'>" + G.esc(d.disk_human.size) + "</span><br>" +
      "mirrors <span class='muted'>" + (disk.mirrors || 0) + "</span><br>" +
      "disk <span class='muted'>" + G.esc(disk.pct === null ? "—" : disk.pct + "% full") +
      "</span><br>" +
      "tokens <span class='" + tokClass + "'>" + G.esc(d.token_summary) + "</span>" +
      "</div>";
  }
  function fillHealthErr(e) {
    document.getElementById("health-body").innerHTML = G.errorBox(e);
  }

  function fillLog(d) {
    var host = document.getElementById("logtail");
    var lines = d.lines || [];
    if (!lines.length) { host.innerHTML = '<div class="empty">no log lines yet</div>'; return; }
    host.innerHTML = lines.map(lineHtml).join("");
  }

  G.mount("dashboard", function (main) {
    main.innerHTML =
      G.bar("Dashboard", "…",
        '<button class="btn" id="btn-dry">Dry run</button>' +
        '<button class="btn primary" id="btn-sync">Sync now</button>', "dash-sub") +
      '<section class="card" id="sync-card" style="display:none">' +
        '<p class="label">Syncing now</p>' +
        '<div class="row" style="margin-bottom:8px">' +
          '<span class="mono" id="sync-label">starting…</span>' +
          '<span class="muted" id="sync-pct">0%</span></div>' +
        '<div class="progress"><span id="sync-fill" style="width:0%"></span></div>' +
      "</section>" +
      '<div class="grid">' +
        '<section class="card"><p class="label" id="plats-label">Platforms</p>' +
          '<div class="stack" id="plats-body">' + G.loading("reading sites…") + "</div></section>" +
        '<section class="card"><p class="label" id="lastrun-label">Last run</p>' +
          '<div id="lastrun-body">' + G.loading() + "</div></section>" +
        '<section class="card"><p class="label">Disk and health</p>' +
          '<div id="health-body">' + G.loading() + "</div></section>" +
      "</div>" +
      '<section class="card"><p class="label">Log ' +
        '<a href="log.html" style="text-transform:none;letter-spacing:0">full →</a></p>' +
        '<div class="mono log" id="logtail">' + G.loading() + "</div></section>";

    function runSync(dry) {
      G.api("POST", "/api/sync", { dry_run: dry })
        .then(function (r) { attachSync(r.job_id); })
        .catch(function (e) {
          if (e.status === 409) G.toast(e.error || "already running", "warn");
          else G.toast(e.error || "could not start sync", "bad");
        });
    }
    document.getElementById("btn-sync").addEventListener("click", function () { runSync(false); });
    document.getElementById("btn-dry").addEventListener("click", function () { runSync(true); });

    refresh();
    // if a sync is already running (started elsewhere), attach to it live
    G.api("GET", "/api/sync/state").then(function (s) {
      if (s.syncing && s.job_id) attachSync(s.job_id);
    }).catch(function () {});
  });
})();
