/* Theme bootstrap.
   Loaded in <head> *before* the stylesheet, and synchronous on purpose: the
   palette has to be right before the first paint, otherwise a light-theme
   user sees a dark flash on every page load.

   Stored values: "auto" (default, follow the OS), "light", "dark".
   The attribute goes on <html>, which is what style.css keys off:
     [data-theme="light"]  explicit light
     [data-theme="dark"]   explicit dark
     (absent)              auto -> prefers-color-scheme decides */
(function () {
  "use strict";
  var KEY = "gitsync-theme";
  var CHROME = { dark: "#0d1117", light: "#f6f8fa" };

  function read() {
    try {
      var v = localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : "auto";
    } catch (e) {
      return "auto";
    }
  }

  function osIsLight() {
    try {
      return !!(window.matchMedia &&
        window.matchMedia("(prefers-color-scheme: light)").matches);
    } catch (e) {
      return false;
    }
  }

  function apply(mode, persist) {
    var root = document.documentElement;
    if (mode === "light" || mode === "dark") {
      root.setAttribute("data-theme", mode);
    } else {
      mode = "auto";
      root.removeAttribute("data-theme");
    }
    var effective = mode === "auto" ? (osIsLight() ? "light" : "dark") : mode;
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute("content", CHROME[effective]);
    if (persist) {
      try { localStorage.setItem(KEY, mode); } catch (e) {}
    }
    window.GITSYNC_THEME = { mode: mode, effective: effective };
    return window.GITSYNC_THEME;
  }

  apply(read(), false);

  // follow the OS while the choice is "auto"
  try {
    var mq = window.matchMedia("(prefers-color-scheme: light)");
    var onChange = function () { if (read() === "auto") apply("auto", false); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  } catch (e) {}

  window.gitsyncTheme = {
    key: KEY,
    get: read,
    apply: apply,
    // Auto -> Light -> Dark -> Auto, used by the top-bar button
    next: function (mode) {
      return mode === "auto" ? "light" : mode === "light" ? "dark" : "auto";
    }
  };
})();
