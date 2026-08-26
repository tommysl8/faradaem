/* The the floorplan panel.

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

    /* ---- the floorplan ------------------------------------------------------ */

    var layoutPanel = id("layout");
    var layoutFigure = id("layout-figure");
    var layoutRun = id("layout-run");
    var layoutProgress = id("layout-progress");
    var layoutState = id("layout-state");
    var layoutMetrics = id("layout-metrics");
    var layoutError = id("layout-error");
    var layoutCaption = id("layout-caption");

    var lastLayout = null;

    function renderLayoutPanel() {
      lastLayout = null;
      layoutRun.disabled = false;
      show(layoutProgress, false);
      show(layoutError, false);
      clear(layoutMetrics);
      show(layoutFigure, false);
      show(id("layout-verify"), false);
      show(id("layout-gds"), false);
      show(id("layout-signoff"), false);
    }

    function pair(label, value) {
      layoutMetrics.appendChild(el("span", "goal-label", label));
      layoutMetrics.appendChild(el("span", "goal-value", value));
    }

    function renderLayoutResult(result) {
      clear(layoutMetrics);
      var plan = result.floorplan;

      pair("area", plan.area_um2.toFixed(1) + " \u00b5m\u00b2");
      pair("bounding box", plan.width_um.toFixed(2) + " \u00d7 "
        + plan.height_um.toFixed(2) + " \u00b5m");
      pair("device active area", plan.active_area_um2.toFixed(1) + " \u00b5m\u00b2");
      pair("interconnect", window.formatEngineering(result.total_parasitic_f, "F"));
      if (result.resistance) {
        var worst = null;
        Object.keys(result.resistance).forEach(function (net) {
          var entry = result.resistance[net];
          if (!worst || entry.worst_ohms > worst.ohms) {
            worst = { net: net, ohms: entry.worst_ohms };
          }
        });
        if (worst) {
          pair("longest wire", worst.ohms.toFixed(0) + " Ω on "
            + worst.net + ", measured by KLayout");
        }
      }
      if (result.gds_bytes) {
        pair("geometry", result.gds_bytes + " bytes of GDS");
      }
      renderVerification(result);

      // What the interconnect actually cost, measured rather than asserted.
      result.comparison.forEach(function (item) {
        if (Math.abs(item.change) < 1e-12) {
          return;
        }
        pair(item.key + " after wiring",
          window.formatEngineering(item.after, "")
          + "  (" + (item.change > 0 ? "+" : "")
          + window.formatEngineering(item.change, "") + ")");
      });

      layoutCaption.textContent =
        (current.floorplan && current.floorplan.caption) || "";
      show(layoutFigure, true);
      show(id("layout-gds"), Boolean(result.gds_base64));
      show(id("layout-signoff"), true);
      window.drawFloorplan(id("layout-plot"), result);
    }

    // Two questions, and they are different questions. A rule check asks
    // whether the geometry could be made; a layout-versus-schematic asks
    // whether it is the circuit that was simulated. Geometry can pass the
    // first and fail the second, which is the failure nobody sees.
    function verdict(list, passed, heading, detail, problems) {
      var item = el("li", "verify-item " + (passed ? "is-pass" : "is-fail"));
      item.appendChild(el("span", "verify-mark", passed ? "PASS" : "FAIL"));

      var text = el("span", "verify-text", heading);
      var what = el("span", "verify-what", detail);
      text.appendChild(what);
      item.appendChild(text);
      list.appendChild(item);

      if (!passed && problems && problems.length) {
        var reasons = el("ul", "verify-problems");
        problems.slice(0, 4).forEach(function (line) {
          reasons.appendChild(el("li", "", line));
        });
        if (problems.length > 4) {
          reasons.appendChild(el("li", "",
            "and " + (problems.length - 4) + " more"));
        }
        item.appendChild(reasons);
      }
    }

    function renderVerification(result) {
      var block = id("layout-verify");
      var list = id("layout-verify-list");
      var note = id("layout-verify-note");
      clear(list);

      if (!result.drc && !result.lvs) {
        show(block, false);
        return;
      }

      if (result.klvs) {
        if (result.klvs.ran === false) {
          verdict(list, false,
            "Layout versus schematic, KLayout's engine",
            "The engine is not installed, so this did not run. The check "
            + "below is Faradaem's own and is not a substitute.",
            [result.klvs.why || ""]);
        } else {
          verdict(list, result.klvs.match,
            "Layout versus schematic, KLayout's engine",
            result.klvs.match
              ? "Devices recognised from the geometry, sizes and values "
                + "measured from it, matched against the circuit by "
                + "topology."
              : "The extracted netlist does not match the circuit.",
            result.klvs.log || []);
        }
      }

      if (result.drc) {
        verdict(list, result.drc.clean,
          "Design rules, fast check",
          result.drc.clean
            ? "Clean on " + result.drc.rules_checked.length
              + " rules read from the PDK. This is the inner loop, not the "
              + "answer: run the foundry's deck below."
            : result.drc.violations.length + " of "
              + result.drc.rules_checked.length + " rules broken.",
          (result.drc.violations || []).map(function (item) {
            return item.tag + ": " + item.what + " ("
              + item.measured_um.toFixed(3) + " against "
              + item.required_um.toFixed(3) + " \u00b5m)";
          }));
      }

      if (result.lvs) {
        verdict(list, result.lvs.match,
          "Layout versus schematic",
          result.lvs.match
            ? "All " + result.lvs.nets_drawn + " nets in the drawing are the "
              + "nets in the netlist that was simulated."
            : result.lvs.problems.length + " connections disagree with the "
              + "netlist.",
          (result.lvs.problems || []).map(function (item) {
            return item.what;
          }));

        // What stays outside the cell. A bias current and a load are
        // ports; an ideal voltage source standing in for an on-chip
        // reference is a cheat, and the verdict colours on which it is.
        var missing = result.lvs.undrawn || [];
        if (missing.length) {
          var names = missing.map(function (item) {
            return item.name + " (" + item.kind + ")";
          });
          var honest = missing.every(function (item) {
            return item.kind !== "voltage source"
              && item.kind !== "controlled source";
          });
          verdict(list, honest,
            "External to this cell",
            honest
              ? "The bias and the load connect from outside, as they would "
                + "on a real chip."
              : "Ideal sources stand in for references a finished design "
                + "would generate on chip.",
            names);
        }
      }

      note.textContent = "Still not checked: the device sizes against the "
        + "schematic, and a real parasitic extraction.";
      show(block, true);
    }

    function runLayout() {
      if (!current.floorplan || !validate()) {
        return;
      }
      show(layoutError, false);
      clear(layoutMetrics);
      show(layoutFigure, false);
      show(id("layout-verify"), false);
      layoutRun.disabled = true;
      layoutState.textContent = "Placing devices, then measuring twice";
      show(layoutProgress, true);

      fetch("/api/layout", {
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
            lastLayout = payload;
            layoutState.textContent = "Measured";
            layoutRun.disabled = false;
            renderLayoutResult(payload);
          });
        })
        .catch(function (error) {
          layoutRun.disabled = false;
          show(layoutProgress, false);
          layoutError.textContent = String(error.message || error);
          show(layoutError, true);
        });
    }

    layoutRun.addEventListener("click", runLayout);

    var layoutGds = id("layout-gds");

    /* The geometry, as the file every layout tool reads. Built in the page
       from the bytes the server computed, so nothing is written to disk
       unless the reader asks for it. */
    function downloadGds() {
      if (!lastLayout || !lastLayout.gds_base64) {
        return;
      }
      var binary = window.atob(lastLayout.gds_base64);
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      var url = URL.createObjectURL(new Blob([bytes],
        { type: "application/octet-stream" }));
      var link = document.createElement("a");
      link.href = url;
      link.download = current.id + "-floorplan.gds";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    }

    layoutGds.addEventListener("click", downloadGds);


    /* The foundry's own deck. A minute of work in another process, so it
       is asked for rather than run every time, and what comes back is the
       runset's answer verbatim: this page checks nothing itself. */
    function runSignoff() {
      var button = id("layout-signoff");
      button.disabled = true;
      layoutState.textContent = "Running the SKY130 runset, about a minute";
      show(layoutProgress, true);
      show(layoutError, false);

      fetch("/api/signoff", {
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
            button.disabled = false;
            layoutState.textContent = "Measured";

            var list = id("layout-verify-list");
            var broken = payload.violations || {};
            verdict(list, payload.clean,
              "Design rules, the foundry's deck",
              payload.clean
                ? "Clean under the SKY130 runset, over the "
                  + payload.sections.join(", ") + " sections."
                : payload.total + " violations across "
                  + Object.keys(broken).length + " rules.",
              Object.keys(broken).sort().map(function (rule) {
                return rule + ": " + broken[rule];
              }));
            show(id("layout-verify"), true);
          });
        })
        .catch(function (error) {
          button.disabled = false;
          show(layoutProgress, false);
          layoutError.textContent = String(error.message || error);
          show(layoutError, true);
        });
    }

    id("layout-signoff").addEventListener("click", runSignoff);

    return {
      key: "'layout'".replace(/'/g, ""),
      render: function (circuit) {
        current = circuit;
        renderLayoutPanel();
      },
      refit: function () {
        if (lastLayout && !layoutFigure.classList.contains("hidden")) {
          var last = lastLayout;
          window.drawFloorplan(id("layout-plot"), last);
        }
      },
      reveal: function () {
        if (lastLayout) {
          var last = lastLayout;
          window.drawFloorplan(id("layout-plot"), last);
        }
      }
    };
  });
})(window, document);
