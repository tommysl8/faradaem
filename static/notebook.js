/* The notebook page: the ledger and the datasheet store, made readable.

   Everything here is presentation over records the server already holds.
   The page never computes a circuit value; it counts, groups and links. */
(function (window, document) {
  "use strict";

  function id(name) { return document.getElementById(name); }

  function show(element, visible) {
    element.classList.toggle("hidden", !visible);
  }

  function el(tag, className, textContent) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (textContent !== undefined) { node.textContent = textContent; }
    return node;
  }

  function clear(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  var runsEl = id("notebook-runs");
  var sheetsEl = id("notebook-sheets");
  var moreButton = id("notebook-more");
  var errorEl = id("notebook-error");
  var offset = 0;
  var loaded = [];

  /* ---- stored datasheets --------------------------------------------- */

  function whenText(ident) {
    var m = /-(\d{8})-(\d{6})-/.exec(ident || "");
    if (!m) { return ident; }
    return m[1].slice(0, 4) + "-" + m[1].slice(4, 6) + "-" +
      m[1].slice(6, 8) + " " + m[2].slice(0, 2) + ":" + m[2].slice(2, 4) +
      " UTC";
  }

  function renderSheets(rows) {
    clear(sheetsEl);
    if (!rows.length) {
      var hint = el("p", "hint");
      hint.appendChild(document.createTextNode(
        "No datasheets stored yet. Characterize a circuit from its "));
      var link = el("a", null, "Datasheet tab on the workbench");
      link.href = "/";
      hint.appendChild(link);
      hint.appendChild(document.createTextNode("."));
      sheetsEl.appendChild(hint);
      return;
    }
    rows.forEach(function (row) {
      var line = el("div", "stored-row");
      line.appendChild(el("span", "stored-circuit", row.name || row.circuit));
      line.appendChild(el("span", "stored-when", whenText(row.id)));
      line.appendChild(el("span",
        row.met === null ? "stored-open"
          : row.met ? "stored-met" : "stored-missed",
        row.met === null
          ? "no targets measured (ran: "
            + ((row.sections_ran || []).join(", ") || "nothing") + ")"
          : row.met ? "targets met" : "targets missed"));
      var open = el("a", "stored-print", "Open");
      open.href = "/datasheet?id=" + encodeURIComponent(row.id);
      line.appendChild(open);
      sheetsEl.appendChild(line);
    });
  }

  fetch("/api/charact/list")
    .then(function (r) { return r.json(); })
    .then(function (payload) { renderSheets(payload.stored || []); })
    .catch(function () {
      clear(sheetsEl);
      sheetsEl.appendChild(el("p", "hint",
        "The store could not be read. Is the server running?"));
    });

  /* ---- runs ----------------------------------------------------------- */

  function runRow(run) {
    var line = el("div", "run-row");

    var head = el("div", "run-head");
    head.appendChild(el("span", "run-id", run.path_name));
    head.appendChild(el("span", "run-sims",
      run.sims + " simulation" + (run.sims === 1 ? "" : "s")));
    if (run.damaged) {
      head.appendChild(el("span", "run-damaged",
        run.damaged + " damaged line" + (run.damaged === 1 ? "" : "s") +
        " (a crash left these)"));
    }
    line.appendChild(head);

    var facts = [];
    if (run.circuits.length) {
      facts.push(run.circuits.join(", "));
    }
    if (run.git) {
      facts.push("commit " + String(run.git).slice(0, 10));
    }
    facts.push(run.records + " records");
    line.appendChild(el("p", "run-facts", facts.join(" · ")));

    if (run.results.length) {
      var verdicts = el("p", "run-results");
      run.results.forEach(function (result) {
        // Three readings, not two: met and completed are green, failures
        // and misses are red, and the neutral outcomes (not_run, stopped,
        // aborted) stay muted instead of wearing a failure they are not.
        var tone = "";
        if (["met", "completed", "survived", "explained"]
            .indexOf(result.status) !== -1) {
          tone = " is-met";
        } else if (["missed", "error", "failed", "budget_exhausted",
                    "broken", "unexplained"].indexOf(result.status) !== -1) {
          tone = " is-missed";
        }
        var mark = el("span", "run-result" + tone,
          (result.arm || "run") + ": " + result.status);
        verdicts.appendChild(mark);
      });
      line.appendChild(verdicts);
    }
    return line;
  }

  function renderRuns() {
    clear(runsEl);
    if (!loaded.length) {
      runsEl.appendChild(el("p", "hint",
        "No runs recorded yet. Anything measured through the workbench " +
        "or the experiment lands here."));
      return;
    }
    loaded.forEach(function (run) {
      runsEl.appendChild(runRow(run));
    });
  }

  function fetchPage() {
    fetch("/api/notebook?offset=" + offset)
      .then(function (r) { return r.json(); })
      .then(function (payload) {
        show(errorEl, false);
        loaded = loaded.concat(payload.rows || []);
        offset = payload.offset + (payload.rows || []).length;
        renderRuns();
        show(moreButton, Boolean(payload.more));
        moreButton.textContent = "Show 20 more (" +
          (payload.total - loaded.length) + " older)";
      })
      .catch(function () {
        errorEl.textContent = "The ledger could not be read. Is the " +
          "server running?";
        show(errorEl, true);
        // A failed "show more" keeps what is already on the page; only a
        // failed first load leaves it empty.
        if (!loaded.length) {
          clear(runsEl);
        }
      });
  }

  moreButton.addEventListener("click", fetchPage);

  /* ---- export --------------------------------------------------------- */

  id("notebook-export").addEventListener("click", function () {
    var lines = ["# Faradaem notebook", ""];
    loaded.forEach(function (run) {
      lines.push("## " + run.path_name);
      lines.push("");
      lines.push("- simulations: " + run.sims);
      lines.push("- records: " + run.records +
        (run.damaged ? " (" + run.damaged + " damaged)" : ""));
      if (run.circuits.length) {
        lines.push("- circuits: " + run.circuits.join(", "));
      }
      if (run.git) {
        lines.push("- commit: " + run.git);
      }
      run.results.forEach(function (result) {
        lines.push("- " + (result.arm || "run") + ": " + result.status);
      });
      lines.push("");
    });
    var blob = new Blob([lines.join("\n")],
                        { type: "text/markdown" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "faradaem-notebook.md";
    document.body.appendChild(link);
    link.click();
    link.remove();
  });

  fetchPage();
})(window, document);
