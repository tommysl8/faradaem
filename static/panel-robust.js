/* The robustness panel.

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
    var tickStart = ctx.tickStart || function () {};
    var tickStop = ctx.tickStop || function () {};
    var markTab = ctx.markTab || function () {};
    var current = null;

    /* ---- robustness --------------------------------------------------------- */

    var robustPanel = id("robust");
    var robustPvt = id("robust-pvt");
    var robustMc = id("robust-mc");
    var robustStop = id("robust-stop");
    var robustProgress = id("robust-progress");
    var robustState = id("robust-state");
    var robustCount = id("robust-count");
    var robustTable = id("robust-table");
    var robustError = id("robust-error");

    var robustJob = null;
    var robustTimer = null;

    function robustIdle() {
      if (robustTimer) {
        clearTimeout(robustTimer);
        robustTimer = null;
      }
      robustJob = null;
      robustPvt.disabled = false;
      robustMc.disabled = false;
      show(robustProgress, false);
      show(robustError, false);
      clear(robustTable);
    }

    function renderRobustPanel() {
      robustIdle();
    }

    function robustValue(value) {
      return typeof value === "number" && isFinite(value)
        ? window.formatEngineering(value, "")
        : "\u2014";
    }

    function renderRobustTable(snapshot) {
      clear(robustTable);
      var keys = snapshot.keys && snapshot.keys.length
        ? snapshot.keys
        : (snapshot.rows[0] && snapshot.rows[0].measured
           ? Object.keys(snapshot.rows[0].measured) : []);
      if (!snapshot.rows.length) {
        return;
      }

      var table = document.createElement("table");
      var head = el("tr");
      head.appendChild(el("th", null, snapshot.mode === "pvt" ? "condition" : "seed"));
      keys.forEach(function (key) { head.appendChild(el("th", null, key)); });
      table.appendChild(head);

      snapshot.rows.forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(el("td", null,
          snapshot.mode === "pvt" ? row.label : String(row.seed)));
        if (row.error) {
          var cell = el("td", "is-bad", row.error);
          cell.colSpan = keys.length;
          tr.appendChild(cell);
        } else {
          keys.forEach(function (key) {
            tr.appendChild(el("td", null, robustValue(row.measured[key])));
          });
        }
        table.appendChild(tr);
      });

      if (snapshot.summary) {
        if (snapshot.mode === "pvt") {
          var tr = el("tr", "is-summary");
          tr.appendChild(el("td", null, "worst case"));
          keys.forEach(function (key) {
            var item = snapshot.summary[key];
            tr.appendChild(el("td", null,
              item ? robustValue(item.value) + " @ " + item.at : "\u2014"));
          });
          table.appendChild(tr);
        } else {
          [["mean", "mean"], ["sigma", "sigma"]].forEach(function (pair) {
            var line = el("tr", "is-summary");
            line.appendChild(el("td", null, pair[1]));
            keys.forEach(function (key) {
              var item = snapshot.summary[key];
              line.appendChild(el("td", null,
                item ? robustValue(item[pair[0]]) : "\u2014"));
            });
            table.appendChild(line);
          });
        }
      }
      robustTable.appendChild(table);
    }

    function pollRobust() {
      if (!robustJob) {
        return;
      }
      fetch("/api/robust/status?job=" + encodeURIComponent(robustJob))
        .then(function (response) { return response.json(); })
        .then(function (snapshot) {
          if (!robustJob) {
            return;
          }
          robustCount.textContent =
            snapshot.done + " / " + snapshot.total + " simulations";
          renderRobustTable(snapshot);
          if (snapshot.status === "running") {
            robustState.textContent = "Running";
            robustTimer = setTimeout(pollRobust, 1500);
            return;
          }
          robustState.textContent =
            snapshot.status === "done" ? "Finished"
            : snapshot.status === "stopped" ? "Stopped" : "Failed";
          markTab("robust", snapshot.status === "failed");
          if (snapshot.status === "done" && snapshot.mode === "pvt") {
            show(id("autopsy-run"), true);
          }
          robustPvt.disabled = false;
          robustMc.disabled = false;
          show(robustStop, false);
          if (snapshot.status === "failed") {
            robustError.textContent = snapshot.error ||
              "The run failed. Check the console running server.py.";
            show(robustError, true);
          }
        })
        .catch(function () {
          if (robustJob) {
            robustTimer = setTimeout(pollRobust, 3000);
          }
        });
    }

    function startRobust(mode) {
      if (!current.pdk || !validate()) {
        return;
      }
      show(robustError, false);
      clear(robustTable);
      robustPvt.disabled = true;
      robustMc.disabled = true;
      robustState.textContent = "Starting";
      robustCount.textContent = "";
      show(robustProgress, true);
      show(robustStop, true);

      fetch("/api/robust", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id, params: values(), mode: mode })
      })
        .then(function (response) {
          return response.json().then(function (payload) {
            if (!response.ok) {
              throw new Error(payload && payload.error
                ? payload.error : "The server refused the request.");
            }
            robustJob = payload.job;
            pollRobust();
          });
        })
        .catch(function (error) {
          robustIdle();
          robustError.textContent = String(error.message || error);
          show(robustError, true);
        });
    }

    robustPvt.addEventListener("click", function () { startRobust("pvt"); });
    robustMc.addEventListener("click", function () { startRobust("mc"); });
    robustStop.addEventListener("click", function () {
      if (robustJob) {
        fetch("/api/robust/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job: robustJob })
        }).catch(function () {});
      }
    });

    /* ---- the autopsy ---------------------------------------------------
       Per-device saturation headroom at every corner, read from the
       model card through ngspice. The table is the answer to "which
       transistor gave up": negative headroom in red, the closest call
       named even when everything holds. */

    var autopsyJob = null;

    function pollAutopsy() {
      fetch("/api/workbench/status?job=" + autopsyJob)
        .then(function (response) { return response.json(); })
        .then(function (snap) {
          if (snap.status === "running") {
            robustState.textContent = "Autopsy: " + snap.stage +
              " · " + Math.round(snap.seconds) + " s";
            setTimeout(pollAutopsy, 1200);
            return;
          }
          id("autopsy-run").disabled = false;
          if (snap.status === "failed") {
            robustError.textContent = snap.error || "The autopsy failed.";
            show(robustError, true);
            robustState.textContent = "Finished";
            return;
          }
          robustState.textContent = "Finished";
          renderAutopsy(snap.result);
        })
        .catch(function () { setTimeout(pollAutopsy, 2500); });
    }

    function renderAutopsy(found) {
      var out = id("autopsy-out");
      clear(out);

      var worst = found.tightest;
      var sentence;
      if (!worst) {
        sentence = "No operating point could be read at any corner.";
      } else if (worst.headroom < 0) {
        sentence = worst.device + " leaves saturation at " + worst.label +
          ": " + (worst.headroom * 1000).toFixed(0) + " mV of headroom.";
      } else {
        sentence = "Every device holds saturation at every corner. The " +
          "tightest is " + worst.device + " with " +
          (worst.headroom * 1000).toFixed(0) + " mV at " + worst.label + ".";
      }
      out.appendChild(el("p", "triage-line " +
        (worst && worst.headroom < 0 ? "is-missed" : "is-met"), sentence));

      var wrap = el("div", "sheet-table-wrap");
      var table = el("table", "sheet-table autopsy-table");
      var head = el("tr");
      head.appendChild(el("th", null, "headroom, mV"));
      found.device_order.forEach(function (name) {
        head.appendChild(el("th", null, name));
      });
      table.appendChild(head);

      found.rows.forEach(function (row) {
        var tr = el("tr");
        tr.appendChild(el("td", "sheet-at", row.label));
        if (!row.devices) {
          var cell = el("td", "field-error", row.error || "did not bias");
          cell.colSpan = found.device_order.length;
          tr.appendChild(cell);
          table.appendChild(tr);
          return;
        }
        found.device_order.forEach(function (name) {
          var slot = row.devices[name] || {};
          var value = slot.headroom;
          var td;
          if (typeof value === "number") {
            td = el("td", "num" + (value < 0 ? " is-negative" : ""),
                    (value * 1000).toFixed(0));
          } else {
            td = el("td", "sheet-at", "not exposed");
          }
          tr.appendChild(td);
        });
        table.appendChild(tr);
      });
      wrap.appendChild(table);
      out.appendChild(wrap);

      out.appendChild(el("p", "sheet-note",
        "vds and vdsat read from the model card through ngspice at each " +
        "corner's own operating point; headroom is their sign-aware " +
        "difference. " + found.sims + " simulations."));
      show(out, true);
    }

    id("autopsy-run").addEventListener("click", function () {
      show(robustError, false);
      id("autopsy-run").disabled = true;
      robustState.textContent = "Autopsy: starting";
      show(robustProgress, true);
      fetch("/api/workbench", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id, params: values(),
                               kind: "autopsy" })
      })
        .then(function (response) {
          return response.json().then(function (payload) {
            if (!response.ok) {
              throw new Error(payload.error || "Refused.");
            }
            autopsyJob = payload.job;
            pollAutopsy();
          });
        })
        .catch(function (error) {
          robustState.textContent = "Finished";
          id("autopsy-run").disabled = false;
          robustError.textContent = String(error.message || error);
          show(robustError, true);
        });
    });

    return {
      key: "'robust'".replace(/'/g, ""),
      render: function (circuit) {
        current = circuit;
        show(id("autopsy-run"), false);
        show(id("autopsy-out"), false);
        renderRobustPanel();
      }
    };
  });
})(window, document);
