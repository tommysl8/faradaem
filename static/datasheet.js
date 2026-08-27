/* The printable datasheet: one stored characterization as a document.

   Black on white on purpose: this page is for the printer and the PDF,
   the one artifact of this tool that leaves the bench. Every value was
   measured by the server's characterization run; this file formats and
   never computes. */
(function (window, document) {
  "use strict";

  function el(tag, className, textContent) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (textContent !== undefined) { node.textContent = textContent; }
    return node;
  }

  var sheet = document.getElementById("sheet");
  var ident = new URLSearchParams(window.location.search).get("id") || "";

  document.getElementById("sheet-print").addEventListener("click",
    function () { window.print(); });

  /* Engineering notation, self-contained: this page must not pull the
     schematic library in to format a number for the printer. */
  function fmtEng(value, unit) {
    if (window.formatEngineering) {
      return window.formatEngineering(value, unit || "");
    }
    if (typeof value !== "number" || !isFinite(value)) { return "—"; }
    if (value === 0) { return "0" + (unit ? " " + unit : ""); }
    var prefixes = [
      [1e12, "T"], [1e9, "G"], [1e6, "M"], [1e3, "k"], [1, ""],
      [1e-3, "m"], [1e-6, "µ"], [1e-9, "n"], [1e-12, "p"], [1e-15, "f"]
    ];
    var magnitude = Math.abs(value);
    for (var i = 0; i < prefixes.length; i++) {
      if (magnitude >= prefixes[i][0] * 0.9995) {
        var scaled = value / prefixes[i][0];
        return scaled.toPrecision(4).replace(/\.?0+$/, "") + " " +
          prefixes[i][1] + (unit || "");
      }
    }
    return value.toExponential(3) + (unit ? " " + unit : "");
  }

  function fmt(value, key) {
    if (typeof value !== "number" || !isFinite(value)) { return "—"; }
    if (/_db$|^.*gain.*db$|db$/.test(key)) { return value.toFixed(2) + " dB"; }
    if (/margin|phase/.test(key) && Math.abs(value) < 361) {
      return value.toFixed(1) + "°";
    }
    if (/power/.test(key)) { return fmtEng(value, "W"); }
    if (/^f_|freq|crossover|bw|gbw/.test(key)) { return fmtEng(value, "Hz"); }
    if (/time|delay/.test(key)) { return fmtEng(value, "s"); }
    if (/slew/.test(key)) { return fmtEng(value, "V/s"); }
    if (/^v|_v$|range|swing|offset/.test(key)) { return fmtEng(value, "V"); }
    return fmtEng(value, "");
  }

  function section(doc, name) {
    var found = (doc.sections || {})[name];
    return (found && found.ran) ? found.data : null;
  }

  /* The same circuit-to-drawer map the workbench uses (app.js is the
     source of truth); a change there belongs here too. The schematic on
     a design review is the first thing a reviewer looks for. */
  var DRAWERS = {
    divider: "drawDivider",
    rc_lowpass: "drawRCLowpass",
    rc_highpass: "drawRCHighpass",
    rlc_bandpass: "drawRLCBandpass",
    inverting_amp: "drawInvertingAmp",
    twopole_amp: "drawTwopoleAmp",
    nfet_cs_amp: "drawNfetCsAmp",
    opamp_two_stage: "drawOpampTwoStage",
    ota_5t: "drawOta5t",
    folded_cascode: "drawFoldedCascode"
  };
  var SCHEMATIC_TAG = {
    divider: "vout", rc_lowpass: "f3db", rc_highpass: "f3db",
    rlc_bandpass: "f0_measured", inverting_amp: "midband_db",
    twopole_amp: "phase_margin", nfet_cs_amp: "midband_db",
    opamp_two_stage: "phase_margin", ota_5t: "phase_margin",
    folded_cascode: "phase_margin"
  };
  var TAG_ARG = {
    divider: "vout", rc_lowpass: "f3db", rc_highpass: "f3db",
    rlc_bandpass: "f0", inverting_amp: "gain_db",
    twopole_amp: "phase_margin", nfet_cs_amp: "gain_db",
    opamp_two_stage: "phase_margin", ota_5t: "phase_margin",
    folded_cascode: "phase_margin"
  };

  function drawSchematic(doc, bench) {
    var drawer = window[DRAWERS[doc.circuit]];
    if (typeof drawer !== "function") {
      return null;
    }
    var figure = el("figure", "printsheet-figure");
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    figure.appendChild(svg);
    var args = {};
    Object.keys(doc.sizing || {}).forEach(function (key) {
      args[key] = doc.sizing[key];
    });
    args[TAG_ARG[doc.circuit]] = bench && bench.measured
      ? bench.measured[SCHEMATIC_TAG[doc.circuit]] : null;
    try {
      drawer(svg, args);
    } catch (drawError) {
      return null;
    }
    return figure;
  }

  function table(headers, rows) {
    var wrap = el("div", "sheet-table-wrap");
    var node = el("table", "sheet-table");
    var head = el("tr");
    headers.forEach(function (text) {
      head.appendChild(el("th", null, text));
    });
    node.appendChild(head);
    rows.forEach(function (cells) {
      var tr = el("tr", cells.className);
      cells.forEach(function (cell) {
        tr.appendChild(cell);
      });
      node.appendChild(tr);
    });
    wrap.appendChild(node);
    return wrap;
  }

  function render(doc) {
    while (sheet.firstChild) { sheet.removeChild(sheet.firstChild); }

    var head = el("header", "printsheet-head");
    head.appendChild(el("h1", null, doc.name || doc.circuit));
    head.appendChild(el("p", "printsheet-sub",
      "Characterization " + (doc.id || "") + " · measured " +
      (doc.when_utc || "")));
    sheet.appendChild(head);

    var p = doc.provenance || {};
    var git = p.git || {};
    sheet.appendChild(el("p", "printsheet-prov",
      "Produced by Faradaem at commit " +
      String(git.commit || "unknown").slice(0, 10) +
      (git.clean === false ? " (uncommitted changes present)" : "") +
      " · ngspice-" + ((p.ngspice || {}).version || "unknown") +
      " · PDK " + String((p.pdk || {}).version || "unknown").slice(0, 10) +
      ". Every value below came out of a simulation; nothing was " +
      "estimated."));

    // The verdict, in one sentence, before any table: the reviewer's
    // first question answered first.
    var benchData = section(doc, "bench");
    if (benchData && benchData.margins && benchData.margins.length) {
      var missed = benchData.margins.filter(function (m) { return !m.met; });
      var bindingMargin = benchData.margins.filter(function (m) {
        return m.binding;
      })[0];
      var verdictText;
      if (!missed.length) {
        verdictText = "Every target holds as measured."
          + (bindingMargin
             ? " The tightest is " + bindingMargin.label + " at "
               + (bindingMargin.margin >= 0 ? "+" : "")
               + (bindingMargin.margin * 100).toFixed(1) + "% margin."
             : "");
      } else {
        verdictText = missed.length + " of " + benchData.margins.length
          + " targets missed: " + missed.map(function (m) {
            return m.label;
          }).join(", ") + ".";
      }
      sheet.appendChild(el("p", "printsheet-verdict "
        + (missed.length ? "is-missed" : "is-met"), verdictText));
    }

    // The schematic, drawn from the stored sizing with the measured tag
    // on its output node, exactly as the workbench draws it.
    var figure = drawSchematic(doc, benchData);
    if (figure) {
      sheet.appendChild(figure);
    }

    // Sizing, with each parameter's own unit when the document carries
    // them (older stored documents may not).
    var sizing = doc.sizing || {};
    var sizingUnits = doc.sizing_units || {};
    sheet.appendChild(el("h2", null, "Sizing"));
    sheet.appendChild(table(["parameter", "value"],
      Object.keys(sizing).sort().map(function (key) {
        return [el("td", null, key), el("td", "num",
          fmtEng(sizing[key], sizingUnits[key] || ""))];
      })));

    // Targets
    var bench = section(doc, "bench");
    if (bench && bench.margins && bench.margins.length) {
      sheet.appendChild(el("h2", null, "Targets"));
      sheet.appendChild(table(["target", "asked", "measured", "margin"],
        bench.margins.map(function (m) {
          var cells = [
            el("td", null, m.label + (m.binding ? " (binding)" : "")),
            el("td", "num", m.op + " " + fmt(m.target, m.key)),
            el("td", "num", fmt(m.measured, m.key)),
            el("td", "num",
              (m.margin >= 0 ? "+" : "") + (m.margin * 100).toFixed(1) + "%")
          ];
          cells.className = m.met ? "is-met" : "is-missed";
          return cells;
        })));
    }

    // Electrical characteristics
    var corners = section(doc, "corners");
    if (bench) {
      sheet.appendChild(el("h2", null, "Electrical characteristics"));
      var keys = Object.keys(bench.measured).filter(function (key) {
        return typeof bench.measured[key] === "number";
      });
      var headers = corners
        ? ["characteristic", "tt", "worst observed", "at"]
        : ["characteristic", "tt"];
      sheet.appendChild(table(headers, keys.map(function (key) {
        var cells = [el("td", null, key.replace(/_/g, " ")),
                     el("td", "num", fmt(bench.measured[key], key))];
        if (corners) {
          var worst = (corners.worst || {})[key];
          cells.push(el("td", "num",
            worst ? fmt(worst.value, key) : "tt only"));
          cells.push(el("td", "sheet-at", worst ? worst.at : "—"));
        }
        return cells;
      })));
      if (corners) {
        var measuredRows = (corners.rows || []).filter(function (r) {
          return r.measured;
        }).length;
        var note = "Worst is the extreme over " + measuredRows + " of " +
          (corners.rows || []).length + " corner simulations: five " +
          "process corners, supply and temperature extremes, and the " +
          "cross corners. Deterministic points, not a distribution: no " +
          "mismatch, no guardband, no guarantee.";
        var failed = (corners.rows || []).filter(function (r) {
          return r.error;
        });
        if (failed.length) {
          note += " Did not bias: " + failed.map(function (r) {
            return r.label;
          }).join(", ") + ".";
        }
        sheet.appendChild(el("p", "sheet-note", note));
      }
    }

    // Step, rejection and range
    ["step", "sheet"].forEach(function (name) {
      var data = section(doc, name);
      if (!data) { return; }
      sheet.appendChild(el("h2", null,
        name === "step" ? "Step response" : "Rejection and range"));
      sheet.appendChild(table(["measurement", "value"],
        Object.keys(data).filter(function (key) {
          return typeof data[key] === "number";
        }).map(function (key) {
          return [el("td", null, key.replace(/_/g, " ")),
                  el("td", "num", fmt(data[key], key))];
        })));
    });

    // The drawn circuit
    var lay = section(doc, "layout");
    var deck = section(doc, "signoff");
    if (lay || deck) {
      sheet.appendChild(el("h2", null, "The drawn circuit"));
      var rows = [];
      if (lay) {
        rows.push([el("td", null, "area"),
                   el("td", "num",
                     typeof lay.area_um2 === "number"
                       ? lay.area_um2.toFixed(0) + " µm²"
                       : "not measured")]);
        if (typeof lay.interconnect_f === "number") {
          rows.push([el("td", null, "drawn interconnect"),
                     el("td", "num", fmtEng(lay.interconnect_f, "F"))]);
        }
        if (lay.drc) {
          rows.push([el("td", null, "design rules, fast check"),
                     el("td", "num", lay.drc.clean ? "clean" : "FAILED")]);
        }
        var engine = lay.klvs && lay.klvs.ran !== false ? lay.klvs : lay.lvs;
        if (engine) {
          rows.push([el("td", null, "layout versus schematic"),
                     el("td", "num", engine.match ? "match" : "MISMATCH")]);
        }
      }
      if (deck) {
        rows.push([el("td", null, "the foundry's own deck"),
                   el("td", "num", deck.clean
                     ? "0 violations, " + (deck.shapes_checked || 0) +
                       " shapes"
                     : deck.total + " violations")]);
      }
      sheet.appendChild(table(["check", "result"], rows));
    }

    // Sections that failed say so.
    Object.keys(doc.sections || {}).forEach(function (name) {
      var found = doc.sections[name];
      if (!found.ran && found.error) {
        sheet.appendChild(el("p", "sheet-note",
          "The " + name + " section failed: " + found.error));
      }
    });

    sheet.appendChild(el("p", "printsheet-foot",
      "Generated by Faradaem. The measurements, their conditions and " +
      "their provenance are stored in " + (doc.id || "this run") +
      "; re-running the characterization at the same sizing and stack " +
      "reproduces this document."));

    document.title = (doc.name || doc.circuit) + " datasheet - Faradaem";
  }

  function sheetMessage(text) {
    while (sheet.firstChild) { sheet.removeChild(sheet.firstChild); }
    sheet.appendChild(el("p", "field-error", text));
  }

  if (!ident) {
    // Arriving without an id is a navigation mistake, not a server
    // error: say where the documents live instead of fetching nothing.
    sheetMessage("No datasheet id in the URL. Open a stored datasheet "
      + "from the notebook.");
  } else {
    fetch("/api/charact/get?id=" + encodeURIComponent(ident))
      .then(function (r) {
        return r.json().then(function (payload) {
          if (!r.ok) {
            throw new Error(payload.error || "Not found.");
          }
          render(payload);
        });
      })
      .catch(function (error) {
        sheetMessage(String(error.message || error)
          + " Open a stored datasheet from the notebook.");
      });
  }
})(window, document);
