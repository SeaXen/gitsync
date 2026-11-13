/* Stars — read one account's stars and apply them to the others.
   Only repos that already exist on a target are starred; nothing is created. */
(function () {
  var G = window.Gitsync;
  var state = { source: "", platforms: [], stars: [], targets: {}, loaded: false };

  function render(main) {
    main.innerHTML =
      G.bar("Stars", "Copy an account's starred repos to your other accounts.") +
      '<section class="card">' +
        '<div class="inline mb">' +
          '<div class="u-min220">' +
            G.field("Read stars from", '<select id="s-src" name="star_source"></select>') + "</div>" +
          '<button class="btn u-end" id="s-reload">Re-read</button>' +
        "</div>" +
        '<div id="s-list">' + G.loading("reading stars…") + "</div>" +
      "</section>" +
      '<section class="card hide" id="s-copycard">' +
        '<div class="bar"><span class="route-title">Copy to</span>' +
        '<div class="actions">' +
          '<label class="check"><input type="checkbox" id="s-dry" name="dry_run"> Dry Run</label>' +
          '<button class="btn primary" id="s-copy">Copy Stars</button>' +
        "</div></div>" +
        '<div id="s-targets" class="mt"></div>' +
        '<div class="viewer mt hide" id="s-out" role="log" aria-live="polite"></div>' +
      "</section>";

    G.api("GET", "/api/platforms?probe=0").then(function (d) {
      state.platforms = d.platforms.filter(function (p) { return p.enabled; });
      document.getElementById("s-src").innerHTML = state.platforms.map(function (p) {
        return '<option value="' + G.esc(p.name) + '">' + G.esc(p.name) +
          " (" + G.esc(p.type) + ")</option>";
      }).join("");
      state.source = state.platforms.length ? state.platforms[0].name : "";
      renderTargets();
      loadStars();
      document.getElementById("s-src").addEventListener("change", function () {
        state.source = document.getElementById("s-src").value;
        state.stars = []; state.loaded = false; state.targets = {};
        renderTargets(); loadStars();
      });
    }).catch(function (e) {
      document.getElementById("s-list").innerHTML = G.errorBox(e);
    });

    document.getElementById("s-reload").addEventListener("click", loadStars);
    document.getElementById("s-copy").addEventListener("click", function () {
      G.busy(this, copy);
    });
  }

  function renderTargets() {
    var host = document.getElementById("s-targets");
    var others = state.platforms.filter(function (p) { return p.name !== state.source; });
    if (!others.length) {
      host.innerHTML = '<div class="empty">no other accounts</div>'; return;
    }
    host.innerHTML = others.map(function (p) {
      return '<label class="check"><input type="checkbox" class="s-t" name="star_targets" value="' +
        G.esc(p.name) + '"> ' + G.esc(p.name) + "</label>";
    }).join("");
  }

  function loadStars() {
    document.getElementById("s-list").innerHTML = G.loading("reading stars…");
    G.api("GET", "/api/stars?platform=" + encodeURIComponent(state.source))
      .then(function (d) {
        state.stars = d.stars || []; state.loaded = true; renderStars();
      }).catch(function (e) {
        document.getElementById("s-list").innerHTML = G.errorBox(e);
      });
  }

  function renderStars() {
    var host = document.getElementById("s-list");
    document.getElementById("s-copycard").classList.toggle("hide",
      !state.stars.length);
    if (!state.stars.length) {
      host.innerHTML = G.emptyState("No stars",
        "or this site does not expose them", "stars");
      return;
    }
    host.innerHTML = '<p class="label">' + state.stars.length + " starred</p>" +
      state.stars.map(function (s) {
        return '<div class="mono u-py2">' + G.esc(s.name) + "</div>";
      }).join("");
  }

  function copy() {
    var targets = Array.prototype.slice.call(
      document.querySelectorAll(".s-t:checked")).map(function (c) {
        return c.value;
      });
    if (!targets.length) return G.toast("pick at least one target", "warn");
    var out = document.getElementById("s-out");
    out.classList.remove("hide"); out.textContent = "copying…";
    return G.api("POST", "/api/stars/copy", {
      source: state.source, targets: targets,
      dry_run: document.getElementById("s-dry").checked
    }).then(function (d) {
      var s = d.summary;
      out.innerHTML = (d.log || []).map(function (l) {
        return '<div class="' + G.stateClass(l) + '">' + G.esc(l) + "</div>";
      }).join("") +
      '<div class="l-head mt">' + s.starred + " starred, " + s.already +
      " already, " + s.missing + " not on that site, " + s.failed + " failed</div>";
      G.toast("stars: " + s.starred + " new, " + s.missing + " not on target",
        s.failed ? "warn" : "ok");
    }).catch(function (e) { out.innerHTML = G.errorBox(e); });
  }

  G.mount("stars", render);
})();
