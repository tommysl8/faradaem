/* Faradaem simulator page -- rendered from the circuit catalogue.
 *
 * The page knows no circuits. It fetches /api/circuits and builds the tab row,
 * the preset chips, the input form and the result strip from what comes back.
 * Adding a circuit on the server makes it appear here with no frontend change.
 *
 * The only per-circuit knowledge left is which schematic compose function to
 * call, which is genuine drawing code and lives in schematic.js.
 *
 * Interaction rules that hold everywhere:
 *   - a figure always shows the circuit the inputs describe, redrawn on input
 *   - only a completed ngspice run may put a measured number on the page
 *   - a run in flight dims the stale reading rather than blanking it
 */

(function (window, document) {
  "use strict";

  var DRAWERS = {
    divider: "drawDivider",
    rc_lowpass: "drawRCLowpass",
    rc_highpass: "drawRCHighpass",
    rlc_bandpass: "drawRLCBandpass",
    inverting_amp: "drawInvertingAmp",
    twopole_amp: "drawTwopoleAmp",
    nfet_cs_amp: "drawNfetCsAmp",
    opamp_two_stage: "drawOpampTwoStage"
  };

  /* Which measured value the schematic tags its output node with, and the
   * argument name each compose function expects it under. */
  var SCHEMATIC_TAG = {
    divider: "vout",
    rc_lowpass: "f3db",
    rc_highpass: "f3db",
    rlc_bandpass: "f0_measured",
    inverting_amp: "midband_db",
    twopole_amp: "phase_margin",
    nfet_cs_amp: "midband_db",
    opamp_two_stage: "phase_margin"
  };

  var TAG_ARG = {
    divider: "vout",
    rc_lowpass: "f3db",
    rc_highpass: "f3db",
    rlc_bandpass: "f0",
    inverting_amp: "gain_db",
    twopole_amp: "phase_margin",
    nfet_cs_amp: "gain_db",
    opamp_two_stage: "phase_margin"
  };

  /* Arrow keys step the leading digit: 1000 -> 2000, 1.5e-7 -> 2.5e-7.
   * Shift makes it a decade. Stepping down out of a decade steps finer, so
   * 1000 goes to 900 rather than to zero. */
  function stepValue(value, direction, shift) {
    var magnitude = Math.abs(value);
    if (!isFinite(magnitude) || magnitude === 0) {
      magnitude = 1;
    }

    var exponent = Math.floor(Math.log10(magnitude));
    var mantissa = magnitude / Math.pow(10, exponent);
    if (direction < 0 && mantissa <= 1 + 1e-9) {
      exponent -= 1;
    }

    var step = Math.pow(10, exponent + (shift ? 1 : 0));
    return Number((value + direction * step).toPrecision(12));
  }

  window.FaradaemAppInternals = { stepValue: stepValue };

  var form = document.getElementById("sim-form");
  if (!form) {
    return;
  }

  function id(name) {
    return document.getElementById(name);
  }

  function show(element, visible) {
    element.classList.toggle("hidden", !visible);
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function el(tag, className, textContent) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (textContent !== undefined) {
      node.textContent = textContent;
    }
    return node;
  }

  var modesEl = id("modes");
  var presetsEl = id("presets");
  var inputsEl = id("inputs");
  var statsEl = id("stats");
  var runButton = id("run");
  var runLabel = id("run-label");
  var errorEl = id("error");
  var errorTextEl = id("error-text");
  var bodePanel = id("bode-panel");
  var resultEl = id("result");
  var captionState = id("caption-state");

  var catalogue = [];
  var current = null;
  var inputs = {};
  var tabs = [];

  /* Values survive a trip to another circuit and back, for this session. */
  var memory = {};

  /* The most recent successful measurement, for flows that act on it. */
  var lastResult = null;

  /* ---- number presentation ------------------------------------------ */

  function present(value, spec) {
    if (typeof value !== "number" || !isFinite(value)) {
      return "—";
    }
    if (spec.format === "db") {
      return value.toFixed(2) + " dB";
    }
    if (spec.format === "deg") {
      return value.toFixed(1) + "°";
    }
    if (spec.format === "plain") {
      return value.toPrecision(4);
    }
    return window.formatEngineering(value, spec.unit || "");
  }

  function relativeDifference(a, b) {
    var scale = Math.max(Math.abs(a), Math.abs(b));
    return scale === 0 ? 0 : Math.abs(a - b) / scale;
  }

  function checkFor(circuit, key) {
    for (var i = 0; i < circuit.checks.length; i++) {
      if (circuit.checks[i].key === key) {
        return circuit.checks[i];
      }
    }
    return null;
  }

  /* A dB check is an absolute comparison; everything else is relative. */
  function agrees(check, measured, expected) {
    if (check.unit === "dB") {
      return Math.abs(measured - expected) <= 0.2;
    }
    return relativeDifference(measured, expected) <= check.tolerance;
  }

  function badge(node, ok, measured, expected) {
    var off = relativeDifference(measured, expected) * 100;
    node.textContent = ok
      ? "agrees"
      : "off by " + (off < 10 ? off.toFixed(1) : off.toFixed(0)) + "%";
    node.classList.toggle("is-ok", ok);
    node.classList.toggle("is-warn", !ok);
    show(node, true);
  }

  /* ---- reading and validating the form ------------------------------- */

  function values() {
    var out = {};
    Object.keys(inputs).forEach(function (key) {
      out[key] = Number(inputs[key].value);
    });
    return out;
  }

  function complain(spec, value) {
    if (inputs[spec.key].value.trim() === "" || !isFinite(value)) {
      return spec.label + " needs a number.";
    }
    if (value < spec.min) {
      return spec.label + " must be at least " +
        window.formatEngineering(spec.min, spec.unit || "") + ". Raise it and run again.";
    }
    if (value > spec.max) {
      return spec.label + " must be at most " +
        window.formatEngineering(spec.max, spec.unit || "") + ". Lower it and run again.";
    }
    return null;
  }

  /* Show or clear the message under each field. Returns true if all are good. */
  function validate() {
    var ok = true;
    Object.keys(inputs).forEach(function (key) {
      var input = inputs[key];
      var problem = complain(input.spec, Number(input.value));
      input.field.classList.toggle("is-invalid", Boolean(problem));
      input.error.textContent = problem || "";
      show(input.error, Boolean(problem));
      if (problem) {
        input.setAttribute("aria-invalid", "true");
        ok = false;
      } else {
        input.removeAttribute("aria-invalid");
      }
    });
    return ok;
  }

  function onArrowKey(event, input) {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") {
      return;
    }
    event.preventDefault();

    var spec = input.spec;
    var next = stepValue(Number(input.value) || 0,
                         event.key === "ArrowUp" ? 1 : -1,
                         event.shiftKey);
    next = Math.min(spec.max, Math.max(spec.min, next));

    input.value = String(next);
    onEdit();
  }

  /* ---- rendering the panel -------------------------------------------- */

  function renderTabs() {
    clear(modesEl);
    tabs = catalogue.map(function (circuit) {
      var tab = el("button", "mode", circuit.name);
      var active = circuit.id === current.id;
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.tabIndex = active ? 0 : -1;
      tab.dataset.circuit = circuit.id;
      tab.addEventListener("click", function () {
        if (circuit.id !== current.id) {
          select(circuit.id);
        }
      });
      modesEl.appendChild(tab);
      return tab;
    });
  }

  /* Arrows move focus between tabs; Enter or Space selects the focused one. */
  modesEl.addEventListener("keydown", function (event) {
    var index = tabs.indexOf(document.activeElement);
    if (index < 0) {
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(tabs[index].dataset.circuit);
      var moved = tabs[index];
      if (moved) {
        moved.focus();
      }
      return;
    }

    var delta = 0;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      delta = 1;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      delta = -1;
    } else if (event.key === "Home") {
      delta = -index;
    } else if (event.key === "End") {
      delta = tabs.length - 1 - index;
    } else {
      return;
    }

    event.preventDefault();
    var next = (index + delta + tabs.length) % tabs.length;
    tabs.forEach(function (tab, i) {
      tab.tabIndex = i === next ? 0 : -1;
    });
    tabs[next].focus();
  });

  function renderPresets() {
    clear(presetsEl);
    (current.presets || []).forEach(function (item) {
      var chip = el("button", "chip", item.label);
      chip.type = "button";
      chip.addEventListener("click", function () {
        Object.keys(item.params).forEach(function (key) {
          if (inputs[key]) {
            inputs[key].value = String(item.params[key]);
          }
        });
        onEdit();
        run();
      });
      presetsEl.appendChild(chip);
    });
  }

  function renderInputs(preset) {
    clear(inputsEl);
    inputs = {};

    current.params.forEach(function (spec) {
      var field = el("div", "field");

      var label = el("label", null, spec.label + " ");
      label.htmlFor = "param-" + spec.key;
      var echo = el("span", "field-echo");
      label.appendChild(echo);
      field.appendChild(label);

      var shell = el("div", "input-shell");
      var input = document.createElement("input");
      input.id = "param-" + spec.key;
      input.type = "number";
      input.step = "any";
      input.value = String(preset && preset[spec.key] !== undefined
        ? preset[spec.key]
        : spec.default);
      input.inputMode = "decimal";
      input.autocomplete = "off";
      input.addEventListener("input", onEdit);
      input.addEventListener("keydown", function (event) {
        onArrowKey(event, input);
      });
      shell.appendChild(input);

      if (spec.unit) {
        var unit = el("span", "unit", spec.unit);
        unit.setAttribute("aria-hidden", "true");
        shell.appendChild(unit);
      }
      field.appendChild(shell);

      var error = el("p", "field-error hidden");
      error.id = "error-" + spec.key;
      input.setAttribute("aria-describedby", error.id);
      field.appendChild(error);

      inputsEl.appendChild(field);

      input.spec = spec;
      input.echo = echo;
      input.field = field;
      input.error = error;
      inputs[spec.key] = input;
    });
  }

  /* ---- figures --------------------------------------------------------- */

  function redraw(result, animate) {
    var reading = values();

    Object.keys(inputs).forEach(function (key) {
      var input = inputs[key];
      input.echo.textContent = window.formatEngineering(
        reading[key], input.spec.unit || ""
      );
    });

    var args = {};
    Object.keys(reading).forEach(function (key) {
      args[key] = reading[key];
    });
    args[TAG_ARG[current.id]] = result ? result[SCHEMATIC_TAG[current.id]] : null;
    window[DRAWERS[current.id]](id("schematic"), args);

    var isAc = current.analysis === "ac";
    show(bodePanel, isAc);
    if (isAc) {
      window.drawBode(id("bode"), result ? bodeData(result, animate) : {});
    }
  }

  function bodeData(result, animate) {
    var markers = (current.readout.markers || []).map(function (marker) {
      return { freq: result[marker.key], label: marker.label };
    }).filter(function (marker) {
      return typeof marker.freq === "number" && isFinite(marker.freq);
    });

    return {
      freq: result.freq,
      mag_db: result.mag_db,
      phase_deg: result.phase_deg,
      markers: markers,
      animate: Boolean(animate)
    };
  }

  /* ---- result strip ----------------------------------------------------- */

  function clearResult() {
    var headline = current.readout.headline;
    id("headline-label").textContent = headline.label;
    id("headline-value").textContent = "—";
    id("headline-value").classList.add("placeholder");
    show(id("headline-check"), false);
    show(id("headline-badge"), false);
    show(id("note"), false);
    clear(statsEl);
    show(statsEl, false);
    captionState.textContent = "Run to measure.";
  }

  function renderResult(result) {
    var readout = current.readout;
    var headline = readout.headline;
    var analytic = result.analytic || {};

    id("headline-label").textContent = headline.label;
    id("headline-value").textContent = present(result[headline.key], headline);
    id("headline-value").classList.remove("placeholder");
    captionState.textContent = "";

    /* A circuit may ship no closed-form check at all -- the SKY130 stage does
     * not, because square law does not describe a short-channel device. Then
     * the check and its badge stay hidden and the note carries whatever
     * caution the server sent about the operating point instead. */
    var noteText = "";
    var check = headline.check ? checkFor(current, headline.check) : null;
    if (check && typeof analytic[check.key] === "number") {
      var expected = analytic[check.key];
      var measured = result[headline.key];
      id("headline-check-label").textContent = check.label;
      id("headline-check-value").textContent = present(expected, headline);
      show(id("headline-check"), true);

      var ok = agrees(check, measured, expected);
      badge(id("headline-badge"), ok, measured, expected);
      if (!ok) {
        noteText =
          "Measured " + present(measured, headline) + " against " +
          present(expected, headline) + ". The simulator is the result; the " +
          "closed form is only the check.";
      }
    } else {
      show(id("headline-check"), false);
      show(id("headline-badge"), false);
    }

    if (!noteText && typeof result.note === "string") {
      noteText = result.note;
    }
    id("note").textContent = noteText;
    show(id("note"), Boolean(noteText));

    clear(statsEl);
    readout.stats.forEach(function (stat) {
      var cell = el("div");
      cell.appendChild(el("span", "stat-label", stat.label));
      cell.appendChild(el("span", "stat-value", present(result[stat.key], stat)));

      var statCheck = stat.check ? checkFor(current, stat.check) : null;
      if (statCheck && typeof analytic[statCheck.key] === "number") {
        var want = analytic[statCheck.key];
        var got = result[stat.key];
        cell.appendChild(el("span", "stat-check", "vs " + present(want, stat)));

        var pill = el("span", "badge badge-small");
        badge(pill, agrees(statCheck, got, want), got, want);
        cell.appendChild(pill);
      }
      statsEl.appendChild(cell);
    });
    show(statsEl, readout.stats.length > 0);
  }

  /* ---- errors ------------------------------------------------------------ */

  function messageFor(response, payload) {
    if (payload && typeof payload.error === "string") {
      return payload.error;
    }
    return "The server returned HTTP " + response.status + " " +
      response.statusText + ". Check the console running server.py.";
  }

  function showError(message) {
    errorTextEl.textContent = message;
    show(errorEl, true);
  }

  function dismissError() {
    show(errorEl, false);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !errorEl.classList.contains("hidden")) {
      dismissError();
    }
  });

  /* ---- running ----------------------------------------------------------- */

  function setPending(pending) {
    runButton.disabled = pending;
    runLabel.textContent = pending ? "Running" : "Run simulation";

    var dots = runButton.querySelector(".run-dots");
    if (pending && !dots) {
      dots = el("span", "run-dots");
      dots.appendChild(el("i"));
      dots.appendChild(el("i"));
      dots.appendChild(el("i"));
      runButton.appendChild(dots);
    } else if (!pending && dots) {
      runButton.removeChild(dots);
    }

    // Dim the stale reading rather than blanking it.
    [bodePanel, resultEl].forEach(function (node) {
      node.classList.toggle("is-loading", pending);
    });
  }

  function onEdit() {
    validate();
    clearResult();
    dismissError();
    redraw(null, false);
    memory[current.id] = values();
  }

  async function run(event) {
    if (event) {
      event.preventDefault();
    }
    if (!validate()) {
      // The inline messages already say what to fix; do not also shout.
      return;
    }

    setPending(true);
    try {
      var response = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: current.id, params: values() })
      });

      var payload = null;
      try {
        payload = await response.json();
      } catch (parseError) {
        payload = null;
      }

      if (!response.ok) {
        showError(messageFor(response, payload));
      } else if (!payload || typeof payload[current.readout.headline.key] !== "number") {
        showError("The server did not return a usable measurement. Run it again.");
      } else {
        dismissError();
        lastResult = payload;
        renderResult(payload);
        redraw(payload, true);
      }
    } catch (networkError) {
      showError("Could not reach the Faradaem server. Start it with python " +
                "server.py and run again.");
    } finally {
      setPending(false);
    }
  }


  /* ---- design to spec ---------------------------------------------------- */

  var designPanel = id("design");
  var designGoalsEl = id("design-goals");
  var designGenerate = id("design-generate");
  var designGenerateLabel = id("design-generate-label");
  var designStart = id("design-start");
  var designStartLabel = id("design-start-label");
  var designStop = id("design-stop");
  var designProgress = id("design-progress");
  var designState = id("design-state");
  var designEvals = id("design-evals");
  var designBest = id("design-best");
  var designReason = id("design-reason");
  var designApply = id("design-apply");
  var designError = id("design-error");

  var designInputs = {};
  var designJob = null;
  var designTimer = null;
  var designResult = null;

  /* "generate" runs the whole story on its own: seed, confirm, iterate if
   * short, apply. "manual" leaves apply to the user. */
  var designMode = "manual";

  function designIdle() {
    if (designTimer) {
      clearTimeout(designTimer);
      designTimer = null;
    }
    designJob = null;
    designResult = null;
    designMode = "manual";
    designGenerate.disabled = false;
    designGenerateLabel.textContent = "Generate design from specs";
    designStart.disabled = false;
    designStartLabel.textContent = "Start optimization";
    show(designProgress, false);
    show(designApply, false);
    show(designReason, false);
    show(designError, false);
    clear(designBest);
  }

  function renderDesignPanel() {
    designIdle();
    clear(designGoalsEl);
    designInputs = {};

    var block = current.design;
    show(designPanel, Boolean(block));
    if (!block) {
      return;
    }
    show(designGenerate, Boolean(block.seeded));

    block.goals.forEach(function (item) {
      var field = el("div", "field");
      var label = el("label", null,
        item.label + " " + (item.op === ">=" ? "at least" : "at most") + " ");
      label.htmlFor = "goal-" + item.key;
      field.appendChild(label);

      var shell = el("div", "input-shell");
      var input = document.createElement("input");
      input.id = "goal-" + item.key;
      input.type = "number";
      input.step = "any";
      input.value = String(item.default);
      input.inputMode = "decimal";
      input.autocomplete = "off";
      shell.appendChild(input);
      if (item.unit) {
        var unit = el("span", "unit", item.unit);
        unit.setAttribute("aria-hidden", "true");
        shell.appendChild(unit);
      }
      field.appendChild(shell);
      designGoalsEl.appendChild(field);
      designInputs[item.key] = input;
    });
  }

  function designShowError(message) {
    designError.textContent = message;
    show(designError, true);
  }

  /* One row per goal: what the best point measures, what was asked, verdict. */
  function renderDesignBest(snapshot) {
    clear(designBest);
    var best = snapshot.best;
    (current.design.goals || []).forEach(function (item) {
      var target = snapshot.targets[item.key];
      var value = best && best.measured ? best.measured[item.key] : null;
      var met = best && best.margins && best.margins[item.key] >= 0;

      designBest.appendChild(el("span", "goal-label", item.label));
      designBest.appendChild(el("span", "goal-value",
        value === null || value === undefined
          ? "\u2014"
          : present(value, { format: item.unit === "dB" ? "db"
              : item.unit === "deg" ? "deg" : "eng", unit: item.unit })));
      designBest.appendChild(el("span", "goal-target",
        (item.op === ">=" ? "\u2265 " : "\u2264 ") +
        present(target, { format: item.unit === "dB" ? "db"
          : item.unit === "deg" ? "deg" : "eng", unit: item.unit })));
      var mark = el("span", "goal-mark", met ? "met" : "short");
      if (met) {
        mark.classList.add("is-met");
      }
      designBest.appendChild(mark);
    });
  }

  function pollDesign() {
    if (!designJob) {
      return;
    }
    fetch("/api/design/status?job=" + encodeURIComponent(designJob))
      .then(function (response) { return response.json(); })
      .then(function (snapshot) {
        if (!designJob) {
          return;
        }
        designEvals.textContent =
          snapshot.evals + " / " + snapshot.max_evals + " simulations";
        renderDesignBest(snapshot);

        if (snapshot.status === "running") {
          designState.textContent = "Running";
          designTimer = setTimeout(pollDesign, 1200);
          return;
        }

        designState.textContent =
          snapshot.status === "done"
            ? (snapshot.feasible ? "Spec met" : "Finished")
            : snapshot.status === "stopped" ? "Stopped" : "Failed";
        designStart.disabled = false;
        designStartLabel.textContent = "Optimize from current values";
        designGenerate.disabled = false;
        designGenerateLabel.textContent = "Generate design from specs";
        show(designStop, false);

        if (snapshot.status === "failed") {
          designShowError(snapshot.error ||
            "The search failed. Check the console running server.py.");
          return;
        }
        if (snapshot.reason) {
          designReason.textContent = snapshot.reason;
          show(designReason, true);
        }
        if (snapshot.best && snapshot.best.params) {
          designResult = snapshot.best.params;
          if (designMode === "generate" && snapshot.feasible) {
            // The generate flow finishes its own story: load the winning
            // values and run the confirming simulation.
            applyDesign();
          } else {
            show(designApply, true);
          }
        }
      })
      .catch(function () {
        if (designJob) {
          designTimer = setTimeout(pollDesign, 2500);
        }
      });
  }

  function collectTargets() {
    var targets = {};
    var bad = null;
    Object.keys(designInputs).forEach(function (key) {
      var value = Number(designInputs[key].value);
      if (!isFinite(value) || value <= 0) {
        bad = key;
      }
      targets[key] = value;
    });
    if (bad !== null) {
      designShowError("Every target needs a positive number. Fix " + bad +
                      " and start again.");
      return null;
    }
    return targets;
  }

  /* Does a finished measurement satisfy every target? Plain comparison of
   * measured numbers, the same arithmetic the badges already do. */
  function meetsTargets(measured, targets) {
    return (current.design.goals || []).every(function (item) {
      var value = measured[item.key];
      if (typeof value !== "number" || !isFinite(value)) {
        return false;
      }
      return item.op === ">="
        ? value >= targets[item.key]
        : value <= targets[item.key];
    });
  }

  function startDesign(mode) {
    if (!current.design || !validate()) {
      return;
    }
    show(designError, false);
    show(designReason, false);
    show(designApply, false);
    designResult = null;
    designMode = mode === "generate" ? "generate" : "manual";

    var targets = collectTargets();
    if (targets === null) {
      return;
    }

    designGenerate.disabled = true;
    designStart.disabled = true;
    designStartLabel.textContent = "Searching";
    designState.textContent = "Starting";
    designEvals.textContent = "";
    clear(designBest);
    show(designProgress, true);
    show(designStop, true);

    fetch("/api/design", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        circuit: current.id,
        params: values(),
        targets: targets
      })
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) {
            throw new Error(payload && payload.error
              ? payload.error
              : "The server refused the design request.");
          }
          designJob = payload.job;
          pollDesign();
        });
      })
      .catch(function (error) {
        designIdle();
        designShowError(String(error.message || error));
      });
  }

  function stopDesign() {
    if (!designJob) {
      return;
    }
    fetch("/api/design/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job: designJob })
    }).catch(function () {});
  }

  function applyDesign() {
    if (!designResult) {
      return;
    }
    Object.keys(designResult).forEach(function (key) {
      if (inputs[key]) {
        inputs[key].value = String(designResult[key]);
      }
    });
    onEdit();
    // The confirming run: the numbers shown are measured, never remembered
    // from the search.
    run();
  }


  /* The whole story on one button: seed a design from the spec, measure it,
   * and only if it falls short, iterate until it does not. */
  function generateDesign() {
    if (!current.design || !current.design.seeded || !validate()) {
      return;
    }
    show(designError, false);
    show(designReason, false);
    show(designApply, false);

    var targets = collectTargets();
    if (targets === null) {
      return;
    }

    designGenerate.disabled = true;
    designStart.disabled = true;
    designGenerateLabel.textContent = "Generating";

    fetch("/api/design/seed", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        circuit: current.id,
        params: values(),
        targets: targets
      })
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) {
            throw new Error(payload && payload.error
              ? payload.error
              : "The server refused the seed request.");
          }
          // The generated circuit appears in the form and the schematic.
          Object.keys(payload.params).forEach(function (key) {
            if (inputs[key]) {
              inputs[key].value = String(payload.params[key]);
            }
          });
          onEdit();
          designGenerateLabel.textContent = "Measuring the generated design";
          return run();
        });
      })
      .then(function () {
        if (lastResult && meetsTargets(lastResult, targets)) {
          designGenerate.disabled = false;
          designStart.disabled = false;
          designGenerateLabel.textContent = "Generate design from specs";
          designReason.textContent =
            "The generated design meets every target as measured. No " +
            "iteration was needed.";
          show(designReason, true);
          return;
        }
        designGenerateLabel.textContent = "Iterating toward the spec";
        startDesign("generate");
      })
      .catch(function (error) {
        designIdle();
        designShowError(String(error.message || error));
      });
  }

  designGenerate.addEventListener("click", generateDesign);
  designStart.addEventListener("click", function () { startDesign("manual"); });
  designStop.addEventListener("click", stopDesign);
  designApply.addEventListener("click", applyDesign);

  /* ---- selection --------------------------------------------------------- */

  function select(circuitId) {
    if (current) {
      memory[current.id] = values();
    }

    for (var i = 0; i < catalogue.length; i++) {
      if (catalogue[i].id === circuitId) {
        current = catalogue[i];
        break;
      }
    }

    id("panel-title").textContent =
      current.analysis === "dc" ? "DC operating point" : "AC sweep";
    id("caption").textContent = current.caption;

    renderTabs();
    renderPresets();
    renderInputs(memory[current.id]);
    renderDesignPanel();
    validate();
    dismissError();
    clearResult();
    redraw(null, false);

    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", "#" + current.id);
    }
  }

  function known(circuitId) {
    return catalogue.some(function (circuit) {
      return circuit.id === circuitId;
    });
  }

  window.addEventListener("hashchange", function () {
    var wanted = window.location.hash.replace("#", "");
    if (known(wanted) && wanted !== current.id) {
      select(wanted);
    }
  });

  form.addEventListener("submit", run);

  async function start() {
    try {
      var response = await fetch("/api/circuits");
      catalogue = (await response.json()).circuits;
    } catch (networkError) {
      id("panel-title").textContent = "Unavailable";
      showError("Could not load the circuit catalogue. Start the server with " +
                "python server.py and reload.");
      return;
    }

    var wanted = window.location.hash.replace("#", "");
    select(known(wanted) ? wanted : catalogue[0].id);
  }

  start();
})(window, document);
