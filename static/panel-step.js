/* The step response panel.

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

    /* ---- step response ------------------------------------------------------ */

    var stepPanel = id("step");
    var stepFigure = id("step-panel");
    var stepRun = id("step-run");
    var stepProgress = id("step-progress");
    var stepState = id("step-state");
    var stepMetrics = id("step-metrics");
    var stepError = id("step-error");
    var stepCaption = id("step-caption");

    var lastStep = null;

    function renderStepPanel() {
      lastStep = null;
      stepRun.disabled = false;
      show(stepProgress, false);
      show(stepError, false);
      clear(stepMetrics);
      show(stepFigure, false);
    }

    function stepMetric(spec, result) {
      var raw = result[spec.key];
      if (raw === null || raw === undefined || !isFinite(raw)) {
        return "\u2014";
      }
      if (spec.format === "percent") {
        return (raw * 100).toFixed(2) + " %";
      }
      // Slew rate is volts per second in the API, because that is the SI of
      // it, and volts per microsecond on the page, because that is how every
      // datasheet in the field writes it.
      if (spec.format === "slew") {
        return (raw / 1e6).toFixed(3) + " V/µs";
      }
      return window.formatEngineering(raw, spec.unit || "");
    }

    function renderStepResult(result) {
      clear(stepMetrics);
      (current.step.readout || []).forEach(function (spec) {
        stepMetrics.appendChild(el("span", "goal-label", spec.label));
        stepMetrics.appendChild(el("span", "goal-value", stepMetric(spec, result)));
      });

      // Both edges, because the reported rate is the worse of the two and the
      // reader should be able to see which one that was.
      if (result.slew_rise && result.slew_fall) {
        stepMetrics.appendChild(el("span", "goal-label", "rising / falling"));
        stepMetrics.appendChild(el("span", "goal-value",
          (result.slew_rise / 1e6).toFixed(3) + "  /  "
          + (result.slew_fall / 1e6).toFixed(3) + " V/µs"));
      }

      stepCaption.textContent = (current.step && current.step.caption) || "";
      show(stepFigure, true);
      window.drawStep(id("step-plot"), result);
    }

    function runStep() {
      if (!current.step || !validate()) {
        return;
      }
      show(stepError, false);
      clear(stepMetrics);
      show(stepFigure, false);
      stepRun.disabled = true;
      stepState.textContent = "Running one transient, about twenty seconds";
      show(stepProgress, true);

      fetch("/api/step", {
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
            lastStep = payload;
            stepState.textContent = "Measured";
            stepRun.disabled = false;
            renderStepResult(payload);
          });
        })
        .catch(function (error) {
          stepRun.disabled = false;
          show(stepProgress, false);
          stepError.textContent = String(error.message || error);
          show(stepError, true);
        });
    }

    stepRun.addEventListener("click", runStep);

    return {
      key: "'step'".replace(/'/g, ""),
      render: function (circuit) {
        current = circuit;
        renderStepPanel();
      },
      refit: function () {
        if (lastStep && !stepFigure.classList.contains("hidden")) {
          var last = lastStep;
          window.drawStep(id("step-plot"), last);
        }
      },
      reveal: function () {
        if (lastStep) {
          var last = lastStep;
          window.drawStep(id("step-plot"), last);
        }
      }
    };
  });
})(window, document);
