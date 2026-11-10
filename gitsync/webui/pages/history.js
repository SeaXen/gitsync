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
      var rows = recs.map(function (r) {
        var bad = (r.failed || 0) + (r.partial || 0);
        var fails = (r.failures || []).map(function (f) {
          return '<div class="mono l-bad" style="padding-left:14px">! ' +
            G.esc(f.route) + "/" + G.esc(f.repo) + ": " + G.esc(f.error) + "</div>";
        }).join("");
        return "<tr><td class='mono'>" + G.esc(G.formatDate ? G.formatDate(r.finished_at) : r.finished_at) + "</td>" +
          "<td class='mono " + (bad ? "l-bad" : "l-ok") + "'>" + (r.ok || 0) + " ok</td>" +
          "<td class='mono'>" + (r.partial || 0) + "</td>" +
          "<td class='mono " + (r.failed ? "l-bad" : "") + "'>" + (r.failed || 0) + "</td>" +
          "<td class='mono muted'>" + (r.repos || 0) + "</td>" +
          "<td class='mono muted'>" + (r.elapsed_sec || "?") + "s</td></tr>" +
          (fails ? "<tr><td colspan='6' style='padding-top:0'>" + fails + "</td></tr>" : "");
      }).join("");
      host.innerHTML = sparkHtml +
        '<div style="overflow-x:auto"><table class="mt" aria-label="Recent sync history"><thead><tr><th>Finished</th>' +
        "<th>OK</th><th>Partial</th><th>Failed</th><th>Repos</th><th>Time</th>" +
        "</tr></thead><tbody>" + rows + "</tbody></table></div>";
    }).catch(function (e) {
      document.getElementById("h-host").innerHTML = G.errorBox(e);
    });
  }

  G.mount("history", render);
})();
