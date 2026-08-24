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
    nfet_cs_amp: "drawNfetCsAmp"
  };

  /* Which measured value the schematic tags its output node with, and the
   * argument name each compose function expects it under. */
  var SCHEMATIC_TAG = {
    divider: "vout",
    rc_lowpass: "f3db",
    rc_highpass: "f3db",
    rlc_bandpass: "f0_measured",
    inverting_amp: "midband_db",
    nfet_cs_amp: "midband_db"
  };

  var TAG_ARG = {
    divider: "vout",
    rc_lowpass: "f3db",
    rc_highpass: "f3db",
    rlc_bandpass: "f0",
    inverting_amp: "gain_db",
    nfet_cs_amp: "gain_db"
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
