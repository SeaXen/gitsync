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
    if (card) card.classList.remove("hide");
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
        if (card) card.classList.add("hide");
        refresh();
      }
    });
  }

  // ---- first-run wizard ------------------------------------------------
  // Four steps, all driven by the same API the individual pages use: add two
  // sites, connect them, pick (or take all) repos, preview, then sync. It
  // only appears while the config has no routes, so an existing setup never
  // sees it; "Hide" remembers that for the rest of the browser session.
  // started: once a sync has been launched from here, the wizard stays on
  // screen for the rest of the session — refresh() runs after every job and
  // would otherwise wipe it the moment the first route appears
  var WIZ = { plats: null, routes: null, started: false };
  var TYPES = ["github", "gitlab", "forgejo", "gitea", "bitbucket", "local"];

  function wizardHost() { return document.getElementById("wizard-host"); }

  function wizardCheck() {
    var host = wizardHost();
    if (!host) return;
    if (!WIZ.plats || !WIZ.routes) return;
    try {
      if (sessionStorage.getItem("gs-wizard-off") === "1") { host.innerHTML = ""; return; }
    } catch (e) { /* private mode */ }
    if (WIZ.routes.length && !WIZ.started) {
      host.innerHTML = "";
      return;
    }
    if (WIZ.started) {
      return;                 // mid-flight: leave the wizard exactly as it is
    }
    renderWizard();
  }

  function wizErr(e) { G.toast(G.apiError(e), "bad"); }

  function renderWizard() {
    var host = wizardHost();
    if (!host) return;
    var live = (WIZ.plats || []).filter(function (p) { return p.enabled; });
    var names = live.map(function (p) { return p.name; });
    var ready = live.length >= 2;
    var html = '<section class="card" id="wizard-card"><div class="bar"><div>' +
      '<span class="route-title">First run — four steps to a working backup</span>' +
      '<div class="muted sub">Add two sites, connect them, pick repos, preview, sync. ' +
      'Nothing is pushed until the last step.</div></div>' +
      '<div class="actions"><button class="btn sm ghost" id="wiz-hide" type="button">Hide</button></div></div>' +

      '<div class="stack mt">' +
      '<div><b>1. Sites</b> ' + (ready
        ? '<span class="chip ok">' + names.join(" + ") + "</span>"
        : '<span class="muted">— add at least two (a source and a target)</span>') + "</div>" +
      (ready ? "" :
        '<div class="form-grid mt">' +
        G.field("Type", G.select("wiz-type", "github", TYPES)) +
        G.field("Name", '<input type="text" id="wiz-name" spellcheck="false" placeholder="github…">', "short, used in routes") +
        G.field("Token", '<input type="password" id="wiz-token" autocomplete="off" spellcheck="false" placeholder="paste a token…">', "write-only; skipped for a local folder") +
        G.field("Folder", '<input type="text" id="wiz-path" spellcheck="false" placeholder="/path/to/bare/repos (type=local)…">') +
        "</div>" +
        '<div class="actions mt"><button class="btn primary" id="wiz-add" type="button">Add site</button>' +
        '<span class="muted" id="wiz-add-status"></span></div>') +

      '<div class="mt"><b>2. Route</b> ' + (WIZ.routes.length
        ? '<span class="chip ok">' + G.esc(WIZ.routes[0].name) + "</span>"
        : '<span class="muted">— pick which site is copied to which</span>') + "</div>" +
      (WIZ.routes.length ? "" : ready ?
        '<div class="form-grid mt">' +
        G.field("Source (truth)", G.select("wiz-src", names[0], names)) +
        '<div class="field"><label>Targets</label><div class="u-col6">' +
          names.map(function (n, i) {
            return '<label class="check"><input type="checkbox" class="wiz-t" value="' +
              G.esc(n) + '"' + (i === 1 ? " checked" : "") + "> " + G.esc(n) + "</label>";
          }).join("") + "</div></div></div>" +
        '<div class="actions mt"><button class="btn primary" id="wiz-route" type="button">Create route</button></div>'
        : '<div class="muted u-fs125">Add your second site above first.</div>') +

      '<div class="mt"><b>3. Repos</b> <span class="muted">— optional: copy everything, or tick what you want</span></div>' +
      (WIZ.routes.length ?
        '<div class="actions mt"><button class="btn" id="wiz-repos" type="button">List repos</button>' +
        '<button class="btn ghost" id="wiz-skiprepos" type="button">Take all of them</button></div>' +
        '<div id="wiz-repo-list" class="stack mt"></div>'
        : '<div class="muted u-fs125">Create the route first.</div>') +

      '<div class="mt"><b>4. Preview, then sync</b> <span class="muted">— a dry run changes nothing</span></div>' +
      (WIZ.routes.length ?
        '<div class="actions mt"><button class="btn" id="wiz-preview" type="button">Preview</button>' +
        '<button class="btn primary" id="wiz-sync" type="button">Run first sync</button></div>'
        : '<div class="muted u-fs125">Preview unlocks once the route exists.</div>') +
      '<div class="viewer mt hide" id="wiz-log" role="log" aria-live="polite"></div>' +
      "</div></section>";
    host.innerHTML = html;

    document.getElementById("wiz-hide").addEventListener("click", function () {
      try { sessionStorage.setItem("gs-wizard-off", "1"); } catch (e) {}
      host.innerHTML = "";
    });
    var add = document.getElementById("wiz-add");
    if (add) add.addEventListener("click", function () {
      G.busy(add, function () { return wizAdd(); });
    });
    var r = document.getElementById("wiz-route");
    if (r) r.addEventListener("click", function () { G.busy(r, function () { return wizRoute(); }); });
    var rl = document.getElementById("wiz-repos");
    if (rl) rl.addEventListener("click", function () { G.busy(rl, function () { return wizListRepos(); }); });
    var sk = document.getElementById("wiz-skiprepos");
    if (sk) sk.addEventListener("click", function () {
      G.api("PUT", "/api/routes/" + encodeURIComponent(WIZ.routes[0].name),
        { clear_filters: true }).then(function () {
          G.toast("every repo on the source will be copied", "ok");
          wizSync(true);
        }).catch(wizErr);
    });
    var pv = document.getElementById("wiz-preview");
    if (pv) pv.addEventListener("click", function () { G.busy(pv, function () { return wizSync(true); }); });
    var sy = document.getElementById("wiz-sync");
    if (sy) sy.addEventListener("click", function () {
      return G.modalConfirm({
        title: "Run the first sync",
        message: "This pushes mirrors to your target site. It never deletes anything in safe mode.",
        confirmText: "Sync now"
      }).then(function (ok) { if (ok) return wizSync(false); });
    });
  }

  function wizAdd() {
    var t = document.getElementById("wiz-type").value;
    var body = { type: t, name: (document.getElementById("wiz-name").value || "").trim() };
    if (!body.name) { G.toast("give the site a short name", "warn"); return Promise.resolve(); }
    if (t === "local") {
      body.path = (document.getElementById("wiz-path").value || "").trim();
    } else {
      var tok = (document.getElementById("wiz-token").value || "").trim();
      if (tok) body.token = tok;
    }
    var st = document.getElementById("wiz-add-status");
    return G.api("POST", "/api/platforms", body).then(function (d) {
      var ok = !(d.test && d.test.ok === false);
      G.toast("added " + body.name + (ok ? "" : " — test failed: " +
        ((d.test && d.test.info) || "")), ok ? "ok" : "warn");
      return G.api("GET", "/api/platforms");
    }).then(function (d) {
      WIZ.plats = d.platforms || [];
      renderWizard();
      var s2 = document.getElementById("wiz-add-status");
      if (s2) s2.textContent = "added — add one more site";
    }).catch(function (e) {
      if (st) st.textContent = "";
      wizErr(e);
    });
  }

  function wizRoute() {
    var targets = Array.prototype.slice.call(
      document.querySelectorAll(".wiz-t:checked")).map(function (c) { return c.value; });
    var body = { name: "main", source: document.getElementById("wiz-src").value,
                 targets: targets };
    return G.api("POST", "/api/routes", body).then(function () {
      G.toast("route main created", "ok");
      return G.api("GET", "/api/routes");
    }).then(function (d) {
      WIZ.routes = d.routes || [];
      renderWizard();
    }).catch(wizErr);
  }

  function wizListRepos() {
    var name = WIZ.routes[0].name;
    var host = document.getElementById("wiz-repo-list");
    host.innerHTML = G.loading("listing repos…");
    return G.api("GET", "/api/repos?platform=" +
      encodeURIComponent(WIZ.routes[0].source)).then(function (d) {
      var rows = d.repos || [];
      if (!rows.length) {
        host.innerHTML = '<div class="muted">No repos found on the source — ' +
          "check the token, or use a local folder that holds bare repos.</div>";
        return;
      }
      host.innerHTML = '<div class="muted u-fs125">' + rows.length +
        " repos — untick any you do not want:</div>" +
        '<div class="u-col6 u-mt8">' + rows.map(function (r) {
          return '<label class="check"><input type="checkbox" class="wiz-repo" value="' +
            G.esc(r.name) + '" checked> ' + G.esc(r.name) +
            (r.private ? ' <span class="chip">private</span>' : "") + "</label>";
        }).join("") + "</div>" +
        '<div class="actions mt"><button class="btn primary" id="wiz-saverepos" type="button">' +
        "Save selection</button></div>";
      document.getElementById("wiz-saverepos").addEventListener("click", function () {
        var inc = Array.prototype.slice.call(
          document.querySelectorAll(".wiz-repo:checked")).map(function (c) { return c.value; });
        if (!inc.length) { G.toast("tick at least one repo, or use Take all", "warn"); return; }
        G.api("PUT", "/api/routes/" + encodeURIComponent(name), { include: inc })
          .then(function () {
            G.toast(inc.length + " repos selected", "ok");
            return wizSync(true);
          }).catch(wizErr);
      });
    }).catch(function (e) {
      host.innerHTML = G.errorBox(e);
    });
  }

  function wizSync(dry) {
    WIZ.started = true;
    var log = document.getElementById("wiz-log");
    if (log) { log.classList.remove("hide"); log.innerHTML = ""; }
    return G.api("POST", "/api/sync", { dry_run: !!dry, route: WIZ.routes[0].name })
      .then(function (r) {
        if (log) {
          log.innerHTML = '<div class="muted"><span class="spinner"></span>' +
            (dry ? "previewing…" : "syncing…") + "</div>";
        }
        G.watchJob("/api/sync/stream?job_id=" + encodeURIComponent(r.job_id), {
          onLine: function (line, cls) {
            if (!log) return;
            var div = document.createElement("div");
            div.className = cls; div.textContent = line;
            log.appendChild(div); log.scrollTop = log.scrollHeight;
          },
          onDone: function (d) {
            G.toast((dry ? "preview" : "first sync") +
              (d.exit === 0 ? " finished" : " finished with errors"),
              d.exit === 0 ? "ok" : "bad");
            if (!dry && d.exit === 0 && log) {
              log.appendChild(document.createElement("div")).textContent =
                "✓ done — this route is now on the timer; the dashboard shows the next run.";
            }
            refresh();
          }
        });
      }).catch(function (e) {
        if (e && e.status === 409) G.toast(e.error || "a sync is already running", "warn");
        else wizErr(e);
      });
  }

  function refresh() {
    G.api("GET", "/api/status").then(fillStatus).catch(function (e) {
      var s = document.getElementById("dash-sub");
      if (s) s.textContent = G.apiError(e);
    });
    G.api("GET", "/api/platforms").then(function (d) {
      fillPlatforms(d); WIZ.plats = d.platforms || []; wizardCheck();
    }).catch(fillPlatErr);
    G.api("GET", "/api/history?n=40").then(fillLastRun).catch(function (e) {
      fillLastRun({ error: e });
    });
    G.api("GET", "/api/health").then(fillHealth).catch(fillHealthErr);
    G.api("GET", "/api/routes").then(function (d) {
      fillRoutes(d); WIZ.routes = d.routes || []; wizardCheck();
    }).catch(function () {});
    G.api("GET", "/api/log?tail=60").then(fillLog).catch(function (e) {
      fillLog({ error: e });
    });
    G.api("GET", "/api/settings").then(fillSettings).catch(function (e) {
      fillSettings({ error: e });
    });
  }

  var lastStatus = null;

  function staleBanner(st) {
    var host = document.getElementById("stale-banner");
    if (!host) return;
    if (!st || !st.stale) { host.hidden = true; host.innerHTML = ""; return; }
    var every = st.every_hours;
    var what = st.age_hours === null || st.age_hours === undefined
      ? "Nothing has synced yet."
      : "The last successful sync was " + G.ago(Math.round(st.age_hours * 3600)) + ".";
    var span = every ? ("This is set to run every " + every + "h.") : "";
    host.hidden = false;
    host.innerHTML =
      '<div class="banner ' + (st.age_hours === null ? "bad" : "warn") + '">' +
        '<span class="banner-icon" aria-hidden="true">!</span>' +
        '<div class="banner-body"><strong>Sync is overdue.</strong> ' +
          G.esc(what) + " " + G.esc(span) +
          '<div class="banner-actions">' +
            '<button class="btn primary" id="stale-sync" type="button">Sync now</button>' +
            '<a class="btn" href="settings.html#scheduler">Check the scheduler</a>' +
          "</div>" +
        "</div>" +
      "</div>";
    var b = document.getElementById("stale-sync");
    if (b) {
      b.addEventListener("click", function () {
        G.busy(b, function () { return G.startSync({ dry_run: false, force: false }); })
          .catch(function (e) {
            if (e && e.status === 409) G.toast("a sync is already running", "warn");
            else G.toast(G.apiError(e), "bad");
          });
      });
    }
  }

  function fillStatus(s) {
    lastStatus = s;
    staleBanner(s.stale);
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
          ? ' <button class="linkbtn muted u-fs12" data-repos="' + G.esc(r.name) +
            '" title="Show the repositories on this site">repos ▾</button>'
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
            list.innerHTML = repos.map(function (x) {
              // a local-folder platform has no hub mirror by design, so
              // "not mirrored" read like a warning on every single repo
              var state = x.mirrored ? "mirrored"
                : (d.type === "local" ? "local folder" : "not mirrored");
              return '<div class="r"><span>' + G.esc(x.name) + '</span><span class="' +
                (x.mirrored ? "ok" : "muted") + '">' + state + "</span></div>";
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
      row("Version", G.esc(st.version || s.version || "—")) +
      "</dl>" +
      '<p class="muted u-fs125 u-mt12">' +
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
        '<div class="muted u-mt6">no runs yet</div>';
      return;
    }
    var bad = (last.failed || 0) + (last.partial || 0);
    var retryBtn = (last.failed && last.failed > 0)
      ? '<button class="btn sm warn u-mt8" id="btn-retry-failed">Retry ' + last.failed + ' failed repo(s)</button>'
      : "";
    var head, subline;
    if (!last.dry_run && last.skipped && !last.repos && !bad) {
      // the timer fired but no route was due (schedules are hourly/daily):
      // "0 OK of 0" read like a failed run instead of a no-op
      head = '<div class="big muted">skipped</div>';
      subline = "the timer ran — no route was due yet";
    } else if (last.dry_run) {
      var wc = (last.would_change === null || last.would_change === undefined)
        ? (last.ok || 0) : last.would_change;
      head = '<div class="big">' + wc + " would change</div>";
      subline = "dry run — " + (last.ok || 0) + " of " + (last.repos || 0) +
        " already in sync, in " + (last.elapsed_sec === null || last.elapsed_sec === undefined ? "?" : last.elapsed_sec) + "s";
    } else {
      head = '<div class="big ' + (bad ? "bad" : "ok") + '">' + (last.ok || 0) + " OK</div>";
      subline = (last.failed || 0) + " failed" +
        (last.partial ? ", " + last.partial + " partial" : "") + " of " +
        (last.repos || 0) + ", in " + (last.elapsed_sec === null || last.elapsed_sec === undefined ? "?" : last.elapsed_sec) + "s";
    }
    host.innerHTML =
      head +
      '<div class="muted u-mt6">' + subline + "</div>" +
      retryBtn +
      '<div class="mono spark">' + G.esc(G.spark(d.spark || [])) + "</div>" +
      '<div class="muted u-fs12">last ' + (d.spark || []).length + " runs</div>";
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
      return '<div class="row u-py1"><span class="mono">' +
        G.esc(t.platform) + '</span><span class="' + cls + ' u-fs12">' +
        G.esc(detail || t.note || t.level) + "</span></div>";
    }).join("");
    host.innerHTML =
      '<div class="mono u-lh19">hub size <span class="muted">' +
      G.esc(d.disk_human.size) + '</span> · mirrors <span class="muted">' +
      (disk.mirrors || 0) + '</span> · disk <span class="' + diskClass + '"' + diskAria + '>' +
      G.esc(diskPct === null ? "—" : diskPct + "% full") + "</span><br>" +
      'tokens <span class="' + tokClass + '">' + G.esc(d.token_summary) + "</span></div>" +
      (rows ? '<div class="mt u-fs125">' + rows + "</div>" : "");
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
      return '<div class="row u-py4"><span><strong>' + G.esc(r.name) + '</strong> (' +
        G.esc(r.source) + ' → ' + G.esc(r.targets.join(", ")) + ')</span><span>' + badge + ' ' + state + '</span></div>';
    }).join("");
    host.innerHTML += '<p class="muted u-fs125 u-mt10">' +
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
      // filled by fillStatus(); a silent install (Docker with no scheduler,
      // a timer that was never installed) otherwise looks perfectly healthy
      '<div id="stale-banner" hidden></div>' +
      '<section class="card hide" id="sync-card">' +
        '<p class="label">Syncing Now</p>' +
        '<div class="row u-mb8">' +
          '<span class="mono" id="sync-label">starting…</span>' +
          '<span class="muted" id="sync-pct">0%</span></div>' +
        '<div class="progress"><span id="sync-fill" class="u-w0"></span></div>' +
      "</section>" +
      '<div id="wizard-host"></div>' +
      '<div class="grid">' +
        '<section class="card"><p class="label" id="plats-label">Platforms</p>' +
          '<div class="stack" id="plats-body">' + G.loading() + "</div></section>" +
        '<section class="card"><p class="label" id="lastrun-label">Last Run</p>' +
          '<div id="lastrun-body">' + G.loading() + "</div></section>" +
        '<section class="card"><p class="label">Disk &amp; Health</p>' +
          '<div id="health-body">' + G.loading() + "</div></section>" +
      "</div>" +
      '<section class="card" id="settings-card"><p class="label">Current Settings ' +
        '<a href="settings.html" class="u-notransform">Change →</a></p>' +
        '<div id="settings-body">' + G.loading() + "</div></section>" +
      '<section class="card" id="routes-card"><p class="label">Routes Status ' +
        '<a href="routes.html" class="u-notransform">Manage routes →</a></p>' +
        '<div class="stack" id="routes-body">' + G.loading() + "</div></section>" +
      '<section class="card" id="log-card"><p class="label">Log ' +
        '<a href="log.html" class="u-notransform">View full log →</a></p>' +
        '<div class="mono log" id="logtail">' + G.loading() + "</div></section>" +
      '<section class="card" id="ref-card"><p class="label">Quick reference</p>' +
        '<div class="mono u-lh19">' +
        '<div class="qr"><span class="c">gitsync sync</span><span class="d">— sync every enabled route now</span></div>' +
        '<div class="qr"><span class="c">gitsync sync --dry-run</span><span class="d">— show what would change, change nothing</span></div>' +
        '<div class="qr"><span class="c">gitsync fix --dry-run</span><span class="d">— find drift without pushing</span></div>' +
        '<div class="qr"><span class="c">gitsync doctor</span><span class="d">— check tokens, mirrors and routes</span></div>' +
        '<div class="qr"><span class="c">gitsync tui</span><span class="d">— the terminal dashboard</span></div>' +
        "</div>" +
        '<p class="muted u-fs125 u-mt12">' +
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
