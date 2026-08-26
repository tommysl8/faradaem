/* The datasheet panel: the document, its store, the compare, the pins.

   One tab, four uses of the same store. The characterization is the
   server's charact module run as a background job; everything rendered
   here is a number the server measured, formatted and never recomputed.

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
    var tickStart = ctx.tickStart || function () {};
    var tickStop = ctx.tickStop || function () {};
    var markTab = ctx.markTab || function () {};
    var current = null;

    var runButton = id("charact-run");
    var runLabel = id("charact-run-label");
    var stopButton = id("charact-stop");
    var packetButton = id("packet-run");
    var stateLine = id("charact-state");
    var errorLine = id("charact-error");
    var staleLine = id("charact-stale");
    var docEl = id("charact-doc");
    var storedEl = id("charact-stored");
    var pickA = id("compare-a");
    var pickB = id("compare-b");
    var compareWarn = id("compare-warn");
    var compareOut = id("compare-out");
    var pinStateEl = id("pin-state");
    var pinCheck = id("pin-check");
    var pinUnpin = id("pin-unpin");
    var pinHistory = id("pin-history");
    var pinError = id("pin-error");

    var job = null;
    var timer = null;
    var shownDoc = null;
    var storedRows = [];

    /* ---- formatting, borrowed from the circuit's own readout ---------- */

    function specFor(key) {
      var readout = current && current.readout;
      if (!readout) { return null; }
      var all = [readout.headline].concat(readout.stats || []);
      for (var i = 0; i < all.length; i++) {
        if (all[i] && all[i].key === key) { return all[i]; }
      }
      return null;
    }

    function fmt(value, key) {
      if (typeof value !== "number" || !isFinite(value)) { return "—"; }
      var spec = specFor(key) || {};
      if (spec.format === "db") { return value.toFixed(2) + " dB"; }
      if (spec.format === "deg") { return value.toFixed(1) + "°"; }
      if (spec.format === "plain") { return value.toPrecision(4); }
      return window.formatEngineering(value, spec.unit || "");
    }

    function labelFor(key) {
      var spec = specFor(key);
      return (spec && spec.label) || key;
    }

    /* ---- the document -------------------------------------------------- */

    function provenanceLine(doc) {
      var p = doc.provenance || {};
      var git = p.git || {};
      var bits = [];
      if (git.commit) {
        bits.push("commit " + String(git.commit).slice(0, 10) +
                  (git.clean === false ? " with uncommitted changes" : ""));
      }
      if (p.ngspice && p.ngspice.version) {
        bits.push("ngspice-" + p.ngspice.version);
      }
      if (p.pdk && p.pdk.version) {
        bits.push("PDK " + String(p.pdk.version).slice(0, 10));
      }
      return "Measured " + (doc.when_utc || "") + " · " + bits.join(" · ");
    }

    function metricTable(doc) {
      var bench = section(doc, "bench");
      if (!bench) { return null; }
      var corners = section(doc, "corners");
      var keys = Object.keys(bench.measured).filter(function (key) {
        return typeof bench.measured[key] === "number";
      });

      var wrap = el("div", "sheet-table-wrap");
      var table = el("table", "sheet-table");
      var head = el("tr");
      head.appendChild(el("th", null, "characteristic"));
      head.appendChild(el("th", null, "tt"));
      if (corners) {
        head.appendChild(el("th", null, "worst observed"));
        head.appendChild(el("th", null, "at"));
      }
      table.appendChild(head);

      keys.forEach(function (key) {
        var row = el("tr");
        row.appendChild(el("td", null, labelFor(key)));
        row.appendChild(el("td", "num", fmt(bench.measured[key], key)));
        if (corners) {
          var worst = (corners.worst || {})[key];
          row.appendChild(el("td", "num",
            worst ? fmt(worst.value, key) : "tt only"));
          row.appendChild(el("td", "sheet-at", worst ? worst.at : "—"));
        }
        table.appendChild(row);
      });
      wrap.appendChild(table);

      if (corners) {
        var measuredRows = (corners.rows || []).filter(function (r) {
          return r.measured;
        }).length;
        var totalRows = (corners.rows || []).length;
        var note = "Worst is the extreme over " + measuredRows + " of " +
          totalRows + " corner simulations. These are deterministic " +
          "points, not a distribution: no mismatch, no guardband, no " +
          "guarantee.";
        var failed = (corners.rows || []).filter(function (r) {
          return r.error;
        });
        if (failed.length) {
          note += " Did not bias: " + failed.map(function (r) {
            return r.label;
          }).join(", ") + ".";
        }
        wrap.appendChild(el("p", "sheet-note", note));
      }
      return wrap;
    }

    function section(doc, name) {
      var found = (doc.sections || {})[name];
      return (found && found.ran) ? found.data : null;
    }

    function marginRows(doc) {
      var bench = section(doc, "bench");
      var margins = bench && bench.margins;
      if (!margins || !margins.length) { return null; }
      var wrap = el("div", "sheet-table-wrap");
      var table = el("table", "sheet-table");
      var head = el("tr");
      ["target", "asked", "measured", "margin"].forEach(function (text) {
        head.appendChild(el("th", null, text));
      });
      table.appendChild(head);
      margins.forEach(function (m) {
        var row = el("tr", m.met ? "is-met" : "is-missed");
        row.appendChild(el("td", null,
          m.label + (m.binding ? " (binding)" : "")));
        row.appendChild(el("td", "num", m.op + " " + fmt(m.target, m.key)));
        row.appendChild(el("td", "num", fmt(m.measured, m.key)));
        row.appendChild(el("td", "num",
          (m.margin >= 0 ? "+" : "") + (m.margin * 100).toFixed(1) + "%"));
        table.appendChild(row);
      });
      wrap.appendChild(table);
      return wrap;
    }

    function pairList(data, keys) {
      var wrap = el("div", "metric-pairs");
      keys.forEach(function (key) {
        if (typeof data[key] !== "number") { return; }
        var cell = el("div");
        cell.appendChild(el("span", "stat-label", key.replace(/_/g, " ")));
        cell.appendChild(el("span", "stat-value",
          window.formatEngineering(data[key], unitGuess(key))));
        wrap.appendChild(cell);
      });
      return wrap;
    }

    function unitGuess(key) {
      if (/db$/.test(key)) { return "dB"; }
      if (/(^|_)(f|freq|bw|gbw)/.test(key)) { return "Hz"; }
      if (/time|delay/.test(key)) { return "s"; }
      if (/power/.test(key)) { return "W"; }
      if (/slew/.test(key)) { return "V/s"; }
      if (/v(out|in|_)/.test(key) || /range|swing|offset/.test(key)) {
        return "V";
      }
      return "";
    }

    function verdictList(doc) {
      var layout = section(doc, "layout");
      var deck = section(doc, "signoff");
      if (!layout && !deck) { return null; }
      var wrap = el("div", "sheet-verdicts");
      function verdict(ok, text) {
        var row = el("div", "verify-item " + (ok ? "is-pass" : "is-fail"));
        row.appendChild(el("span", "verify-mark", ok ? "PASS" : "FAIL"));
        row.appendChild(el("span", null, text));
        wrap.appendChild(row);
      }
      if (layout) {
        if (layout.drc) {
          verdict(layout.drc.clean, "Design rules, fast check");
        }
        if (layout.klvs && layout.klvs.ran !== false) {
          verdict(layout.klvs.match,
                  "Layout versus schematic, KLayout's engine");
        } else if (layout.lvs) {
          verdict(layout.lvs.match, "Layout versus schematic, own check");
        }
        wrap.appendChild(el("p", "sheet-note",
          "Area " + (layout.area_um2 || 0).toFixed(0) + " µm² · " +
          "drawn interconnect " +
          window.formatEngineering(layout.interconnect_f || 0, "F") + "."));
      }
      if (deck) {
        verdict(deck.clean, deck.clean
          ? "The foundry's own deck: 0 violations over " +
            (deck.shapes_checked || 0) + " shapes"
          : "The foundry's own deck: " + deck.total + " violations");
      }
      return wrap;
    }

    function renderDoc(doc) {
      shownDoc = doc;
      clear(docEl);

      docEl.appendChild(el("p", "sheet-provenance", provenanceLine(doc)));

      var margins = marginRows(doc);
      if (margins) {
        docEl.appendChild(el("h3", "mentor-head", "Targets"));
        docEl.appendChild(margins);
      }

      var metrics = metricTable(doc);
      if (metrics) {
        docEl.appendChild(el("h3", "mentor-head",
                             "Electrical characteristics"));
        docEl.appendChild(metrics);
      }

      var step = section(doc, "step");
      if (step) {
        docEl.appendChild(el("h3", "mentor-head", "Step response"));
        docEl.appendChild(pairList(step, Object.keys(step)));
      }
      var sheet = section(doc, "sheet");
      if (sheet) {
        docEl.appendChild(el("h3", "mentor-head", "Rejection and range"));
        docEl.appendChild(pairList(sheet, Object.keys(sheet)));
      }

      var verdicts = verdictList(doc);
      if (verdicts) {
        docEl.appendChild(el("h3", "mentor-head", "The drawn circuit"));
        docEl.appendChild(verdicts);
      }

      // Sections that could not run say so; silence would read as clean.
      Object.keys(doc.sections || {}).forEach(function (name) {
        var found = doc.sections[name];
        if (!found.ran && found.error) {
          docEl.appendChild(el("p", "field-error",
            name + " failed: " + found.error));
        }
      });

      if (doc.id) {
        var actions = el("p", "sheet-links");
        var open = el("a", null, "Open the printable datasheet");
        open.href = "/datasheet?id=" + encodeURIComponent(doc.id);
        open.target = "_blank";
        actions.appendChild(open);
        docEl.appendChild(actions);
      }

      show(docEl, true);
      checkStale();
    }

    function checkStale() {
      if (!shownDoc) { show(staleLine, false); return; }
      var now = values();
      var sizing = shownDoc.sizing || {};
      var moved = Object.keys(sizing).some(function (key) {
        return now[key] !== undefined && now[key] !== sizing[key];
      });
      show(staleLine, moved);
    }

    /* ---- the run -------------------------------------------------------- */

    function poll() {
      fetch("/api/workbench/status?job=" + job)
        .then(function (r) { return r.json(); })
        .then(function (snap) {
          if (snap.status === "running") {
            // The server's own clock: the ticker would fight the stage
            // text for the same node.
            stateLine.textContent = "Running: " + snap.stage + " · " +
              Math.round(snap.seconds) + " s";
            timer = setTimeout(poll, 1200);
            return;
          }
          runButton.disabled = false;
          show(stopButton, false);
          if (snap.status === "failed") {
            markTab("datasheet", true);
            errorLine.textContent = snap.error ||
              "The characterization failed.";
            show(errorLine, true);
            show(stateLine, false);
            return;
          }
          markTab("datasheet");
          stateLine.textContent = "Measured, " + snap.sims +
            " simulations observed at the simulator boundary.";
          if (snap.result) {
            snap.result.id = snap.stored_id;
            renderDoc(snap.result);
          }
          refreshStored();
        })
        .catch(function () { timer = setTimeout(poll, 2500); });
    }

    runButton.addEventListener("click", function () {
      show(errorLine, false);
      runButton.disabled = true;
      show(stopButton, true);
      stateLine.textContent = "Starting";
      show(stateLine, true);
      fetch("/api/workbench", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id, params: values(),
                               kind: "charact" })
      })
        .then(function (r) {
          return r.json().then(function (payload) {
            if (!r.ok) { throw new Error(payload.error || "Refused."); }
            job = payload.job;
            poll();
          });
        })
        .catch(function (error) {
          show(stateLine, false);
          runButton.disabled = false;
          show(stopButton, false);
          errorLine.textContent = String(error.message || error);
          show(errorLine, true);
        });
    });

    stopButton.addEventListener("click", function () {
      if (!job) { return; }
      fetch("/api/workbench/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job: job })
      });
    });

    /* ---- the store and the compare -------------------------------------- */

    function whenText(ident) {
      var m = /-(\d{8})-(\d{6})-/.exec(ident || "");
      if (!m) { return ident; }
      return m[1].slice(0, 4) + "-" + m[1].slice(4, 6) + "-" +
        m[1].slice(6, 8) + " " + m[2].slice(0, 2) + ":" + m[2].slice(2, 4);
    }

    function refreshStored() {
      fetch("/api/charact/list?circuit=" + encodeURIComponent(current.id))
        .then(function (r) { return r.json(); })
        .then(function (payload) {
          storedRows = payload.stored || [];
          clear(storedEl);
          if (!storedRows.length) {
            storedEl.appendChild(el("p", "hint",
              "Nothing stored yet. Characterize this sizing first."));
          }
          storedRows.slice(0, 5).forEach(function (row) {
            var line = el("div", "stored-row");
            line.appendChild(el("span", "stored-when",
              whenText(row.id) + " UTC"));
            line.appendChild(el("span",
              row.met === null ? "stored-open"
                : row.met ? "stored-met" : "stored-missed",
              row.met === null ? "" : row.met ? "targets met"
                : "targets missed"));
            var open = el("button", "chip", "Open");
            open.type = "button";
            open.addEventListener("click", function () {
              fetch("/api/charact/get?id=" + encodeURIComponent(row.id))
                .then(function (r) { return r.json(); })
                .then(renderDoc);
            });
            line.appendChild(open);
            var print = el("a", "stored-print", "Print view");
            print.href = "/datasheet?id=" + encodeURIComponent(row.id);
            print.target = "_blank";
            line.appendChild(print);
            storedEl.appendChild(line);
          });
          if (storedRows.length > 5) {
            var more = el("a", "stored-print",
              "All " + storedRows.length + " in the notebook");
            more.href = "/notebook";
            storedEl.appendChild(more);
          }
          fillPickers();
        })
        .catch(function () {});
    }

    function fillPickers() {
      [pickA, pickB].forEach(function (select, index) {
        clear(select);
        if (index === 0) {
          var live = document.createElement("option");
          live.value = "";
          live.textContent = "Current sizing, bench only (1 simulation)";
          select.appendChild(live);
        }
        storedRows.forEach(function (row) {
          var option = document.createElement("option");
          option.value = row.id;
          option.textContent = whenText(row.id) +
            (row.met === null ? "" : row.met ? " · met" : " · missed");
          select.appendChild(option);
        });
      });
    }

    function benchOf(doc) {
      var bench = section(doc, "bench");
      return bench ? bench.measured : null;
    }

    id("compare-run").addEventListener("click", function () {
      show(compareWarn, false);
      show(compareOut, false);
      var wantA = pickA.value;
      var wantB = pickB.value;
      if (!wantB) {
        compareWarn.textContent = "Pick a stored run for side B.";
        show(compareWarn, true);
        return;
      }
      var loads = [
        wantA
          ? fetch("/api/charact/get?id=" + encodeURIComponent(wantA))
              .then(function (r) { return r.json(); })
          : fetch("/api/simulate", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ circuit: current.id, params: values() })
            }).then(function (r) { return r.json(); })
             .then(function (measured) {
               return { live: true, sections: { bench: {
                 ran: true, data: { measured: measured } } } };
             }),
        fetch("/api/charact/get?id=" + encodeURIComponent(wantB))
          .then(function (r) { return r.json(); })
      ];
      Promise.all(loads).then(function (docs) {
        renderCompare(docs[0], docs[1]);
      }).catch(function (error) {
        compareWarn.textContent = String(error.message || error);
        show(compareWarn, true);
      });
    });

    function renderCompare(a, b) {
      var benchA = benchOf(a) || {};
      var benchB = benchOf(b) || {};
      var keys = [];
      Object.keys(benchA).concat(Object.keys(benchB)).forEach(function (k) {
        if (typeof (benchA[k] !== undefined ? benchA[k] : benchB[k])
            === "number" && keys.indexOf(k) < 0) {
          keys.push(k);
        }
      });

      clear(compareOut);
      var wrap = el("div", "sheet-table-wrap");
      var table = el("table", "sheet-table");
      var head = el("tr");
      ["", "A", "B", "B − A"].forEach(function (text) {
        head.appendChild(el("th", null, text));
      });
      table.appendChild(head);
      keys.forEach(function (key) {
        var va = benchA[key];
        var vb = benchB[key];
        var row = el("tr");
        row.appendChild(el("td", null, labelFor(key)));
        row.appendChild(el("td", "num", fmt(va, key)));
        row.appendChild(el("td", "num", fmt(vb, key)));
        row.appendChild(el("td", "num delta",
          (typeof va === "number" && typeof vb === "number")
            ? fmt(vb - va, key) : "—"));
        table.appendChild(row);
      });
      wrap.appendChild(table);
      compareOut.appendChild(wrap);

      // A bench-only side cannot argue about corners.
      var cornersA = section(a, "corners");
      var cornersB = section(b, "corners");
      if (cornersA && cornersB) {
        compareOut.appendChild(el("h3", "mentor-head", "Corner by corner"));
        compareOut.appendChild(cornerGrid(cornersA, cornersB));
      } else if (a.live) {
        compareOut.appendChild(el("p", "sheet-note",
          "Bench-only on side A; characterize it to compare corners " +
          "and layout."));
      }

      // Two runs measured by different stacks are not one experiment.
      var pa = (a.provenance || {});
      var pb = (b.provenance || {});
      if (!a.live && JSON.stringify([pa.ngspice, pa.pdk, (pa.git || {}).commit])
          !== JSON.stringify([pb.ngspice, pb.pdk, (pb.git || {}).commit])) {
        compareWarn.textContent = "These runs were measured by different " +
          "simulator versions; treat cross-run deltas with care.";
        show(compareWarn, true);
      }
      show(compareOut, true);
    }

    function cornerGrid(cornersA, cornersB) {
      var wrap = el("div", "sheet-table-wrap");
      var table = el("table", "sheet-table");
      var keys = cornersA.keys || [];
      var head = el("tr");
      head.appendChild(el("th", null, "corner"));
      keys.forEach(function (key) {
        head.appendChild(el("th", null, labelFor(key) + " A"));
        head.appendChild(el("th", null, "B"));
      });
      table.appendChild(head);
      (cornersA.rows || []).forEach(function (rowA, index) {
        var rowB = (cornersB.rows || [])[index] || {};
        var tr = el("tr");
        tr.appendChild(el("td", null, rowA.label));
        keys.forEach(function (key) {
          tr.appendChild(el("td", "num", rowA.measured
            ? fmt(rowA.measured[key], key) : "—"));
          tr.appendChild(el("td", "num", rowB.measured
            ? fmt(rowB.measured[key], key) : "—"));
        });
        table.appendChild(tr);
      });
      wrap.appendChild(table);
      return wrap;
    }

    /* ---- pins ----------------------------------------------------------- */

    function refreshPins() {
      fetch("/api/pin/status?circuit=" + encodeURIComponent(current.id))
        .then(function (r) { return r.json(); })
        .then(renderPins)
        .catch(function () {});
    }

    function renderPins(state) {
      clear(pinStateEl);
      clear(pinHistory);
      var pinned = state.pinned;
      show(pinCheck, Boolean(pinned));
      show(pinUnpin, Boolean(pinned));
      if (!pinned) {
        pinStateEl.appendChild(el("p", "hint",
          "Nothing pinned. Run the simulation, then pin its numbers " +
          "from the result."));
        return;
      }
      var line = "Pinned " + pinned.pinned_utc + " · " +
        Object.keys(pinned.expected).length + " numbers · tolerance ±" +
        (pinned.tolerance * 100).toFixed(1) + "%";
      pinStateEl.appendChild(el("p", "sheet-provenance", line));

      var records = state.history || [];
      if (records.length) {
        pinHistory.appendChild(sparkline(records, state.first_break));
        var last = records[records.length - 1];
        var text = last.ok
          ? "Last check held: every number within tolerance."
          : "Last check failed: " + (last.rows || [])
              .filter(function (r) { return !r.ok; })
              .map(function (r) { return r.key; }).join(", ") + " moved.";
        pinHistory.appendChild(el("p",
          last.ok ? "sheet-note" : "field-error", text));
      }
    }

    function sparkline(records, firstBreak) {
      var svg = document.createElementNS("http://www.w3.org/2000/svg",
                                         "svg");
      var step = 14;
      var width = Math.max(records.length * step, step);
      svg.setAttribute("viewBox", "0 0 " + width + " 20");
      svg.setAttribute("class", "pin-spark");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label",
        records.length + " checks of the pinned sizing");
      records.forEach(function (record, index) {
        var mark = document.createElementNS(
          "http://www.w3.org/2000/svg", "rect");
        mark.setAttribute("x", String(index * step + 2));
        mark.setAttribute("y", record.ok ? "6" : "2");
        mark.setAttribute("width", "8");
        mark.setAttribute("height", record.ok ? "8" : "16");
        mark.setAttribute("class",
          (record.ok ? "spark-ok" : "spark-bad") +
          (index === firstBreak ? " spark-first" : ""));
        svg.appendChild(mark);
      });
      return svg;
    }

    pinCheck.addEventListener("click", function () {
      show(pinError, false);
      pinCheck.disabled = true;
      tickStart(pinCheck.firstChild ? pinCheck : pinCheck);
      fetch("/api/pin/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id })
      })
        .then(function (r) {
          return r.json().then(function (payload) {
            if (!r.ok) { throw new Error(payload.error || "Refused."); }
            refreshPins();
          });
        })
        .catch(function (error) {
          pinError.textContent = String(error.message || error);
          show(pinError, true);
        })
        .then(function () {
          tickStop(pinCheck, null);
          pinCheck.disabled = false;
          pinCheck.textContent =
            "Re-measure the pinned sizing (1 simulation)";
        });
    });

    pinUnpin.addEventListener("click", function () {
      fetch("/api/pin/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id })
      }).then(refreshPins);
    });

    /* ---- the packet ----------------------------------------------------- */

    packetButton.addEventListener("click", function () {
      show(errorLine, false);
      packetButton.disabled = true;
      stateLine.textContent = "Building and verifying the packet";
      show(stateLine, true);
      tickStart(stateLine);
      fetch("/api/packet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id, params: values() })
      })
        .then(function (r) {
          if (!r.ok) {
            return r.json().then(function (payload) {
              throw new Error(payload.error || "The packet was refused.");
            });
          }
          return r.blob().then(function (blob) {
            var link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = current.id + "-packet.zip";
            document.body.appendChild(link);
            link.click();
            link.remove();
            tickStop(stateLine,
              "Packet built: verified in this run, digests inside.");
          });
        })
        .catch(function (error) {
          tickStop(stateLine, null);
          show(stateLine, false);
          errorLine.textContent = String(error.message || error);
          show(errorLine, true);
        })
        .then(function () { packetButton.disabled = false; });
    });

    /* ---- lifecycle ------------------------------------------------------ */

    return {
      key: "datasheet",
      render: function (circuit) {
        current = circuit;
        if (timer) { clearTimeout(timer); timer = null; }
        job = null;
        shownDoc = null;
        clear(docEl);
        show(docEl, false);
        show(staleLine, false);
        show(errorLine, false);
        show(stateLine, false);
        show(compareOut, false);
        show(compareWarn, false);
        runButton.disabled = false;
        show(stopButton, false);

        var sections = ["the bench"];
        if (circuit.step) { sections.push("the step"); }
        if (circuit.datasheet) { sections.push("rejection and range"); }
        if (circuit.pdk) { sections.push("all 11 corners"); }
        if (circuit.floorplan) { sections.push("the layout and its verdicts"); }
        var cost = 1 + (circuit.step ? 1 : 0) + (circuit.datasheet ? 1 : 0) +
          (circuit.pdk ? 11 : 0) + (circuit.floorplan ? 2 : 0);
        runLabel.textContent = "Characterize and write the datasheet (" +
          sections.join(", ") + "; about " + cost + " simulation" +
          (cost === 1 ? "" : "s") + ")";

        show(packetButton, Boolean(circuit.floorplan));
        refreshStored();
        refreshPins();
      },
      onValuesEdited: checkStale,
      refit: function () {
        // Called when the pane is shown (and on resize): the pins and
        // the store may have moved while another tab had the page.
        var now = Date.now();
        if (!this._lastRefit || now - this._lastRefit > 2000) {
          this._lastRefit = now;
          refreshStored();
          refreshPins();
        }
        checkStale();
      }
    };
  });
})(window, document);
