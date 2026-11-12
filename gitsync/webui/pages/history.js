/* History — past runs, duration sparkline, failure details. */
(function () {
  var G = window.Gitsync;

  function render(main) {
    main.innerHTML = G.bar("History", "Recent sync runs, newest first.") +
      '<section class="card"><div id="h-host">' + G.loading() + "</div></section>";
    G.api("GET", "/api/history?n=60").then(function (d) {
      var recs = (d.records || []).slice().reverse();
      var host = document.getElementById("h-host");
      if (!recs.length) {
        host.innerHTML = G.emptyState("No runs recorded yet", "Run a sync to see history.", "history");
        return;
      }
      var sparkHtml = (d.spark && d.spark.length)
        ? '<div class="mono spark">' + G.esc(G.spark(d.spark)) + '</div>' +
          '<div class="muted" style="font-size:12px">duration trend, last ' +
          d.spark.length + " runs</div>"
        : "";
      var rows = recs.map(function (r, i) {
        var bad = (r.failed || 0) + (r.partial || 0);
        var fails = (r.failures || []).map(function (f) {
          return '<div class="mono l-bad" style="padding-left:14px">! ' +
            G.esc(f.route) + "/" + G.esc(f.repo) + ": " + G.esc(f.error) + "</div>";
        }).join("");
        var okCell;
        if (!r.dry_run && r.skipped && !r.repos && !bad) {
          // scheduled tick with nothing due: not a 0-repo run
          okCell = "<td class='mono muted' title='the timer ran, no route was " +
            "due'>skipped</td>";
        } else if (r.dry_run) {
          var wc = (r.would_change === null || r.would_change === undefined)
            ? (r.ok || 0) : r.would_change;
          okCell = "<td class='mono' title='dry run — nothing was changed'>" +
            wc + " would change</td>";
        } else {
          okCell = "<td class='mono " + (bad ? "l-bad" : "l-ok") + "'>" + (r.ok || 0) + " ok</td>";
        }
        return "<tr><td class='mono'>" + G.esc(G.formatDate ? G.formatDate(r.finished_at) : r.finished_at) + "</td>" +
          okCell +
          "<td class='mono'>" + (r.partial || 0) + "</td>" +
          "<td class='mono " + (r.failed ? "l-bad" : "") + "'>" + (r.failed || 0) + "</td>" +
          "<td class='mono muted'>" + (r.repos || 0) + "</td>" +
          "<td class='mono muted'>" + (r.elapsed_sec === null || r.elapsed_sec === undefined ? "?" : r.elapsed_sec) + "s</td>" +
          "<td><button class='btn sm ghost btn-run-report' data-idx='" + i + "'>Report</button></td></tr>" +
          (fails ? "<tr><td colspan='7' style='padding-top:0'>" + fails + "</td></tr>" : "");
      }).join("");
      host.innerHTML = sparkHtml +
        '<div style="overflow-x:auto"><table class="mt" aria-label="Recent sync history"><thead><tr><th>Finished</th>' +
        "<th>OK</th><th>Partial</th><th>Failed</th><th>Repos</th><th>Time</th><th></th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table></div>";

      host.querySelectorAll(".btn-run-report").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var idx = parseInt(btn.getAttribute("data-idx"), 10);
          var r = recs[idx];
          if (!r) return;
          var bad = (r.failed || 0) + (r.partial || 0);
          var status = (r.skipped && !r.repos && !bad && !r.dry_run)
            ? "SKIPPED (no route was due)" : (r.dry_run ? "DRY RUN" :
              (bad ? (r.failed ? "FAILED" : "PARTIAL") : "SUCCESS"));
          var detail = "Status: " + status + "\n" +
            "Finished: " + r.finished_at + "\n" +
            (r.dry_run
              ? "Would change: " + ((r.would_change === null || r.would_change === undefined)
                  ? (r.ok || 0) : r.would_change) + " of " + (r.repos || 0) + " repos (nothing was changed)\n"
              : "Results: " + (r.ok || 0) + " OK, " + (r.partial || 0) + " partial, " + (r.failed || 0) + " failed of " + (r.repos || 0) + " repos\n") +
            "Duration: " + (r.elapsed_sec === null || r.elapsed_sec === undefined ? "?" : r.elapsed_sec) + "s\n\n";
          if (r.failures && r.failures.length) {
            detail += "Failures (" + r.failures.length + "):\n";
            r.failures.forEach(function (f) {
              detail += "• " + f.route + " / " + f.repo + ": " + f.error + "\n";
            });
          } else if (r.dry_run) {
            detail += "No sync errors were recorded for this preview.";
          } else {
            detail += "All repositories synced successfully with zero errors.";
          }
          G.modalConfirm({
            title: "Run Execution Report",
            message: detail,
            confirmText: "Close",
            cancelText: "Dismiss"
          });
        });
      });
    }).catch(function (e) {
      document.getElementById("h-host").innerHTML = G.errorBox(e);
    });
  }

  G.mount("history", render);
})();
