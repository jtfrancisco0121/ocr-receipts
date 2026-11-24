// Light/dark switch shared by the landing page and the app (SPEC.md section 12).
// Loaded in <head> without defer so a saved choice is applied before the first paint.
// Storage can throw (Safari private mode, blocked cookies): every access is inside try/catch.
(function () {
  "use strict";

  var root = document.documentElement;

  function saved() {
    try {
      var value = window.localStorage.getItem("resibo-theme");
      return value === "light" || value === "dark" ? value : null;
    } catch (err) {
      return null;
    }
  }

  function save(value) {
    try {
      window.localStorage.setItem("resibo-theme", value);
    } catch (err) {
      // the choice then lasts for this page view only
    }
  }

  function osPrefersDark() {
    try {
      return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    } catch (err) {
      return false;
    }
  }

  function isDark() {
    var attribute = root.dataset.theme;
    if (attribute === "dark") return true;
    if (attribute === "light") return false;
    return osPrefersDark();
  }

  var choice = saved();
  if (choice) root.dataset.theme = choice;

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("theme-toggle");
    if (!button) return;
    function reflect() {
      button.setAttribute("aria-pressed", isDark() ? "true" : "false");
    }
    button.addEventListener("click", function () {
      var next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      save(next);
      reflect();
    });
    reflect();
  });
})();
