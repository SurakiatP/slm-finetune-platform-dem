// Runs in <head> before the page paints. Keycloak is served from the same
// origin as the app (/auth), so the app's saved theme ("light" | "dark" |
// "system", written by next-themes under the key "theme") is readable here.
(function () {
  var stored = null;
  try {
    stored = window.localStorage.getItem("theme");
  } catch (e) {
    // Storage blocked: fall back to the OS setting.
  }
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function apply() {
    var dark = stored === "dark" || (stored !== "light" && !!media && media.matches);
    document.documentElement.classList.toggle("pf-v5-theme-dark", dark);
  }
  apply();
  if (media && stored !== "light" && stored !== "dark" && media.addEventListener) {
    media.addEventListener("change", apply);
  }
})();
