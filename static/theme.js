/* The theme toggle. Dark is the default and the identity; light is the
   same instrument in a bright lab.

   The choice is stored in this browser only. The pre-paint snippet in
   each page's head applies the stored choice before the first paint, so
   a light-mode reader never sees a dark flash; this file only owns the
   button. */
(function (window, document) {
  "use strict";

  var KEY = "faradaem-theme";

  // Loaded synchronously in the head, before the stylesheet, so the
  // stored choice lands before the first paint and a light-mode reader
  // never sees a dark flash. The button below is wired later, when the
  // body exists.
  try {
    if (window.localStorage.getItem(KEY) === "light") {
      document.documentElement.setAttribute("data-theme", "light");
    }
  } catch (error) {
    // A browser that refuses storage simply stays dark.
  }

  function apply(theme) {
    if (theme === "light") {
      document.documentElement.setAttribute("data-theme", "light");
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
  }

  function label(button) {
    var light = document.documentElement.getAttribute("data-theme") === "light";
    button.textContent = light ? "Dark mode" : "Light mode";
  }

  function start() {
    var button = document.getElementById("theme-toggle");
    if (!button) {
      return;
    }
    label(button);
    button.addEventListener("click", function () {
      var light = document.documentElement.getAttribute("data-theme") === "light";
      var next = light ? "dark" : "light";
      apply(next);
      try {
        window.localStorage.setItem(KEY, next);
      } catch (error) {
        // A browser that refuses storage still gets the session's theme.
      }
      label(button);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})(window, document);
