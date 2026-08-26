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

    return {
      key: "'robust'".replace(/'/g, ""),
      render: function (circuit) {
        current = circuit;
        renderRobustPanel();
      }
    };
  });
})(window, document);
