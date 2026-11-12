/* Repos — browse an account's repos, pick some, sync just those
   (all targets / one site / local only), or save the selection to a file. */
(function () {
  var G = window.Gitsync;
  var state = { platform: "", platforms: [], repos: [], sel: {}, q: "", to: "",
                cmp: null, cmpQ: "", cmpRoute: "", cmpMap: {}, cmpRoutes: [] };

  function render(main) {
    main.innerHTML =
      G.bar("Repos", "Browse an account and sync a selection.",
        '<button class="btn" id="r-none">Select None</button>' +
        '<button class="btn" id="r-all">Select All</button>' +
        '<button class="btn" id="r-cmp-btn">Compare all sites</button>') +
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
      '<div id="r-live"></div>' +
      '<section class="card" id="r-cmp" style="display:none">' +
        '<div class="bar"><span class="route-title">Repo × site matrix</span>' +
        '<div class="actions">' +
          '<div id="r-cmp-route-wrap" style="min-width:150px;display:none">' +
            G.field("Route", '<select id="r-cmp-route" name="route"></select>') + "</div>" +
          '<div class="grow" style="min-width:180px">' +
            G.field("Find repo", '<input type="text" id="r-cmp-q" name="cmp_filter" autocomplete="off" placeholder="type to filter…">') + "</div>" +
          '<button class="btn" id="r-cmp-again">Re-run</button>' +
        "</div></div>" +
        '<div id="r-cmp-body">' + G.loading("comparing HEADs across every site…") + "</div>" +
      "</section>";

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
    document.getElementById("r-cmp-btn").addEventListener("click", function () {
      runCompare(this);
    });
    document.getElementById("r-cmp-again").addEventListener("click", function () {
      runCompare(this);
    });
    document.getElementById("r-cmp-q").addEventListener("input", function (e) {
      state.cmpQ = e.target.value.toLowerCase();
      if (state.cmp) renderMatrix();
    });
    autoCompare();
  }

  // ---- repo × site matrix (drift) ----
  function runCompare(btn) {
    var card = document.getElementById("r-cmp");
    var body = document.getElementById("r-cmp-body");
    card.style.display = "";
    var had = !!state.cmp;
    if (!had) {
      body.innerHTML = G.loading("comparing HEADs across every site…") +
        '<div class="viewer mt" id="r-cmp-lines" role="log" aria-live="polite"></div>';
    }
    var lines = document.getElementById("r-cmp-lines");
    if (lines) lines.textContent = had ? "re-checking…" : "starting…";
    if (!had) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    var req = { kind: "compare" };
    if (state.cmpRoute) req.route = state.cmpRoute;
    G.busy(btn, function () {
      return G.api("POST", "/api/jobs", req)
        .then(function (r) {
          var parsed = [];
          G.watchJob("/api/jobs/" + encodeURIComponent(r.job_id) + "/stream", {
            onLine: function (line) {
              // compare --json prints one matrix per route, one per line;
              // the human progress lines are mixed in
              try {
                var d = JSON.parse(line);
                if (d && d.repos && d.route) parsed.push(d);
              } catch (e) {}
              if (!lines) return;
              var div = document.createElement("div");
              div.className = G.stateClass(line);
              div.textContent = line.length > 400 ? line.slice(0, 400) + "…" : line;
              lines.appendChild(div);
              lines.scrollTop = lines.scrollHeight;
            },
            onDone: function (d) {
              var data = parsed.filter(function (m) {
                return m.route === state.cmpRoute;
              })[0] || parsed[0];
              if (!data) {
                if (!state.cmp) {
                  body.innerHTML = G.errorBox("compare produced no data (exit " +
                    (d.exit === undefined ? "?" : d.exit) + ")");
                }
                return;
              }
              state.cmpMap[data.route] = data;
              state.cmp = data;
              renderMatrix();
            }
          });
        })
        .catch(function (e) {
          if (e.status === 409) G.toast(e.error || "another job is running", "warn");
          else G.toast(G.apiError(e), "bad");
          if (!state.cmp) body.innerHTML = G.errorBox(e);
        });
    });
  }

  // the matrix is the point of the page, so it loads on its own: the cached
  // answer renders instantly and a stale one refreshes in the background.
  // One matrix per route — a config with a one-way and a mesh route must not
  // show whichever happened to be compared last.
  function autoCompare() {
    G.api("GET", "/api/routes").then(function (rd) {
      state.cmpRoutes = rd.routes || [];
      var sel = document.getElementById("r-cmp-route");
      var wrap = document.getElementById("r-cmp-route-wrap");
      if (!sel) return;
      sel.innerHTML = state.cmpRoutes.map(function (r) {
        return '<option value="' + G.esc(r.name) + '">' + G.esc(r.name) +
          " (" + G.esc(r.source) + ")</option>";
      }).join("");
      if (!state.cmpRoute) {
        state.cmpRoute = (state.cmpRoutes[0] || {}).name || "";
      }
      sel.value = state.cmpRoute;
      if (wrap) wrap.style.display = state.cmpRoutes.length > 1 ? "" : "none";
      sel.addEventListener("change", function () {
        state.cmpRoute = sel.value;
        state.cmp = state.cmpMap[state.cmpRoute] || null;
        if (state.cmp) renderMatrix();
        else runCompare(null);
      });
    }).catch(function () {});

    G.api("GET", "/api/compare").then(function (d) {
      (d.routes || []).forEach(function (m) { state.cmpMap[m.route] = m; });
      var mine = state.cmpMap[state.cmpRoute] ||
        (d.routes || [])[0] || null;
      if (d.cached && mine) {
        if (!state.cmpRoute) state.cmpRoute = mine.route;
        state.cmp = mine;
        renderMatrix();
      }
      if (!d.cached || d.age_sec > 120 || !state.cmpMap[state.cmpRoute]) {
        runCompare(null);
      }
    }).catch(function () { runCompare(null); });
  }

  function cell(sha, src, err) {
    if (err) {
      // a failed probe is NOT a missing repo — say so instead of guessing
      return '<td class="warn" title="could not check: ' + G.esc(err) +
        '">⚠ not checked</td>';
    }
    if (!sha) return '<td class="bad" title="not on this site">✗ missing</td>';
    var short = sha.slice(0, 8);
    if (sha === src) {
      return '<td class="ok mono" title="' + G.esc(sha) + '">' + short + " ✓</td>";
    }
    return '<td class="warn mono" title="' + G.esc(sha) + '">' + short + " ⇄</td>";
  }

  function renderMatrix() {
    var body = document.getElementById("r-cmp-body");
    var card = document.getElementById("r-cmp");
    if (card) card.style.display = "";      // auto-load renders with no click
    var d = state.cmp;
    var q = state.cmpQ;
    var rows = d.repos.filter(function (r) {
      return !q || r.name.toLowerCase().indexOf(q) >= 0;
    });
    var tgts = d.targets || [];
    var ok = rows.filter(function (r) {
      return r.src && !Object.keys(r.errors || {}).length &&
        tgts.every(function (t) { return r.targets[t] === r.src; });
    }).length;
    var unknown = rows.filter(function (r) {
      return Object.keys(r.errors || {}).length > 0;
    }).length;
    var extraBits = Object.keys(d.extra || {}).map(function (t) {
      return '<span class="chip warn">' + G.esc(t) + " +" +
        d.extra[t].length + " extra</span>";
    }).join("");
    var head = '<div class="inline mb">' +
      '<span class="chip">route ' + G.esc(d.route) + "</span>" +
      '<span class="chip">source ' + G.esc(d.source) + "</span>" +
      '<span class="chip ' + (d.problems ? "warn" : "ok") + '">' + rows.length +
      " repos · " + ok + " in sync · " + (rows.length - ok - unknown) + " off</span>" +
      (unknown ? '<span class="chip warn">' + unknown +
        " could not be checked</span>" : "") +
      extraBits +
      "</div>" +
      '<p class="muted" style="font-size:12.5px;margin:0 0 12px">' +
      "✓ same commit · ⇄ different commit · ✗ not on that site · " +
      "⚠ the site could not be reached, so nothing is known about that repo. " +
      "<b>full</b> mode prunes refs inside a repo; it never deletes a whole " +
      "repository from a target, which is what the “extra” counts are.</p>";
    if (!rows.length) {
      body.innerHTML = head + G.emptyState("No matching repos",
        "clear the filter to see all", "repos");
      return;
    }
    var table =
      '<div style="overflow-x:auto"><table aria-label="Repository commit per site">' +
      "<thead><tr><th>Repo</th><th>" + G.esc(d.source) + " (source)</th>" +
      tgts.map(function (t) { return "<th>" + G.esc(t) + "</th>"; }).join("") +
      "</tr></thead><tbody>" +
      rows.map(function (r) {
        var errs = r.errors || {};
        return "<tr><td class=\"mono\">" + G.esc(r.name) + "</td>" +
          '<td class="mono ' + (errs.__src__ ? "warn" : "") + '" title="' +
          G.esc(errs.__src__ || "") + '">' +
          (r.src ? r.src.slice(0, 8) : (errs.__src__ ? "⚠" : "—")) + "</td>" +
          tgts.map(function (t) {
            return cell(r.targets[t], r.src, errs[t]);
          }).join("") +
          "</tr>";
      }).join("") + "</tbody></table></div>";
    var extra = Object.keys(d.extra || {}).map(function (t) {
      return '<div class="mt"><strong>' + G.esc(t) + "</strong> has " +
        d.extra[t].length + " repo(s) the source does not:" +
        '<div class="mono muted" style="font-size:12px;margin-top:4px">' +
        G.esc(d.extra[t].join(", ")) + "</div></div>";
    }).join("");
    body.innerHTML = head + table + (extra || "") + pruneBox(d);
    var btn = document.getElementById("r-prune");
    if (btn) btn.addEventListener("click", doPrune);
  }

  // deletion is opt-in per target (route -> prune_repos) and per click, so
  // the button only exists when the route allows it — and always asks for
  // the route name before anything is removed
  function pruneBox(d) {
    var n = Object.keys(d.extra || {}).reduce(function (a, t) {
      return a + d.extra[t].length;
    }, 0);
    if (!n) return "";
    var allowed = (d.prune_repos || []);
    if (!allowed.length) {
      return '<p class="muted" style="font-size:12.5px;margin-top:14px">' +
        "To delete these, tick the target under “Delete extra repos” on the " +
        '<a href="routes.html">Routes</a> page (full mode only).</p>';
    }
    return '<div class="mt" style="border-top:1px solid var(--line);padding-top:12px">' +
      '<div class="row"><span><strong>' + n + " repo(s)</strong> can be " +
      "permanently deleted from " + G.esc(allowed.join(", ")) + "</span>" +
      '<button class="btn danger" id="r-prune">Delete them…</button></div>' +
      '<p class="muted" style="font-size:12.5px;margin-top:6px">' +
      "Deleting removes the repository and everything in it — issues, merge " +
      "requests, wiki, stars. It cannot be undone.</p></div>";
  }

  function doPrune() {
    var d = state.cmp;
    var n = Object.keys(d.extra || {}).reduce(function (a, t) {
      return a + d.extra[t].length;
    }, 0);
    var list = Object.keys(d.extra || {}).map(function (t) {
      return t + ": " + d.extra[t].join(", ");
    }).join("\n");
    G.modalConfirm({
      title: "Delete " + n + " repo(s)?",
      message: "Route " + d.route + " — the following repositories exist on a " +
        "target but not on " + d.source + ":\n\n" + list +
        "\n\nThis is permanent. Type the route name to confirm.",
      confirmText: "I understand — show me the confirmation",
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      return G.modalConfirm({
        title: "Type the route name",
        message: "Type “" + d.route + "” to delete " + n + " repo(s) from " +
          (d.prune_repos || []).join(", ") + ".",
        confirmText: "Delete permanently",
        danger: true,
        prompt: d.route
      });
    }).then(function (typed) {
      // the second modal resolves with the typed text, not a boolean
      if (!typed) return;
      return G.api("POST", "/api/jobs", { kind: "prune", route: d.route })
        .then(function (r) {
          G.toast("deleting " + n + " repo(s)…");
          var host = document.getElementById("r-live");
          host.innerHTML =
            '<section class="card"><div class="bar"><span class="route-title">' +
            "Deleting " + n + " repo(s) from " + G.esc(d.route) + "</span>" +
            '<span class="muted mono" id="rp-state">running…</span></div>' +
            '<div class="viewer mt" id="rp-lines" role="log" aria-live="polite"></div></section>';
          var lines = document.getElementById("rp-lines");
          host.scrollIntoView({ behavior: "smooth", block: "nearest" });
          G.watchJob("/api/jobs/" + encodeURIComponent(r.job_id) + "/stream", {
            onLine: function (line, cls) {
              var div = document.createElement("div");
              div.className = cls;
              div.textContent = line;
              lines.appendChild(div);
              lines.scrollTop = lines.scrollHeight;
            },
            onDone: function (ev) {
              var st = document.getElementById("rp-state");
              if (st) st.textContent = ev && ev.exit === 0 ? "done" : "exit " + (ev ? ev.exit : "?");
              state.cmp = null;
              runCompare(null);
            }
          });
        })
        .catch(function (e) {
          if (e.status === 409) G.toast(e.error || "another job is running", "warn");
          else G.toast(G.apiError(e), "bad");
        });
    });
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
