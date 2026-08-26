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
    var robustError = id("robust-error");

    /* The two suites answer different questions and share nothing but
       the circuit, so each gets its own job, its own progress block and
       its own stop. Running both at once is the whole point: the corner
       sweep and the mismatch sample can grind in parallel. */
    var slots = {
      pvt: { button: robustPvt, job: null, timer: null,
             progress: id("robust-progress-pvt"),
             state: id("robust-state-pvt"),
             count: id("robust-count-pvt"),
             stop: id("robust-stop-pvt"),
             table: id("robust-table-pvt") },
      mc: { button: robustMc, job: null, timer: null,
            progress: id("robust-progress-mc"),
            state: id("robust-state-mc"),
            count: id("robust-count-mc"),
            stop: id("robust-stop-mc"),
            table: id("robust-table-mc") }
    };

    function slotIdle(slot) {
      if (slot.timer) {
        clearTimeout(slot.timer);
        slot.timer = null;
      }
      slot.job = null;
      slot.button.disabled = false;
      show(slot.progress, false);
      clear(slot.table);
    }

    function renderRobustPanel() {
      slotIdle(slots.pvt);
      slotIdle(slots.mc);
      show(robustError, false);
    }

    function robustValue(value) {
      return typeof value === "number" && isFinite(value)
        ? window.formatEngineering(value, "")
        : "\u2014";
    }

    function renderRobustTable(snapshot, host) {
      clear(host);
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
      host.appendChild(table);
    }

    function pollSlot(mode) {
      var slot = slots[mode];
      if (!slot.job) {
        return;
      }
      fetch("/api/robust/status?job=" + encodeURIComponent(slot.job))
        .then(function (response) { return response.json(); })
        .then(function (snapshot) {
          if (!slot.job) {
            return;
          }
          slot.count.textContent =
            snapshot.done + " / " + snapshot.total + " simulations";
          renderRobustTable(snapshot, slot.table);
          if (snapshot.status === "running") {
            slot.state.textContent = "Running";
            slot.timer = setTimeout(function () { pollSlot(mode); }, 1500);
            return;
          }
          slot.state.textContent =
            snapshot.status === "done" ? "Finished"
            : snapshot.status === "stopped" ? "Stopped" : "Failed";
          markTab("robust", snapshot.status === "failed");
          if (snapshot.status === "done" && snapshot.mode === "pvt") {
            show(id("autopsy-run"), true);
          }
          slot.job = null;
          slot.button.disabled = false;
          show(slot.stop, false);
          if (snapshot.status === "failed") {
            robustError.textContent = snapshot.error ||
              "The run failed. Check the console running server.py.";
            show(robustError, true);
          }
        })
        .catch(function () {
          if (slot.job) {
            slot.timer = setTimeout(function () { pollSlot(mode); }, 3000);
          }
        });
    }

    function startRobust(mode) {
      if (!current.pdk || !validate()) {
        return;
      }
      var slot = slots[mode];
      show(robustError, false);
      clear(slot.table);
      slot.button.disabled = true;
      slot.state.textContent = "Starting";
      slot.count.textContent = "";
      show(slot.progress, true);
      show(slot.stop, true);

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
            slot.job = payload.job;
            pollSlot(mode);
          });
        })
        .catch(function (error) {
          slotIdle(slot);
          robustError.textContent = String(error.message || error);
          show(robustError, true);
        });
    }

    robustPvt.addEventListener("click", function () { startRobust("pvt"); });
    robustMc.addEventListener("click", function () { startRobust("mc"); });
    slots.pvt.stop.addEventListener("click", function () {
      if (slots.pvt.job) {
        fetch("/api/robust/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job: slots.pvt.job })
        }).catch(function () {});
      }
    });
    slots.mc.stop.addEventListener("click", function () {
      if (slots.mc.job) {
        fetch("/api/robust/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job: slots.mc.job })
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
            slots.pvt.state.textContent = "Autopsy: " + snap.stage +
              " · " + Math.round(snap.seconds) + " s";
            setTimeout(pollAutopsy, 1200);
            return;
          }
          id("autopsy-run").disabled = false;
          if (snap.status === "failed") {
            robustError.textContent = snap.error || "The autopsy failed.";
            show(robustError, true);
            slots.pvt.state.textContent = "Finished";
            return;
          }
          slots.pvt.state.textContent = "Finished";
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
      slots.pvt.state.textContent = "Autopsy: starting";
      show(slots.pvt.progress, true);
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
          slots.pvt.state.textContent = "Finished";
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
