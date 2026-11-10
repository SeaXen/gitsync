/* Log — full log viewer with tail size, filter and optional auto-refresh. */
(function () {
  var G = window.Gitsync;
  var timer = null;

  function render(main) {
    main.innerHTML =
      G.bar("Log", "The gitsync log, newest last.") +
      '<section class="card">' +
        '<div class="inline mb">' +
          '<div class="field" style="margin:0;min-width:140px"><label>Tail</label>' +
          '<select id="l-tail"><option>200</option><option selected>400</option>' +
          '<option>1000</option><option>3000</option></select></div>' +
          '<div class="field grow" style="margin:0"><label>Filter</label>' +
          '<input type="text" id="l-q" placeholder="substring or /regex-ish/…"></div>' +
          '<label class="check"><input type="checkbox" id="l-auto"> auto-refresh (3s)</label>' +
          '<button class="btn" id="l-refresh" style="align-self:flex-end">Refresh</button>' +
        "</div>" +
        '<div class="muted mb" id="l-meta"></div>' +
        '<div class="viewer" id="l-lines">' + G.loading() + "</div>" +
      "</section>";

    document.getElementById("l-refresh").addEventListener("click", load);
    document.getElementById("l-tail").addEventListener("change", load);
    var q = document.getElementById("l-q");
    var deb;
    q.addEventListener("input", function () { clearTimeout(deb); deb = setTimeout(load, 300); });
    document.getElementById("l-auto").addEventListener("change", function (e) {
      clearInterval(timer);
      if (e.target.checked) timer = setInterval(load, 3000);
    });
    load();
  }

  function load() {
    var tail = document.getElementById("l-tail").value;
    var q = document.getElementById("l-q").value.trim();
    G.api("GET", "/api/log?tail=" + tail + "&q=" + encodeURIComponent(q))
      .then(function (d) {
        var host = document.getElementById("l-lines");
        document.getElementById("l-meta").textContent =
          d.shown + " shown" + (q ? " (filtered)" : "") + " of " + d.total + " lines";
        if (!d.lines.length) { host.innerHTML = '<div class="empty">no matching lines</div>'; return; }
        host.innerHTML = d.lines.map(function (l) {
          return '<div class="' + G.stateClass(l) + '">' + G.esc(l) + "</div>";
        }).join("");
        host.scrollTop = host.scrollHeight;
      }).catch(function (e) { document.getElementById("l-lines").innerHTML = G.errorBox(e); });
  }

  G.mount("log", render);
})();
