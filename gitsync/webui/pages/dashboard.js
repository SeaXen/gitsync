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
    G.api("GET", "/api/routes").then(fillRoutes).catch(function () {});
    G.api("GET", "/api/log?tail=60").then(fillLog).catch(function (e) {
      fillLog({ error: e });
    });
    G.api("GET", "/api/settings").then(fillSettings).catch(function (e) {
      fillSettings({ error: e });
    });
  }

  var lastStatus = null;

  function fillStatus(s) {
    lastStatus = s;
    var sub = document.getElementById("dash-sub");
    if (sub) {
      if (s.schedule !== "off" && (!s.scheduler || s.scheduler.indexOf("not scheduled") !== -1 || s.scheduler.indexOf("none") !== -1)) {
        sub.innerHTML = "Schedule set to " + G.esc(s.schedule) +
          ' but no timer is installed — <button class="btn-link" id="dash-install-timer" type="button">Install timer</button>';
        var b = document.getElementById("dash-install-timer");
        if (b) {
          b.addEventListener("click", function () {
            G.busy(this, function () {
              return G.api("POST", "/api/scheduler/install", s.schedule ? { schedule: s.schedule } : {})
                .then(function (d) {
                  G.toast(d.status || "Timer installed", d.ok ? "ok" : "warn");
                  G.refreshStatus();
                }).catch(function (e) {
                  G.toast(G.apiError(e), "bad");
                });
            });
          });
        }
      } else {
        sub.textContent = "Timer: " + s.schedule + ", next run " +
          (s.next_run || "—") + " · scheduler: " + s.scheduler;
      }
    }
  }

  function cleanPlatError(err) {
    if (!err) return "unknown error";
    var s = String(err);
    if (/errno -2|name or service not known|getaddrinfo/i.test(s)) {
      return "Can't reach host — check network or DNS";
    }
    if (/timed? out/i.test(s)) {
      return "Connection timed out";
    }
    if (/401|403|bad credentials|rejected/i.test(s)) {
      return "Authentication failed — check token";
    }
    if (s.indexOf("API failed:") === 0) {
      return s.slice(11).trim();
    }
    return s;
  }

  function fillPlatforms(d) {
    var host = document.getElementById("plats-body");
    var rows = d.platforms || [];
    var live = rows.filter(function (r) { return r.enabled && r.ok; }).length;
    var totalRepos = rows.reduce(function (n, r) {
      return n + (r.repo_count || 0);
    }, 0);
    var lbl = document.getElementById("plats-label");
    if (lbl) lbl.textContent = "Platforms: " + rows.length + " sites, " + live +
      " connected · " + totalRepos + " repos";
    if (!rows.length) {
      host.innerHTML = G.emptyState("No platforms", "Add one on the Platforms page.", "platforms");
      return;
    }
    host.innerHTML = rows.map(function (r) {
      var dot = !r.enabled ? "off" : (r.ok ? "" : "bad");
      var right;
      if (!r.enabled) {
        right = '<span class="muted">disabled</span>';
      } else if (r.ok) {
        right = r.repo_count === null ? "?" : r.repo_count + " repos";
      } else {
        var cleanErr = cleanPlatError(r.info);
        right = '<span class="bad" title="' + G.esc(r.info) + '">' + G.esc(cleanErr) + "</span>";
      }
      return '<div class="row"><span><i class="dot ' + dot + '"></i>' +
        G.esc(r.name) + '</span><span class="mono muted">' + right + "</span>" +
        // "which repos?" — expandable list, fetched once per platform
        (r.ok && r.enabled
          ? ' <button class="linkbtn muted" data-repos="' + G.esc(r.name) +
            '" title="Show the repositories on this site" style="font-size:12px">repos ▾</button>'
          : "") + "</span></div>" +
        '<div class="repo-list" data-repolist="' + G.esc(r.name) + '" hidden></div>';
    }).join("");
    host.querySelectorAll("[data-repos]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var name = btn.getAttribute("data-repos");
        var list = host.querySelector('[data-repolist="' + name + '"]');
        if (!list) return;
        if (!list.hidden) {          // collapse
          list.hidden = true;
          btn.textContent = "repos ▾";
          return;
        }
        if (list.dataset.loaded) {
          list.hidden = false;
          btn.textContent = "repos ▴";
          return;
        }
        list.textContent = "loading…";
        list.hidden = false;
        btn.textContent = "repos ▴";
        G.api("GET", "/api/repos?platform=" + encodeURIComponent(name))
          .then(function (d) {
            var repos = (d.repos || []).slice().sort(function (a, b) {
              return a.name.localeCompare(b.name);
            });
            list.dataset.loaded = "1";
            if (!repos.length) {
              list.innerHTML = '<div class="muted">no repositories</div>';
              return;
            }
            list.innerHTML = repos.map(function (r) {
              return '<div class="r"><span>' + G.esc(r.name) + '</span><span class="' +
                (r.mirrored ? "ok" : "muted") + '">' +
                (r.mirrored ? "mirrored" : "not mirrored") + "</span></div>";
            }).join("");
          })
          .catch(function (e) {
            list.innerHTML = G.errorBox(e);
          });
      });
    });
  }
  function modeChip(mode) {
    var full = mode === "full";
    return '<span class="chip ' + (full ? "warn" : "ok") + '" title="' +
      (full
        ? "full = exact mirror. Pushes --mirror and prunes destination branches and tags that no longer exist on the source — destructive."
        : "safe = never deletes or force-pushes. Targets keep branches and tags the source no longer has.") +
      '">' + (full ? "full — prunes" : "safe — never deletes") + "</span>";
  }

  // "what is it doing right now?" — mode, schedule, next run and the rest of
  // the effective settings, on the dashboard instead of buried in Settings
  function fillSettings(d) {
    var host = document.getElementById("settings-body");
    if (!host) return;
    if (d.error) { host.innerHTML = G.errorBox(d.error); return; }
    var st = d.settings || {};
    var s = lastStatus || {};
    var notify = st.notify || {};
    var channels = [];
    if (notify.discord_set) channels.push("Discord");
    if (notify.telegram_set) channels.push("Telegram");
    var sched = (st.schedules || {})[st.schedule] || st.schedule || "—";
    var row = function (k, v) {
      return "<dt>" + G.esc(k) + '</dt><dd class="mono">' + v + "</dd>";
    };
    host.innerHTML =
      '<dl class="kv">' +
      row("Sync mode", modeChip(st.mode) + ' <span class="muted">' +
          (st.mode === "full"
            ? "destinations are pruned to match the source"
            : "destinations are never pruned or force-pushed") + "</span>") +
      row("State", s.paused
          ? '<span class="chip paused">paused — nothing will run</span>'
          : '<span class="chip ok">running on schedule</span>') +
      row("Schedule", G.esc(sched) + ' <span class="muted">next run ' +
          G.esc(s.next_run || "—") + "</span>") +
      row("Scheduler", G.esc(s.scheduler || st.scheduler || "—")) +
      row("Workers / forks", G.esc(st.workers) + " · forks " +
          (st.include_forks ? "included" : "excluded") +
          " · pace " + (st.pace || 0) + "s") +
      row("Mesh conflicts", G.esc(st.mesh_conflict || "newest")) +
      row("Notify", channels.length
          ? G.esc(channels.join(" + ")) + " on " + G.esc(notify.on)
          : '<span class="muted">not set</span>') +
      row("Config", '<span class="muted">' + G.esc((st.paths || {}).config) +
          "</span>") +
      row("Mirror hub", '<span class="muted">' + G.esc((st.paths || {}).mirror) +
          "</span>") +
      row("Log file", '<span class="muted">' + G.esc((st.paths || {}).log) +
          "</span>") +
      row("Version", G.esc(s.version || d.version || "—")) +
      "</dl>" +
      '<p class="muted" style="font-size:12.5px;margin:12px 0 0">' +
      "Change these on the <a href=\"settings.html\">Settings</a> page, " +
      "or from the terminal: <code>gitsync options</code>.</p>";
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
    var retryBtn = (last.failed && last.failed > 0)
      ? '<button class="btn sm warn" id="btn-retry-failed" style="margin-top:8px">Retry ' + last.failed + ' failed repo(s)</button>'
      : "";
    var head, subline;
    if (last.dry_run) {
      var wc = (last.would_change === null || last.would_change === undefined)
        ? (last.ok || 0) : last.would_change;
      head = '<div class="big">' + wc + " would change</div>";
      subline = "dry run — " + (last.ok || 0) + " of " + (last.repos || 0) +
        " already in sync, in " + (last.elapsed_sec || "?") + "s";
    } else {
      head = '<div class="big ' + (bad ? "bad" : "ok") + '">' + (last.ok || 0) + " OK</div>";
      subline = (last.failed || 0) + " failed" +
        (last.partial ? ", " + last.partial + " partial" : "") + " of " +
        (last.repos || 0) + ", in " + (last.elapsed_sec || "?") + "s";
    }
    host.innerHTML =
      head +
      '<div class="muted" style="margin-top:6px">' + subline + "</div>" +
      retryBtn +
      '<div class="mono spark">' + G.esc(G.spark(d.spark || [])) + "</div>" +
      '<div class="muted" style="font-size:12px">last ' + (d.spark || []).length + " runs</div>";
    var btn = document.getElementById("btn-retry-failed");
    if (btn) {
      btn.addEventListener("click", function () {
        G.busy(btn, function () {
          return G.startSync({ retry_failed: true }).then(function () {
            G.toast("Retrying failed repos…");
          }).catch(function (err) {
            if (err.status !== 409) G.toast(G.apiError(err), "bad");
          });
        });
      });
    }
  }

  function fillHealth(d) {
    var host = document.getElementById("health-body");
    var disk = d.disk || {};
    var hasUnknown = (d.tokens || []).some(function (t) { return t.level === "unknown"; });
    var tokClass = d.token_bad ? "bad" : (hasUnknown ? "warn" : "ok");
    var diskPct = disk.pct;
    var diskClass = diskPct === null ? "muted" : (diskPct >= 95 ? "bad" : (diskPct >= 85 ? "warn" : "muted"));
    var diskAria = diskPct >= 95 ? ' role="alert" aria-label="Critical disk space"' : (diskPct >= 85 ? ' aria-label="Warning disk space"' : "");
    var rows = (d.tokens || []).map(function (t) {
      var cls = (t.level === "expired" || t.level === "critical") ? "bad"
        : (t.level === "warning" || t.level === "unknown" ? "warn" : "muted");
      var detail = t.days_left === null || t.days_left === undefined
        ? "" : (t.days_left < 0 ? "expired " + (-t.days_left) + "d ago"
                                : t.days_left + "d left");
      return '<div class="row" style="padding:1px 0"><span class="mono">' +
        G.esc(t.platform) + '</span><span class="' + cls + '" style="font-size:12px">' +
        G.esc(detail || t.note || t.level) + "</span></div>";
    }).join("");
    host.innerHTML =
      '<div class="mono" style="line-height:1.9">hub size <span class="muted">' +
      G.esc(d.disk_human.size) + '</span> · mirrors <span class="muted">' +
      (disk.mirrors || 0) + '</span> · disk <span class="' + diskClass + '"' + diskAria + '>' +
      G.esc(diskPct === null ? "—" : diskPct + "% full") + "</span><br>" +
      'tokens <span class="' + tokClass + '">' + G.esc(d.token_summary) + "</span></div>" +
      (rows ? '<div class="mt" style="font-size:12.5px">' + rows + "</div>" : "");
  }
  function fillHealthErr(e) {
    document.getElementById("health-body").innerHTML = G.errorBox(e);
  }

  function fillRoutes(d) {
    var host = document.getElementById("routes-body");
    if (!host) return;
    var rows = d.routes || [];
    if (!rows.length) {
      host.innerHTML = '<div class="muted">No routes configured yet. <a href="routes.html">Create route →</a></div>';
      return;
    }
    host.innerHTML = rows.map(function (r) {
      var badge = '<span class="chip ' + (r.mode === "full" ? "warn" : "") + '" title="' +
        (r.mode === "full"
          ? "full: prunes destination branches and tags that no longer exist on the source"
          : "safe: never deletes or force-pushes on destinations") +
        '">mode: ' + G.esc(r.mode || "safe") + "</span>";
      var state = !r.enabled ? '<span class="chip paused">paused</span>' : '<span class="chip">active</span>';
      return '<div class="row" style="padding:4px 0"><span><strong>' + G.esc(r.name) + '</strong> (' +
        G.esc(r.source) + ' → ' + G.esc(r.targets.join(", ")) + ')</span><span>' + badge + ' ' + state + '</span></div>';
    }).join("");
    host.innerHTML += '<p class="muted" style="font-size:12.5px;margin:10px 0 0">' +
      "<strong>safe</strong> never deletes or force-pushes — destinations keep " +
      "branches and tags the source no longer has. <strong>full</strong> is an " +
      "exact mirror and prunes what the source dropped (destructive).</p>";
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
      '<section class="card" id="settings-card"><p class="label">Current Settings ' +
        '<a href="settings.html" style="text-transform:none;letter-spacing:0">Change →</a></p>' +
        '<div id="settings-body">' + G.loading() + "</div></section>" +
      '<section class="card" id="routes-card"><p class="label">Routes Status ' +
        '<a href="routes.html" style="text-transform:none;letter-spacing:0">Manage routes →</a></p>' +
        '<div class="stack" id="routes-body">' + G.loading() + "</div></section>" +
      '<section class="card" id="log-card"><p class="label">Log ' +
        '<a href="log.html" style="text-transform:none;letter-spacing:0">View full log →</a></p>' +
        '<div class="mono log" id="logtail">' + G.loading() + "</div></section>" +
      '<section class="card" id="ref-card"><p class="label">Quick reference</p>' +
        '<div class="mono" style="line-height:2">' +
        '<div><b>gitsync sync</b> — sync every enabled route now</div>' +
        '<div><b>gitsync sync --dry-run</b> — show what would change, change nothing</div>' +
        '<div><b>gitsync fix --dry-run</b> — find drift without pushing</div>' +
        '<div><b>gitsync doctor</b> — check tokens, mirrors and routes</div>' +
        '<div><b>gitsync tui</b> — the terminal dashboard</div>' +
        "</div>" +
        '<p class="muted" style="font-size:12.5px;margin:12px 0 0">' +
        "On this page: <b>Sync now</b> and <b>Dry run</b> are in the top bar, " +
        "<b>Pause</b> suspends every run, and each panel has a minimise button.</p>" +
      "</section>";

    refresh();
    G.onSync(function (jobId) { attachSync(jobId); });
    ["settings-card", "routes-card", "log-card", "ref-card"].forEach(function (id) {
      G.collapsible(document.getElementById(id), id);
    });
  });
})();
