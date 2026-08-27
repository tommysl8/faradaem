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
      if (slot.job) {
        // Walking away from a live suite stops it server-side too;
        // otherwise it keeps simulating a sizing nobody is looking at.
        fetch("/api/robust/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job: slot.job })
        }).catch(function () {});
      }
      slot.job = null;
      slot.lastFailed = false;
      slot.button.disabled = false;
      slot.stop.disabled = false;
      slot.stop.textContent = "Stop";
      show(slot.progress, false);
      clear(slot.table);
    }

    function renderRobustPanel() {
      slotIdle(slots.pvt);
      slotIdle(slots.mc);
      show(robustError, false);
    }

    /* Headers and cells speak the circuit's own readout language:
       "phase margin" with degrees, never phase_margin with a bare
       number. */
    function specFor(key) {
      var readout = current && current.readout;
      if (!readout) { return null; }
      var all = [readout.headline].concat(readout.stats || []);
      for (var i = 0; i < all.length; i++) {
        if (all[i] && all[i].key === key) { return all[i]; }
      }
      return null;
    }

    function keyLabel(key) {
      var spec = specFor(key);
      return (spec && spec.label) || key.replace(/_/g, " ");
    }

    function robustValue(value, key) {
      if (typeof value !== "number" || !isFinite(value)) {
        return "\u2014";
      }
      var spec = specFor(key) || {};
      if (spec.format === "db") { return value.toFixed(2) + " dB"; }
      if (spec.format === "deg") { return value.toFixed(1) + "\u00b0"; }
      if (spec.format === "plain") { return value.toPrecision(4); }
      return window.formatEngineering(value, spec.unit || "");
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
      keys.forEach(function (key) {
        head.appendChild(el("th", null, keyLabel(key)));
      });
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
            tr.appendChild(el("td", null, robustValue(row.measured[key], key)));
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
              item ? robustValue(item.value, key) + " @ " + item.at
                   : "\u2014"));
          });
          table.appendChild(tr);
        } else {
          [["mean", "mean"], ["sigma", "sigma"]].forEach(function (pair) {
            var line = el("tr", "is-summary");
            line.appendChild(el("td", null, pair[1]));
            keys.forEach(function (key) {
              var item = snapshot.summary[key];
              line.appendChild(el("td", null,
                item ? robustValue(item[pair[0]], key) : "\u2014"));
            });
            table.appendChild(line);
          });
        }
      }
      host.appendChild(table);
    }

    var SUITE_NAMES = { pvt: "The corner suite", mc: "Monte Carlo" };

    function pollSlot(mode) {
      var slot = slots[mode];
      if (!slot.job) {
        return;
      }
      fetch("/api/robust/status?job=" + encodeURIComponent(slot.job))
        .then(function (response) {
          return response.json().then(function (snapshot) {
            return { ok: response.ok, snapshot: snapshot };
          });
        })
        .then(function (got) {
          if (!slot.job) {
            return;
          }
          if (!got.ok) {
            // The job is gone: the server restarted or evicted it.
            slot.job = null;
            slotIdle(slot);
            robustError.textContent = SUITE_NAMES[mode] + ": "
              + ((got.snapshot && got.snapshot.error)
                 || "the job is gone. Start it again.");
            show(robustError, true);
            return;
          }
          var snapshot = got.snapshot;
          var countText =
            snapshot.done + " / " + snapshot.total + " simulations";
          // The suite's own measured pace makes the estimate honest.
          if (snapshot.status === "running" && slot.started
              && snapshot.done >= 2) {
            var pace = (Date.now() - slot.started) / snapshot.done;
            var left = Math.round(
              pace * (snapshot.total - snapshot.done) / 1000);
            if (left >= 2) {
              countText += " · about " + (left >= 90
                ? Math.round(left / 60) + " min" : left + " s") + " left";
            }
          }
          slot.count.textContent = countText;
          renderRobustTable(snapshot, slot.table);
          if (snapshot.status === "running") {
            slot.state.textContent = "Running";
            slot.timer = setTimeout(function () { pollSlot(mode); }, 1500);
            return;
          }
          var outcome =
            snapshot.status === "done" ? "Finished"
            : snapshot.status === "stopped" ? "Stopped" : "Failed";
          slot.state.textContent = outcome
            + (slot.editedDuringRun
               ? " · at the sizing before your edits" : "");
          if (window.FaradaemNotify) {
            window.FaradaemNotify.done(SUITE_NAMES[mode] + " "
              + outcome.toLowerCase() + ".");
          }
          slot.lastFailed = snapshot.status === "failed";
          // The tab dot answers for both suites: red while either's
          // latest outcome failed, never green because the other one
          // finished later.
          markTab("robust",
            Boolean(slots.pvt.lastFailed || slots.mc.lastFailed));
          if (snapshot.status === "done" && snapshot.mode === "pvt") {
            show(id("autopsy-run"), true);
          }
          slot.job = null;
          slot.button.disabled = false;
          slot.stop.disabled = false;
          slot.stop.textContent = "Stop";
          show(slot.stop, false);
          if (snapshot.status === "failed") {
            robustError.textContent = SUITE_NAMES[mode] + ": "
              + (snapshot.error
                 || "the run failed. Check the console running server.py.");
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
      slot.started = Date.now();
      slot.editedDuringRun = false;
      show(slot.progress, true);
      show(slot.stop, true);
      if (window.FaradaemNotify) {
        window.FaradaemNotify.ask();
      }

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

    /* A stop says it is stopping, and a stop that could not be sent says
       that too, instead of looking pressed and doing nothing. The pvt
       slot's chip also serves the autopsy, which runs in its progress
       block. */
    function requestStop(slot, url, job) {
      slot.stop.disabled = true;
      slot.stop.textContent = "Stopping";
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job: job })
      }).catch(function () {
        slot.stop.disabled = false;
        slot.stop.textContent = "Stop";
        robustError.textContent =
          "The stop request did not reach the server. Try again.";
        show(robustError, true);
      });
    }

    slots.pvt.stop.addEventListener("click", function () {
      if (slots.pvt.job) {
        requestStop(slots.pvt, "/api/robust/stop", slots.pvt.job);
      } else if (autopsyJob) {
        requestStop(slots.pvt, "/api/workbench/stop", autopsyJob);
      }
    });
    slots.mc.stop.addEventListener("click", function () {
      if (slots.mc.job) {
        requestStop(slots.mc, "/api/robust/stop", slots.mc.job);
      }
    });

    /* ---- the autopsy ---------------------------------------------------
       Per-device saturation headroom at every corner, read from the
       model card through ngspice. The table is the answer to "which
       transistor gave up": negative headroom in red, the closest call
       named even when everything holds. */

    var autopsyJob = null;
    var autopsyTimer = null;
    var autopsyEdited = false;

    function autopsyDone() {
      autopsyJob = null;
      id("autopsy-run").disabled = false;
      slots.pvt.stop.disabled = false;
      slots.pvt.stop.textContent = "Stop";
      show(slots.pvt.stop, false);
    }

    function pollAutopsy() {
      var job = autopsyJob;
      var misses = 0;

      function tick() {
        if (job !== autopsyJob) {
          return;
        }
        fetch("/api/workbench/status?job=" + job)
          .then(function (response) {
            return response.json().then(function (snap) {
              return { ok: response.ok, snap: snap };
            });
          })
          .then(function (got) {
            if (job !== autopsyJob) {
              return;
            }
            if (!got.ok) {
              autopsyDone();
              slots.pvt.state.textContent = "Autopsy failed";
              robustError.textContent = (got.snap && got.snap.error)
                || "The autopsy's job is gone. Start it again.";
              show(robustError, true);
              return;
            }
            var snap = got.snap;
            misses = 0;
            if (snap.status === "running") {
              slots.pvt.state.textContent = "Autopsy: " + snap.stage +
                " · " + Math.round(snap.seconds) + " s";
              autopsyTimer = setTimeout(tick, 1200);
              return;
            }
            autopsyDone();
            if (snap.status === "failed") {
              robustError.textContent = snap.error || "The autopsy failed.";
              show(robustError, true);
              slots.pvt.state.textContent = "Autopsy failed";
              return;
            }
            if (snap.status === "stopped") {
              slots.pvt.state.textContent = "Autopsy stopped";
              return;
            }
            slots.pvt.state.textContent = "Finished"
              + (autopsyEdited ? " · at the sizing before your edits" : "");
            renderAutopsy(snap.result);
          })
          .catch(function () {
            if (job !== autopsyJob) {
              return;
            }
            misses += 1;
            if (misses >= 4) {
              autopsyDone();
              slots.pvt.state.textContent = "Autopsy failed";
              robustError.textContent = "Could not reach the Faradaem server.";
              show(robustError, true);
              return;
            }
            autopsyTimer = setTimeout(tick, 2500);
          });
      }

      tick();
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
      autopsyEdited = false;
      id("autopsy-run").disabled = true;
      slots.pvt.state.textContent = "Autopsy: starting";
      show(slots.pvt.progress, true);
      show(slots.pvt.stop, true);
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
          autopsyDone();
          slots.pvt.state.textContent = "Autopsy failed";
          robustError.textContent = String(error.message || error);
          show(robustError, true);
        });
    });

    function resetPanel() {
      if (autopsyJob) {
        // Leaving the circuit stops its autopsy server-side too.
        fetch("/api/workbench/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job: autopsyJob })
        }).catch(function () {});
      }
      autopsyJob = null;
      if (autopsyTimer) {
        clearTimeout(autopsyTimer);
        autopsyTimer = null;
      }
      id("autopsy-run").disabled = false;
      show(id("autopsy-run"), false);
      show(id("autopsy-out"), false);
      renderRobustPanel();
    }

    /* An edit outdates what is finished, but must not kill a suite ten
       minutes into its run over one keystroke. Finished tables clear;
       running suites finish, and their state line then says which
       sizing they measured. */
    function softStale() {
      ["pvt", "mc"].forEach(function (mode) {
        var slot = slots[mode];
        if (slot.job) {
          slot.editedDuringRun = true;
        } else {
          clear(slot.table);
          show(slot.progress, false);
        }
      });
      if (autopsyJob) {
        autopsyEdited = true;
      } else {
        show(id("autopsy-out"), false);
        show(id("autopsy-run"), false);
      }
    }

    return {
      key: "'robust'".replace(/'/g, ""),
      render: function (circuit) {
        current = circuit;
        resetPanel();
      },
      onValuesEdited: softStale
    };
  });
})(window, document);
