/* The rejection and range panel.

   Lifted out of app.js, which had grown past two thousand lines in one
   closure. The body below is unchanged: everything it used to reach for
   in that closure now arrives in ctx, and `current` is still a plain
   local, updated by render(), so no line inside had to be rewritten.

   Registers itself on window.FaradaemPanels, which app.js walks once. */
(function (window, document) {
  "use strict";

  window.FaradaemPanels = window.FaradaemPanels || [];

  window.FaradaemPanels.push(function (ctx) {
    var id = ctx.id;
    var show = ctx.show;
    var clear = ctx.clear;
    var el = ctx.el;
    var values = ctx.values;
    var validate = ctx.validate;
    var current = null;

    /* ---- rejection and range ------------------------------------------------ */

    var sheetPanel = id("sheet");
    var sheetFigure = id("sheet-panel");
    var sheetRun = id("sheet-run");
    var sheetProgress = id("sheet-progress");
    var sheetState = id("sheet-state");
    var sheetMetrics = id("sheet-metrics");
    var sheetError = id("sheet-error");
    var sheetCaption = id("sheet-caption");

    var lastSheet = null;

    function renderSheetPanel() {
      lastSheet = null;
      sheetRun.disabled = false;
      show(sheetProgress, false);
      show(sheetError, false);
      clear(sheetMetrics);
      show(sheetFigure, false);
    }

    function renderSheetResult(result) {
      clear(sheetMetrics);
      (current.datasheet.readout || []).forEach(function (spec) {
        var raw = result[spec.key];
        sheetMetrics.appendChild(el("span", "goal-label", spec.label));
        sheetMetrics.appendChild(el("span", "goal-value",
          typeof raw === "number" && isFinite(raw)
            ? window.formatEngineering(raw, spec.unit || "")
            : "\u2014"));
      });

      // The range is two numbers, and which end it ran out at is the useful
      // half of the answer.
      sheetMetrics.appendChild(el("span", "goal-label", "follows from"));
      sheetMetrics.appendChild(el("span", "goal-value",
        result.input_low.toFixed(3) + " V to " + result.input_high.toFixed(3)
        + " V, on a " + result.supply.toFixed(2) + " V supply"));

      sheetCaption.textContent =
        (current.datasheet && current.datasheet.caption) || "";
      show(sheetFigure, true);
      window.drawTransfer(id("sheet-plot"), result);
    }

    function runSheet() {
      if (!current.datasheet || !validate()) {
        return;
      }
      show(sheetError, false);
      clear(sheetMetrics);
      show(sheetFigure, false);
      sheetRun.disabled = true;
      sheetState.textContent = "Running four amplifiers, about half a minute";
      show(sheetProgress, true);

      fetch("/api/datasheet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id, params: values() })
      })
        .then(function (response) {
          return response.json().then(function (payload) {
            if (!response.ok) {
              throw new Error(payload && payload.error
                ? payload.error : "The server refused the request.");
            }
            lastSheet = payload;
            sheetState.textContent = "Measured";
            sheetRun.disabled = false;
            renderSheetResult(payload);
          });
        })
        .catch(function (error) {
          sheetRun.disabled = false;
          show(sheetProgress, false);
          sheetError.textContent = String(error.message || error);
          show(sheetError, true);
        });
    }

    sheetRun.addEventListener("click", runSheet);

    return {
      key: "'sheet'".replace(/'/g, ""),
      render: function (circuit) {
        current = circuit;
        renderSheetPanel();
      },
      refit: function () {
        if (lastSheet && !sheetFigure.classList.contains("hidden")) {
          var last = lastSheet;
          window.drawTransfer(id("sheet-plot"), last);
        }
      },
      reveal: function () {
        if (lastSheet) {
          var last = lastSheet;
          window.drawTransfer(id("sheet-plot"), last);
        }
      }
    };
  });
})(window, document);
