/* Tools — doctor, compare, fix, tokens, disk, gc, restore, mirror.
   Each runs as a streamed job (same engine the CLI uses). */
(function () {
  var G = window.Gitsync;

  function toolCard(id, title, desc, fieldsHtml, runLabel) {
    return '<section class="card"><div class="bar"><div>' +
      '<span class="route-title">' + G.esc(title) + "</span>" +
      '<div class="muted sub">' + G.esc(desc) + "</div></div>" +
      '<div class="actions"><button class="btn primary" data-run="' + id + '">' +
      G.esc(runLabel || "Run") + "</button></div></div>" +
      (fieldsHtml ? '<div class="form-grid mt" data-fields="' + id + '">' + fieldsHtml + "</div>" : "") +
      "</section>";
  }
  function f(label, inner, hint) { return G.field(label, inner, hint); }

  function render(main) {
    main.innerHTML = G.bar("Tools", "Diagnostics and one-off actions — output streams live.") +
      '<div class="grid">' +
      toolCard("doctor", "Doctor", "Health check: config, platforms, tokens, routes, disk, scheduler.", "") +
      toolCard("doctorfix", "Doctor — auto-repair", "Also remove broken mirror shells so the next sync re-clones.", "", "Run --fix") +
      toolCard("compare", "Compare (drift)", "Check every repo's HEAD matches across a route. Read-only, can be slow.",
        f("Route (blank = all)", '<input type="text" id="compare-route" name="route" placeholder="all…">')) +
      toolCard("fix", "Fix drift", "Detect divergence and re-sync the mismatched repos.",
        f("Route (blank = all)", '<input type="text" id="fix-route" name="route" placeholder="all…">')) +
      toolCard("tokens", "Token health", "Check every credential for expiry / revocation.", "") +
      toolCard("disk", "Disk usage", "Hub size, per-platform size, free space, orphans.",
        f("Top N", '<input type="number" id="disk-top" name="top" value="10" min="0" inputmode="numeric">') +
        '<div class="field"><label>Options</label><label class="check"><input type="checkbox" id="disk-orphans" name="list_orphans"> list orphans</label></div>') +
      toolCard("gc", "Reclaim space", "reflog-expire + gc mirrors. Safe (reachable commits are kept). Takes the run lock.",
        f("Idle older than (days)", '<input type="number" id="gc-older" name="older_than_days" value="0" min="0" inputmode="numeric">') +
        '<div class="field"><label>Options</label><label class="check"><input type="checkbox" id="gc-agg" name="aggressive"> aggressive</label></div>') +
      toolCard("restore", "Restore from hub", "Push every hub mirror back out — for when the source host is gone.",
        f("To (required)", '<input type="text" id="restore-to" name="restore_to" placeholder="platform…">') +
        f("Only repos", '<input type="text" id="restore-repos" name="restore_repos" placeholder="comma, blank = all…">') +
        '<div class="field"><label>Options</label>' +
        '<label class="check"><input type="checkbox" id="restore-dry" name="restore_dry_run"> dry run</label>' +
        '<label class="check"><input type="checkbox" id="restore-prune" name="restore_prune"> prune (mode=full)</label>' +
        '<label class="check"><input type="checkbox" id="restore-public" name="restore_public"> create public</label></div>') +
      toolCard("mirror", "Mirror repos", "Sync one or more repos now.",
        f("Repos", '<input type="text" id="mirror-repos" name="mirror_repos" placeholder="comma-separated…">') +
        f("From", '<input type="text" id="mirror-from" name="mirror_from" placeholder="route source…">') +
        f("To", '<input type="text" id="mirror-to" name="mirror_to" placeholder="all targets…">')) +
      "</div>" +
      '<section class="card" id="tool-out-card" style="display:none">' +
        '<div class="bar"><span class="route-title" id="tool-out-title">Output</span>' +
        '<span class="muted mono" id="tool-out-status"></span></div>' +
        '<div class="viewer mt" id="tool-out" role="log" aria-live="polite"></div>' +
      "</section>";

    main.querySelectorAll("button[data-run]").forEach(function (b) {
      b.addEventListener("click", function () { G.busy(b, function () { return run(b.dataset.run); }); });
    });
  }

  function val(id) { var el = document.getElementById(id); return el ? el.value.trim() : ""; }
  function checked(id) { var el = document.getElementById(id); return el ? el.checked : false; }

  function run(kind) {
    var body = {}, label = kind;
    function handleErr(e) {
      if (e && e.status === 409) G.toast(e.error || "another job is running", "warn");
      else G.toast(G.apiError(e), "bad");
    }
    if (kind === "doctor") { body = {}; label = "doctor"; }
    else if (kind === "doctorfix") { body = { fix: true }; label = "doctor --fix"; }
    else if (kind === "compare") { body = { route: val("compare-route") }; label = "compare"; }
    else if (kind === "fix") { body = { route: val("fix-route") }; label = "fix"; }
    else if (kind === "tokens") { body = {}; label = "tokens"; }
    else if (kind === "disk") { body = { top: val("disk-top"), orphans: checked("disk-orphans") }; label = "disk"; }
    else if (kind === "gc") {
      return G.modalConfirm({
        title: "Reclaim mirror space",
        message: "gc rewrites mirrors to reclaim space. Safe (reachable commits are kept), but can take several minutes. Continue?",
        confirmText: "Run gc",
        danger: false
      }).then(function (ok) {
        if (!ok) return;
        body = { older_than: val("gc-older"), aggressive: checked("gc-agg"), kind: kind };
        return G.api("POST", "/api/jobs", body).then(function (r) {
          showOut("gc", r.job_id);
        }).catch(handleErr);
      });
    } else if (kind === "restore") {
      if (!val("restore-to")) return G.toast("restore needs a target platform", "warn");
      body = { to: val("restore-to"), repos: val("restore-repos"),
               dry_run: checked("restore-dry"), prune: checked("restore-prune"),
               public: checked("restore-public"), kind: kind };
      label = "restore → " + val("restore-to");
      var sendRestore = function () {
        return G.api("POST", "/api/jobs", body).then(function (r) {
          showOut(label, r.job_id);
        }).catch(handleErr);
      };
      if (!checked("restore-dry")) {
        return G.modalConfirm({
          title: "Restore from hub",
          message: "Restore pushes hub mirrors out to " + val("restore-to") + ". Continue?",
          confirmText: "Restore Mirrors",
          danger: true
        }).then(function (ok) {
          if (ok) return sendRestore();
        });
      }
      return sendRestore();
    } else if (kind === "mirror") {
      if (!val("mirror-repos")) return G.toast("mirror needs repo names", "warn");
      body = { repos: val("mirror-repos"), src: val("mirror-from"), to: val("mirror-to") };
      label = "mirror " + val("mirror-repos");
    }
    body.kind = kind;
    return G.api("POST", "/api/jobs", body).then(function (r) {
      showOut(label, r.job_id);
    }).catch(handleErr);
  }

  function showOut(label, jobId) {
    document.getElementById("tool-out-card").style.display = "";
    document.getElementById("tool-out-title").textContent = label;
    var lines = document.getElementById("tool-out");
    var status = document.getElementById("tool-out-status");
    lines.innerHTML = '<div class="muted"><span class="spinner"></span>running…</div>';
    status.textContent = "running…";
    G.watchJob("/api/jobs/" + encodeURIComponent(jobId) + "/stream", {
      onLine: function (line, cls) {
        var div = document.createElement("div");
        div.className = cls; div.textContent = line;
        lines.appendChild(div); lines.scrollTop = lines.scrollHeight;
      },
      onDone: function (d) {
        status.textContent = "done · exit " + d.exit + " · " + d.elapsed + "s";
        G.toast(label + (d.exit === 0 ? " finished" : " finished (exit " + d.exit + ")"),
          d.exit === 0 ? "ok" : "bad");
      },
      onLost: function () {
        status.textContent = "connection lost";
      }
    });
    document.getElementById("tool-out-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  G.mount("tools", render);
})();
