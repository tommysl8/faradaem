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
    opamp_two_stage: "drawOpampTwoStage",
    ota_5t: "drawOta5t",
    folded_cascode: "drawFoldedCascode"
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
    opamp_two_stage: "phase_margin",
    ota_5t: "phase_margin",
    folded_cascode: "phase_margin"
  };

  var TAG_ARG = {
    divider: "vout",
    rc_lowpass: "f3db",
    rc_highpass: "f3db",
    rlc_bandpass: "f0",
    inverting_amp: "gain_db",
    twopole_amp: "phase_margin",
    nfet_cs_amp: "gain_db",
    opamp_two_stage: "phase_margin",
    ota_5t: "phase_margin",
    folded_cascode: "phase_margin"
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

  /* Which Faradaem this is, decided before the page was written and
     stamped on <html> by spice/deployment.py. Read here on the first line
     that runs, so nothing renders a capability this deployment lacks and
     then takes it back. Never inferred from a failed request. */
  var MODE = document.documentElement.getAttribute("data-deployment");
  var isStatic = MODE === "static";

  /* Every request to Faradaem's own server goes through here, and there
     is no other way to make one. In the static deployment there is no
     server: a request to /api could only 404, and a page that asks is a
     page that does not know what it is. So this refuses.

     One door rather than a guard at each of sixteen call sites, because
     "no /api request from the published site" is then a property a test
     can check by reading this file, instead of a claim about sixteen
     code paths that someone has to re-audit every time one is added. */
  function api(path, options) {
    if (isStatic) {
      return Promise.reject(new Error(
        "The published demo has no simulator behind it, so " + path
        + " was not requested."));
    }
    return fetch(path, options);
  }

  window.FaradaemApi = api;

  function id(name) {
    return document.getElementById(name);
  }

  /* Bind a handler, or do nothing if the control is not in this
     deployment's document. Same reasoning as show(): the server-only
     controls are deleted at build time, so asking to listen to one is
     asking about something that legitimately is not here. Returns whether
     it bound, for the few callers that care. */
  function on(target, event, handler) {
    var node = typeof target === "string" ? id(target) : target;
    if (node) {
      node.addEventListener(event, handler);
    }
    return Boolean(node);
  }

  /* show/clear tolerate a missing element on purpose. The server-only
     controls are not in the static document at all -- deleted at build
     time, not hidden -- so code that arranges them is asking about
     something that legitimately does not exist here. Doing nothing is the
     right answer, and it is the only one that does not need every caller
     to know which deployment it is in. */
  function show(element, visible) {
    if (element) {
      element.classList.toggle("hidden", !visible);
    }
  }

  function clear(node) {
    if (!node) {
      return;
    }
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function text(element, value) {
    if (element) {
      element.textContent = value;
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

  /* The previous measurement per circuit, for saying what an edit changed.
     A neutral fact in the accent colour: whether a bigger number is better
     depends on which number it is, and the page does not pretend to know. */
  var lastMeasured = {};
  // Which circuit and which values lastResult was measured on: the pair
  // that keeps a measurement from being attributed to newer form text.
  var lastResultCircuit = null;
  var lastResultParams = null;

  function deltaText(before, now, spec) {
    if (typeof before !== "number" || typeof now !== "number"
        || before === now) {
      return null;
    }
    var change = now - before;
    var sign = change > 0 ? "+" : "\u2212";
    // Formatted the way the number itself is: half a decibel is 0.50 dB,
    // never 500 mdB.
    return sign + present(Math.abs(change), spec);
  }

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

  /* ---- what the page remembers between visits ------------------------------
   * Editing six values, reloading, and finding the defaults back is a small
   * cruelty that a browser has had the means to avoid since 2009. So the
   * last VALID sizing per circuit is kept, namespaced and versioned, and
   * restored on arrival.
   *
   * Valid is the load-bearing word. Half-typed text is not a design: while
   * someone clears a field to retype it the form holds "", and persisting
   * that would mean a reload restoring an empty box instead of the numbers
   * they had. Only a sizing where every value parses and sits inside its
   * declared range replaces what is stored.
   *
   * Storage can fail for reasons that are none of the page's business --
   * a private window, site data blocked, a full quota. Every access is
   * wrapped, and every failure means "no memory", never a broken page.
   */

  //: Namespaced, and versioned in the key itself: a future format change
  //: writes a new key rather than trying to understand an old one, and the
  //: old key ages out with the browser's own storage.
  var STORE_KEY = "faradaem.designs.v1";

  function readStore() {
    var raw;
    try {
      raw = window.localStorage.getItem(STORE_KEY);
    } catch (blocked) {
      return {};
    }
    if (!raw) {
      return {};
    }
    var parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (corrupt) {
      return {};
    }
    // Anything that is not the shape written here is treated as absent.
    // Corrupt storage must never be able to break the page that reads it.
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)
        || parsed.version !== 1 || !parsed.circuits
        || typeof parsed.circuits !== "object"
        || Array.isArray(parsed.circuits)) {
      return {};
    }
    return parsed.circuits;
  }

  function writeStore(circuits) {
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify({
        version: 1,
        circuits: circuits
      }));
    } catch (blocked) {
      // No memory this session. Everything else still works.
    }
  }

  /* The stored sizing for a circuit, dropped entirely unless every key the
     circuit declares is a finite number inside its range. A catalogue that
     gained or retightened a parameter since the write invalidates it, which
     is the migration: stale shapes are ignored, never coerced. */
  function storedFor(circuit) {
    var all = readStore();
    var saved = all[circuit.id];
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) {
      return null;
    }
    var out = {};
    var ok = true;
    circuit.params.forEach(function (spec) {
      var value = saved[spec.key];
      if (typeof value !== "number" || !isFinite(value)
          || value < spec.min || value > spec.max) {
        ok = false;
        return;
      }
      out[spec.key] = value;
    });
    return ok ? out : null;
  }

  /* Persist the sizing on the page, if it is one. Called after every edit,
     preset, import and reset; silently declines while the form is mid-typo. */
  function remember() {
    if (!current || !Object.keys(inputs).length) {
      return false;
    }
    var sizing = {};
    var ok = true;
    current.params.forEach(function (spec) {
      var input = inputs[spec.key];
      if (!input) {
        ok = false;
        return;
      }
      var value = window.parseEngineering(input.value);
      if (typeof value !== "number" || !isFinite(value)
          || value < spec.min || value > spec.max) {
        ok = false;
        return;
      }
      sizing[spec.key] = value;
    });
    if (!ok) {
      return false;
    }
    var all = readStore();
    all[current.id] = sizing;
    writeStore(all);
    return true;
  }

  function forget(circuitId) {
    var all = readStore();
    if (all[circuitId] !== undefined) {
      delete all[circuitId];
      writeStore(all);
    }
  }

  window.FaradaemStore = {
    KEY: STORE_KEY,
    read: readStore,
    write: writeStore,
    forCircuit: storedFor,
    remember: remember,
    forget: forget
  };

  /* ---- reading and validating the form ------------------------------- */

  function values() {
    var out = {};
    Object.keys(inputs).forEach(function (key) {
      out[key] = window.parseEngineering(inputs[key].value);
    });
    return out;
  }

  function complain(spec, value) {
    if (inputs[spec.key].value.trim() === "" || !isFinite(value)) {
      return spec.label + " needs a number. Engineering suffixes work: "
        + "10k, 2.2u, 5meg.";
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
      var problem = complain(input.spec, window.parseEngineering(input.value));
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
    var next = stepValue(window.parseEngineering(input.value) || 0,
                         event.key === "ArrowUp" ? 1 : -1,
                         event.shiftKey);
    next = Math.min(spec.max, Math.max(spec.min, next));

    input.value = String(next);
    onEdit();
  }

  /* ---- rendering the panel -------------------------------------------- */

  /* One small glyph per circuit family, drawn like the schematics are:
     strokes, no fills, meaning over ornament. A chip you can recognise
     before you read it. */
  var GLYPHS = {
    divider: "M8 1v3 M5 4h6l-1.5 2h3l-1.5 2h3l-1.5 2h3L14 12 M8 12v3",
    rc_lowpass: "M1 8h4l1-2 2 4 2-4 1 2h4 M11 8v4 M13 8v4",
    rc_highpass: "M1 8h3 M6 5v6 M8 5v6 M8 8h2l1-2 2 4 1-2",
    rlc_bandpass: "M1 8h2l1-2 2 4 1-2h1a2 2 0 014 0h1 M13 6v4 M15 6v4",
    inverting_amp: "M3 3v10l9-5z M3 5H1 M3 11H1 M12 8h3",
    twopole_amp: "M2 3v10l8-5z M10 6l4-1 M10 10l4 1",
    nfet_cs_amp: "M4 3v10 M6 5v6 M6 6h6v-3 M6 10h6v3 M1 8h3",
    opamp_two_stage: "M2 3v10l7-5z M9 5l5-2v10l-5-2 M2 5H1 M2 11H1",
    ota_5t: "M3 3v10l9-5z M3 5H1 M3 11H1 M12 8h3 M13 6v4",
    folded_cascode: "M2 4v8 M4 5v6 M4 6h4v-3h4 M4 10h4v3h4 M12 3v3 M12 10v3",
  };

  //: The one panel the circuit tabs control. Stable, so aria-controls on
  //: every tab and aria-labelledby on the panel can point at each other.
  var CIRCUIT_PANEL_ID = "circuit-panel";

  function renderTabs() {
    clear(modesEl);
    tabs = catalogue.map(function (circuit) {
      var tab = el("button", "mode");
      if (GLYPHS[circuit.id]) {
        var glyph = document.createElementNS("http://www.w3.org/2000/svg",
                                             "svg");
        glyph.setAttribute("viewBox", "0 0 16 16");
        glyph.setAttribute("aria-hidden", "true");
        var stroke = document.createElementNS(
          "http://www.w3.org/2000/svg", "path");
        stroke.setAttribute("d", GLYPHS[circuit.id]);
        glyph.appendChild(stroke);
        tab.appendChild(glyph);
      }
      tab.appendChild(document.createTextNode(circuit.name));
      var active = circuit.id === current.id;
      tab.type = "button";
      tab.id = "mode-tab-" + circuit.id;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", active ? "true" : "false");
      // Every tab controls the one panel below the strip, which is what
      // makes this a tablist rather than ten unrelated buttons.
      tab.setAttribute("aria-controls", CIRCUIT_PANEL_ID);
      // Roving tabindex: Tab reaches the strip once, arrows move within
      // it. Ten circuits should not cost ten presses to walk past.
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

    var panel = id(CIRCUIT_PANEL_ID);
    if (panel && current) {
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", "mode-tab-" + current.id);
    }
  }

  /* Arrow keys move focus AND select, which is the behaviour the pattern
     prescribes for a tablist whose panels are already loaded: a reader
     arrowing along the strip hears each circuit as they reach it, rather
     than hearing nothing until they guess that Enter is needed. Enter and
     Space still work, for anyone who expects them to. Home and End go to
     the ends, and the ends wrap. */
  function focusTab(index) {
    var next = (index + tabs.length) % tabs.length;
    tabs.forEach(function (tab, i) {
      tab.tabIndex = i === next ? 0 : -1;
    });
    var wanted = tabs[next].dataset.circuit;
    if (wanted !== current.id) {
      // select() re-renders the strip, so the element to focus is the one
      // that exists afterwards, not the one captured before.
      select(wanted);
    }
    var landed = tabs[next];
    if (landed) {
      landed.focus();
    }
  }

  modesEl.addEventListener("keydown", function (event) {
    var index = tabs.indexOf(document.activeElement);
    if (index < 0) {
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      focusTab(index);
      return;
    }

    var target = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      target = index + 1;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      target = index - 1;
    } else if (event.key === "Home") {
      target = 0;
    } else if (event.key === "End") {
      target = tabs.length - 1;
    } else {
      return;
    }

    event.preventDefault();
    focusTab(target);
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
        remember();
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
      // Text, not number: engineers write "10k" and "2.2u", and a number
      // input refuses the letters before the parser can read them. The
      // echo beside the label shows what the entry was read as.
      input.type = "text";
      input.value = String(preset && preset[spec.key] !== undefined
        ? preset[spec.key]
        : spec.default);
      input.autocomplete = "off";
      input.spellcheck = false;
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

    // A circuit can reach the catalogue before anyone has drawn it. Say so
    // rather than throwing: the numbers are still real, and a missing
    // picture should cost the reader a caption, not the whole panel.
    var drawer = window[DRAWERS[current.id]];
    var schematicFigure = id("schematic").parentNode.parentNode;
    if (typeof drawer !== "function") {
      clear(id("schematic"));
      show(schematicFigure, false);
      naturalView = null;
    } else {
      show(schematicFigure, true);
      drawer(id("schematic"), args);
      var drawn = id("schematic").viewBox.baseVal;
      naturalView = { w: drawn.width, h: drawn.height };
      fitSchematic();
    }

    var isAc = current.analysis === "ac";
    show(bodePanel, isAc);
    if (isAc && bodePanel) {
      lastBode = result ? bodeData(result, animate) : {};
      // The held design's response rides under the live curve.
      if (heldDesign && heldDesign.circuit === current.id
          && heldDesign.bode) {
        lastBode.ghost = heldDesign.bode;
      }
      window.drawBode(id("bode"), lastBode);
      lastBode = Object.assign({}, lastBode, { animate: false });
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
    text(id("headline-label"), headline.label);
    var value = id("headline-value");
    if (value) {
      value.textContent = "—";
      value.classList.add("placeholder");
    }
    show(id("headline-check"), false);
    show(id("headline-badge"), false);
    show(id("note"), false);
    clear(statsEl);
    show(statsEl, false);
    text(captionState, isStatic
      ? "Measuring needs the local app."
      : "Run to measure.");
  }

  function renderResult(result) {
    var readout = current.readout;
    var headline = readout.headline;
    var analytic = result.analytic || {};

    text(id("headline-label"), headline.label);
    text(id("headline-value"), present(result[headline.key], headline));
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
      text(id("headline-check-label"), check.label);
      text(id("headline-check-value"), present(expected, headline));
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
    text(id("note"), noteText);
    show(id("note"), Boolean(noteText));

    var previous = lastMeasured[current.id] || {};
    var headlineDelta = deltaText(previous[headline.key],
                                  result[headline.key], headline);
    if (headlineDelta) {
      id("headline-value").appendChild(el("span", "delta", headlineDelta));
    }

    clear(statsEl);
    readout.stats.forEach(function (stat) {
      var cell = el("div");
      cell.appendChild(el("span", "stat-label", stat.label));
      cell.appendChild(el("span", "stat-value", present(result[stat.key], stat)));
      var moved = deltaText(previous[stat.key], result[stat.key], stat);
      if (moved) {
        cell.appendChild(el("span", "delta", moved));
      }

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

    // Remember what was measured, so the next run can say what changed.
    var kept = {};
    [headline].concat(readout.stats).forEach(function (spec) {
      if (typeof result[spec.key] === "number") {
        kept[spec.key] = result[spec.key];
      }
    });
    lastMeasured[current.id] = kept;

    // And the bench hears it.
    benchSet("sim", "pass", present(result[headline.key], headline));

    // A number worth keeping can be pinned where it landed.
    show(id("pin-row"), true);
    text(id("pin-note"), "");
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

  on("error-dismiss", "click", dismissError);

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !errorEl.classList.contains("hidden")) {
      dismissError();
    }
  });

  /* ---- the netlist viewer ------------------------------------------------- */

  var netlistToggle = id("netlist-toggle");
  var netlistCopy = id("netlist-copy");
  var netlistView = id("netlist-view");
  var netlistShown = false;

  function hideNetlist() {
    netlistShown = false;
    show(netlistView, false);
    show(netlistCopy, false);
    text(netlistToggle, "View netlist");
  }

  function refreshNetlist() {
    if (!validate()) {
      return;
    }
    api("/api/netlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ circuit: current.id, params: values() })
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) {
            throw new Error(payload && payload.error
              ? payload.error : "Could not build the netlist.");
          }
          netlistView.textContent = payload.netlist;
          show(netlistView, true);
          show(netlistCopy, true);
          netlistShown = true;
          netlistToggle.textContent = "Hide netlist";
        });
      })
      .catch(function (error) {
        netlistView.textContent = String(error.message || error);
        show(netlistView, true);
        // An error message is not a netlist: no copy button for it.
        show(netlistCopy, false);
        netlistShown = true;
        netlistToggle.textContent = "Hide netlist";
      });
  }

  if (netlistToggle) {
    on(netlistToggle, "click", function () {
      if (netlistShown) {
        hideNetlist();
      } else {
        refreshNetlist();
      }
    });
  }

  /* ---- running ----------------------------------------------------------- */

  function setPending(pending) {
    // Nothing here exists in the static build, where nothing can pend.
    if (!runButton) {
      return;
    }
    runButton.disabled = pending;
    text(runLabel, pending ? "Running" : "Run simulation");

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
      if (node) {
        node.classList.toggle("is-loading", pending);
      }
    });
  }

  function onEdit() {
    validate();
    clearResult();
    dismissError();
    redraw(null, false);
    memory[current.id] = values();
    // Only a complete, in-range sizing is written; a half-typed field
    // leaves the last good one standing.
    remember();
    if (netlistShown) {
      refreshNetlist();
    }
    // An edited sizing outdates anything measured at the old one.
    show(id("pin-row"), false);
    show(id("triage-line"), false);
    show(id("ab-block"), false);
    biasReset();
    panels.forEach(function (panel) {
      if (panel.onValuesEdited) {
        panel.onValuesEdited();
      }
    });
  }

  async function run(event) {
    if (isStatic) {
      return null;
    }
    if (event) {
      event.preventDefault();
    }
    if (!validate()) {
      // The inline messages already say what to fix; do not also shout.
      return null;
    }

    // The measurement belongs to the circuit AND the values it was
    // started with. If the user switches tabs or keeps typing while
    // ngspice works, the response is dropped or paired with what was
    // actually simulated, never with the form's newer text.
    var ranCircuit = current.id;
    var ranParams = values();
    var measured = null;
    setPending(true);
    captionState.textContent = 'Measuring';
    tickStart(captionState);
    benchSet("sim", "run");
    try {
      var response = await api("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ circuit: ranCircuit, params: ranParams })
      });

      var payload = null;
      try {
        payload = await response.json();
      } catch (parseError) {
        payload = null;
      }

      if (current.id !== ranCircuit) {
        return null;
      }
      if (!response.ok) {
        showError(messageFor(response, payload));
      } else if (!payload || typeof payload[current.readout.headline.key] !== "number") {
        showError("The server did not return a usable measurement. Run it again.");
      } else {
        dismissError();
        lastResult = payload;
        lastResultCircuit = ranCircuit;
        lastResultParams = ranParams;
        measured = payload;
        renderResult(payload);
        redraw(payload, true);
        biasOffer();
        historyRecord(ranParams,
                      present(payload[current.readout.headline.key],
                              current.readout.headline));
        abRender();
      }
    } catch (networkError) {
      if (current.id === ranCircuit) {
        showError("Could not reach the Faradaem server. Start it with python " +
                  "server.py and run again.");
      }
    } finally {
      setPending(false);
      if (current.id === ranCircuit) {
        // This run's own outcome decides the cleanup, never a remembered
        // success: a failed run must not leave the bench claiming work.
        tickStop(captionState, measured ? "" : "Run to measure.");
        if (!measured) {
          benchSet("sim", "idle");
        }
      } else {
        tickStop(captionState, null);
      }
    }
    return measured;
  }


  /* ---- two designs side by side -------------------------------------------
   * Hold the measured design as A and keep working: every later
   * measurement renders the delta table against it, knob by knob and
   * number by number, and A's frequency response stays under the live
   * curve as a ghost. The spreadsheet engineers keep by hand, kept by
   * the page instead. */

  var heldDesign = null;

  function abDelta(a, b) {
    if (typeof a !== "number" || typeof b !== "number" || a === 0) {
      return "—";
    }
    var percent = ((b - a) / Math.abs(a)) * 100;
    if (Math.abs(percent) < 0.05) {
      return "same";
    }
    return (percent >= 0 ? "+" : "") + percent.toFixed(1) + "%";
  }

  function abRender() {
    var block = id("ab-block");
    if (!heldDesign || heldDesign.circuit !== current.id || !lastResult
        || lastResultCircuit !== current.id) {
      show(block, false);
      return;
    }
    text(id("ab-head"), "Against design A, held at " + heldDesign.headline);
    var host = id("ab-table");
    clear(host);
    var table = el("table", "sheet-table");
    var head = el("tr");
    ["", "A, held", "B, on the bench", "change"].forEach(function (text) {
      head.appendChild(el("th", null, text));
    });
    table.appendChild(head);

    // B is the design that was measured, never the form's newer text.
    var now = lastResultParams || values();
    current.params.forEach(function (spec) {
      var a = heldDesign.params[spec.key];
      var b = now[spec.key];
      if (a === undefined) {
        return;
      }
      var row = el("tr");
      row.appendChild(el("td", null, spec.label || spec.key));
      row.appendChild(el("td", "num",
        window.formatEngineering(a, spec.unit || "")));
      row.appendChild(el("td", "num",
        window.formatEngineering(b, spec.unit || "")));
      row.appendChild(el("td", "num delta", abDelta(a, b)));
      table.appendChild(row);
    });

    [current.readout.headline].concat(current.readout.stats || [])
      .forEach(function (metric) {
        var a = heldDesign.measured[metric.key];
        var b = lastResult[metric.key];
        if (typeof a !== "number" || typeof b !== "number") {
          return;
        }
        var row = el("tr", "is-summary");
        row.appendChild(el("td", null, metric.label));
        row.appendChild(el("td", "num", present(a, metric)));
        row.appendChild(el("td", "num", present(b, metric)));
        row.appendChild(el("td", "num delta", abDelta(a, b)));
        table.appendChild(row);
      });
    host.appendChild(table);
    text(id("ab-note"), current.analysis === "ac"
      ? "A's frequency response is the faint trace on the plot. Both "
        + "columns were measured on this machine."
      : "Both columns were measured on this machine.");
    show(block, true);
  }

  on("ab-hold", "click", function () {
    if (!lastResult) {
      return;
    }
    var slim = {};
    [current.readout.headline].concat(current.readout.stats || [])
      .forEach(function (metric) {
        if (typeof lastResult[metric.key] === "number") {
          slim[metric.key] = lastResult[metric.key];
        }
      });
    heldDesign = {
      circuit: current.id,
      params: lastResultParams || values(),
      measured: slim,
      headline: present(lastResult[current.readout.headline.key],
                        current.readout.headline),
      bode: lastResult.freq ? { freq: lastResult.freq,
                                mag_db: lastResult.mag_db,
                                phase_deg: lastResult.phase_deg } : null
    };
    abRender();
  });

  on("ab-release", "click", function () {
    heldDesign = null;
    abRender();
    if (lastResult) {
      redraw(lastResult, false);
    }
  });

  /* ---- a design as one file -----------------------------------------------
   * "Send me your setup" answered with a file: circuit, sizing, what it
   * measured here, and where it came from. Importing selects the circuit,
   * loads the sizing, and measures it again on this machine, because the
   * numbers a design shows must come from the simulator in front of you,
   * never from the sender's. */

  function exportDesign() {
    var payload = {
      faradaem_design: 1,
      app_version: getComputedStyle(document.documentElement)
        .getPropertyValue("--app-version").replace(/"/g, "").trim(),
      circuit: current.id,
      name: current.name,
      params: values(),
      measured: null,
      exported_utc: new Date().toISOString()
    };
    // Measured numbers travel only with the sizing they were measured
    // on: same circuit, same values. An edited form exports its sizing
    // with measured null, which is the truth about it.
    if (lastResult && lastResultCircuit === current.id
        && JSON.stringify(lastResultParams) === JSON.stringify(values())) {
      var slim = {};
      var keys = [current.readout.headline.key];
      (current.readout.stats || []).forEach(function (stat) {
        keys.push(stat.key);
      });
      keys.forEach(function (key) {
        if (typeof lastResult[key] === "number") {
          slim[key] = lastResult[key];
        }
      });
      payload.measured = slim;
    }
    var url = URL.createObjectURL(new Blob(
      [JSON.stringify(payload, null, 1)], { type: "application/json" }));
    var link = document.createElement("a");
    link.href = url;
    link.download = current.id + "-design.json";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  function shareNote(text) {
    var note = id("share-note");
    note.textContent = text;
    show(note, Boolean(text));
  }

  /* ---- import validation --------------------------------------------------
   * The deciding is in static/import-validate.js, which knows nothing about
   * the DOM and can therefore be exercised by a test without a browser.
   * What is here is the wiring: hand it the catalogue and this build's
   * version, and mutate nothing until it says yes.
   */

  var IMPORT = window.FaradaemImport;
  var IMPORT_MAX_BYTES = IMPORT ? IMPORT.MAX_BYTES : 256 * 1024;

  function validateImportedDesign(text, options) {
    if (!IMPORT) {
      return { ok: false, error: "The import validator did not load. "
        + "Reload the page." };
    }
    var settings = options || {};
    return IMPORT.validate(text, {
      bytes: settings.bytes,
      catalogue: catalogue,
      appVersion: getComputedStyle(document.documentElement)
        .getPropertyValue("--app-version").replace(/"/g, "").trim()
    });
  }

  function importError(message) {
    var box = id("import-error");
    if (!box) {
      return;
    }
    box.textContent = message;
    show(box, true);
    // role="alert" announces it; focus makes it findable by keyboard,
    // which matters because the file dialog took focus away.
    if (box.focus) {
      box.focus();
    }
  }

  function clearImportError() {
    show(id("import-error"), false);
    text(id("import-error"), "");
  }

  function applyImportedDesign(result) {
    var design = result.design;
    clearImportError();

    if (design.circuit !== current.id) {
      select(design.circuit);
    }
    Object.keys(design.params).forEach(function (key) {
      if (inputs[key]) {
        inputs[key].value = String(design.params[key]);
      }
    });
    onEdit();
    remember();

    var what = design.name || design.circuit;
    var when = design.exported_utc
      ? ", exported " + design.exported_utc.slice(0, 10) : "";
    var extra = result.warnings.length ? " " + result.warnings.join(" ") : "";

    if (isStatic) {
      shareNote("Imported " + what + when + ". This static demo cannot "
        + "measure it; run locally to simulate." + extra);
      return;
    }
    shareNote("Imported " + what + when + ". Measuring it here, because a "
      + "design's numbers must come from the simulator in front of you."
      + extra);
    run();
  }

  function importDesign(file) {
    // Size is refused off the file handle, before a byte is read.
    if (file.size > IMPORT_MAX_BYTES) {
      importError("That file is " + Math.round(file.size / 1024) + " kB. A "
        + "Faradaem design is under "
        + Math.round(IMPORT_MAX_BYTES / 1024) + " kB, so this is not one.");
      return Promise.resolve(false);
    }
    return file.text().then(function (text) {
      var result = validateImportedDesign(text, { bytes: file.size });
      if (!result.ok) {
        importError(result.error);
        return false;
      }
      applyImportedDesign(result);
      return true;
    }).catch(function (readError) {
      importError("That file could not be read: "
        + String(readError && readError.message ? readError.message
                 : readError));
      return false;
    });
  }

  on("design-export", "click", exportDesign);

  /* Reset asks nothing. It affects one circuit, the values are visible
     before and after, and the registry defaults it restores are one click
     away in the examples anyway; a confirmation would be ceremony over a
     reversible change. */
  on("design-reset", "click", function () {
    delete memory[current.id];
    clearImportError();
    renderInputs(null);
    onEdit();
    // After onEdit, which would otherwise persist the defaults straight
    // back. Forgetting last leaves nothing stored for this circuit, which
    // is what "reset" means.
    forget(current.id);
    shareNote(current.name + " is back to its catalogue defaults.");
  });

  on("design-import", "click", function () {
    clearImportError();
    id("design-import-file").click();
  });
  on("design-import-file", "change", function () {
    var file = this.files && this.files[0];
    if (file) {
      importDesign(file);
    }
    this.value = "";
  });

  /* ---- long jobs report back ----------------------------------------------
   * Corners, Monte Carlo, the datasheet and the design search take
   * minutes; people switch tabs and forget. The first time a long job
   * starts, the browser asks once whether it may say "done"; after that,
   * a finished job notifies only when the tab is hidden. */

  window.FaradaemNotify = {
    ask: function () {
      if (window.Notification && Notification.permission === "default") {
        try {
          Notification.requestPermission();
        } catch (ignored) { /* an old browser without the promise form */ }
      }
    },
    done: function (body) {
      if (window.Notification && Notification.permission === "granted"
          && document.hidden) {
        try {
          new Notification("Faradaem", { body: body });
        } catch (ignored) { /* notification construction can throw on some platforms */ }
      }
    }
  };

  /* ---- run history --------------------------------------------------------
   * Every measured sizing this session is a place you can walk back to:
   * "what did I have before lunch" answered with the arrow chips. Walking
   * restores the values into the form; measuring again is your click,
   * because a restore must never spend a simulation on its own. */

  var runHistory = {};
  var runHistoryAt = {};

  function historyList() {
    if (!runHistory[current.id]) {
      runHistory[current.id] = [];
    }
    return runHistory[current.id];
  }

  function historyRender() {
    // Session history needs measurements, which this deployment cannot make.
    if (!id("history-row")) {
      return;
    }
    var list = historyList();
    var at = runHistoryAt[current.id];
    if (typeof at !== "number" || at >= list.length) {
      at = list.length - 1;
      runHistoryAt[current.id] = at;
    }
    show(id("history-row"), list.length > 1);
    if (list.length < 2) {
      return;
    }
    var entry = list[at];
    text(id("history-pos"), (at + 1) + " of " + list.length
      + (entry && entry.headline ? " · " + entry.headline : ""));
    id("history-prev").disabled = at <= 0;
    id("history-next").disabled = at >= list.length - 1;
  }

  function historyRecord(params, headline) {
    var list = historyList();
    var snapshot = JSON.stringify(params);
    if (list.length && JSON.stringify(list[list.length - 1].params)
        === snapshot) {
      // The same sizing measured again is one place, not two.
      runHistoryAt[current.id] = list.length - 1;
      historyRender();
      return;
    }
    list.push({ params: params, headline: headline });
    if (list.length > 50) {
      list.shift();
    }
    runHistoryAt[current.id] = list.length - 1;
    historyRender();
  }

  function historyGo(direction) {
    var list = historyList();
    var at = runHistoryAt[current.id] + direction;
    if (at < 0 || at >= list.length) {
      return;
    }
    runHistoryAt[current.id] = at;
    var entry = list[at];
    Object.keys(entry.params).forEach(function (key) {
      if (inputs[key]) {
        inputs[key].value = String(entry.params[key]);
      }
    });
    onEdit();
    historyRender();
  }

  on("history-prev", "click", function () { historyGo(-1); });
  on("history-next", "click", function () { historyGo(1); });

  /* ---- bias annotations --------------------------------------------------
   * What an engineer pencils onto a printed schematic: Id, gm, Vgs, Vds,
   * Vdsat and headroom beside every device. One simulation fetches all of
   * it at the bench's own operating point; hovering a transistor shows its
   * numbers. Edited values outdate the annotations, so they clear. */

  var biasChip = id("bias-chip");
  var biasData = null;
  var biasTip = null;
  // Bumped by every reset: a bias response landing after an edit or a
  // circuit switch describes an operating point the page no longer
  // shows, and is dropped.
  var biasSeq = 0;

  function biasReset() {
    // The bias chip is a local-only control; nothing to reset without it.
    if (!biasChip) {
      return;
    }
    biasSeq += 1;
    biasData = null;
    tickStop(biasChip, null);
    show(biasChip, false);
    biasChip.disabled = false;
    biasChip.textContent = "Annotate bias (1 simulation)";
    id("schematic").classList.remove("bias-armed");
    if (biasTip) {
      show(biasTip, false);
    }
  }

  function biasOffer() {
    if (current.pdk && !isStatic) {
      show(biasChip, true);
    }
  }

  function biasRow(label, value, unit) {
    var row = "<span class=\"k\">" + label + "</span><span class=\"v\">";
    if (typeof value === "number" && isFinite(value)) {
      row += window.formatEngineering(value, unit);
    } else {
      row += "not exposed";
    }
    return row + "</span>";
  }

  function biasTipHtml(name, slot) {
    var html = "<strong>" + name + "</strong><div class=\"bias-grid\">";
    html += biasRow("Id", slot.id, "A");
    html += biasRow("gm", slot.gm, "S");
    html += biasRow("Vgs", slot.vgs, "V");
    html += biasRow("Vds", slot.vds, "V");
    html += biasRow("Vdsat", slot.vdsat, "V");
    var headroom = slot.headroom;
    html += "<span class=\"k\">headroom</span><span class=\"v ";
    if (typeof headroom === "number" && isFinite(headroom)) {
      html += (headroom < 0 ? "is-bad" : (headroom < 0.1 ? "is-thin" : "is-ok"));
      html += "\">" + (headroom * 1000).toFixed(0) + " mV";
      html += headroom < 0 ? " (out of saturation)" : "";
    } else {
      html += "\">not exposed";
    }
    return html + "</span></div>";
  }

  function biasShow(group) {
    var name = group.getAttribute("data-device");
    if (!biasData || biasData.circuit !== current.id
        || !biasData.devices[name]) {
      return;
    }
    if (!biasTip) {
      biasTip = el("div", "bias-tip hidden");
      id("schematic").parentNode.appendChild(biasTip);
    }
    biasTip.innerHTML = biasTipHtml(name, biasData.devices[name]);
    var host = id("schematic").parentNode.getBoundingClientRect();
    var box = group.getBoundingClientRect();
    biasTip.style.left = Math.max(4, box.right - host.left + 8) + "px";
    biasTip.style.top = Math.max(4, box.top - host.top - 4) + "px";
    show(biasTip, true);
  }

  function biasFetch() {
    var seq = biasSeq;
    biasChip.disabled = true;
    biasChip.textContent = "Measuring bias";
    tickStart(biasChip);
    api("/api/bias", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ circuit: current.id, params: values() })
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          return { ok: response.ok, payload: payload };
        });
      })
      .then(function (got) {
        if (seq !== biasSeq) {
          // An edit or a switch reset the annotations while this
          // measured; its answer describes a page that is gone.
          return;
        }
        tickStop(biasChip, null);
        if (!got.ok) {
          biasChip.disabled = false;
          biasChip.textContent = "Annotate bias (1 simulation)";
          showError((got.payload && got.payload.error)
                    || "The bias measurement failed. Run it again.");
          return;
        }
        biasData = { circuit: got.payload.circuit,
                     devices: got.payload.devices };
        biasChip.textContent = "Bias measured. Hover a device.";
        biasChip.disabled = true;
        id("schematic").classList.add("bias-armed");
      })
      .catch(function () {
        if (seq !== biasSeq) {
          return;
        }
        tickStop(biasChip, null);
        biasChip.disabled = false;
        biasChip.textContent = "Annotate bias (1 simulation)";
        showError("Could not reach the Faradaem server for the bias run.");
      });
  }

  on(biasChip, "click", biasFetch);
  on("schematic", "mouseover", function (event) {
    var group = event.target.closest && event.target.closest("g[data-device]");
    if (group) {
      biasShow(group);
    }
  });
  on("schematic", "mouseout", function (event) {
    if (biasTip && event.target.closest
        && event.target.closest("g[data-device]")) {
      show(biasTip, false);
    }
  });

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
  var designStarted = null;

  function designIdle() {
    if (designTimer) {
      clearTimeout(designTimer);
      designTimer = null;
    }
    if (designJob) {
      // Leaving a live search behind, on a circuit switch or a panel
      // reset: stop it server-side too, or it keeps spending simulations
      // nobody is watching.
      stopDesign();
    }
    designJob = null;
    designResult = null;
    designGenerate.disabled = false;
    designGenerateLabel.textContent = "Generate design from specs";
    designStart.disabled = false;
    designStartLabel.textContent = "Optimize from the values on the page";
    show(designProgress, false);
    show(designApply, false);
    show(id("design-changed"), false);
    show(designReason, false);
    show(designError, false);
    clear(designBest);
  }

  function renderDesignPanel() {
    // The design panel is absent from the static document.
    if (!designGenerate) {
      return;
    }
    // Visibility belongs to the tab controller; this only fills the panel.
    designIdle();
    clear(designGoalsEl);
    designInputs = {};

    var block = current.design;
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
      // Text for the same reason the parameter fields are: "5meg" and
      // "150u" are how targets get written.
      input.type = "text";
      input.value = String(item.default);
      input.autocomplete = "off";
      input.spellcheck = false;
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
    api("/api/design/status?job=" + encodeURIComponent(designJob))
      .then(function (response) {
        return response.json().then(function (snapshot) {
          return { ok: response.ok, snapshot: snapshot };
        });
      })
      .then(function (got) {
        if (!designJob) {
          return;
        }
        if (!got.ok) {
          // The job is gone: the server restarted, or pruned it. Say so
          // and stop asking, instead of rendering undefined forever.
          var gone = (got.snapshot && got.snapshot.error)
            || "The search's job is gone. Start it again.";
          designJob = null;
          designIdle();
          designShowError(gone);
          return;
        }
        var snapshot = got.snapshot;
        var evalsText =
          snapshot.evals + " / " + snapshot.max_evals + " simulations";
        // A real estimate from this search's own pace, not a guess.
        if (snapshot.status === "running" && designStarted
            && snapshot.evals >= 2) {
          var pace = (Date.now() - designStarted) / snapshot.evals;
          var left = Math.round(
            pace * (snapshot.max_evals - snapshot.evals) / 1000);
          if (left >= 2) {
            evalsText += " · about " + (left >= 90
              ? Math.round(left / 60) + " min" : left + " s") + " left at worst";
          }
        }
        designEvals.textContent = evalsText;
        renderDesignBest(snapshot);

        if (snapshot.status === "running") {
          designState.textContent = "Running";
          designTimer = setTimeout(pollDesign, 1200);
          return;
        }
        window.FaradaemNotify.done(
          snapshot.status === "done"
            ? (snapshot.feasible
               ? "The design search finished: every target holds."
               : "The design search finished: the spec was not met.")
            : "The design search " + snapshot.status + ".");

        designState.textContent =
          snapshot.status === "done"
            ? (snapshot.feasible ? "Spec met" : "Finished")
            : snapshot.status === "stopped" ? "Stopped" : "Failed";
        designStart.disabled = false;
        designStartLabel.textContent = "Optimize from the values on the page";
        designGenerate.disabled = false;
        designGenerateLabel.textContent = "Generate design from specs";
        show(designStop, false);

        if (snapshot.status === "failed") {
          designShowError(snapshot.error ||
            "The search failed. Check the console running server.py.");
          return;
        }
        if (snapshot.status === "done") {
          text(id("design-state"), snapshot.feasible
            ? "Finished: every target holds"
            : "Finished: the spec was not met");
        }
        if (snapshot.reason) {
          designReason.textContent = snapshot.reason;
          show(designReason, true);
        }
        if (snapshot.best && snapshot.best.params) {
          designResult = snapshot.best.params;
          // Report exactly what the search changed against the values
          // that were on the page.
          renderDesignChanges(values(), designResult);
          if (snapshot.status === "done" && snapshot.feasible) {
            // A finished search that met the spec puts its answer on the
            // bench and measures it: the 1.12.2 contract.
            applyDesign();
          } else {
            // A stopped or unmet search only offers its best point. The
            // user's own values stay until they choose.
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
    var labels = {};
    (current.design.goals || []).forEach(function (goal) {
      labels[goal.key] = goal.label;
    });
    Object.keys(designInputs).forEach(function (key) {
      var value = window.parseEngineering(designInputs[key].value);
      if (!isFinite(value) || value <= 0) {
        bad = labels[key] || key;
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
    designStarted = Date.now();
    window.FaradaemNotify.ask();

    api("/api/design", {
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
    api("/api/design/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job: designJob })
    }).catch(function () {});
  }

  /* What the search changed, knob by knob, before the new values land.
     A search that reports only "done" teaches nothing; one that says
     "ibias 20 uA \u2192 46 uA" teaches the move. */
  function renderDesignChanges(before, after) {
    var host = id("design-changed");
    clear(host);
    var specs = {};
    current.params.forEach(function (spec) { specs[spec.key] = spec; });

    var moved = [];
    var held = [];
    Object.keys(after).forEach(function (key) {
      var spec = specs[key] || { key: key, label: key };
      var was = before[key];
      var now = after[key];
      if (typeof was === "number" && typeof now === "number" &&
          Math.abs(now - was) > Math.abs(was) * 1e-9) {
        moved.push({ spec: spec, was: was, now: now });
      } else {
        held.push(spec.label || key);
      }
    });

    if (!moved.length) {
      host.appendChild(el("p", "sheet-note",
        "The search kept every value where it already was."));
      show(host, true);
      return;
    }

    host.appendChild(el("p", "mentor-head", "What the search changed"));
    var wrap = el("div", "sheet-table-wrap");
    var table = el("table", "sheet-table");
    var head = el("tr");
    ["knob", "was", "now", "change"].forEach(function (text) {
      head.appendChild(el("th", null, text));
    });
    table.appendChild(head);
    moved.forEach(function (item) {
      var row = el("tr");
      var unit = item.spec.unit || "";
      row.appendChild(el("td", null, item.spec.label || item.spec.key));
      row.appendChild(el("td", "num",
        window.formatEngineering(item.was, unit)));
      row.appendChild(el("td", "num",
        window.formatEngineering(item.now, unit)));
      var percent = item.was === 0 ? null
        : ((item.now - item.was) / Math.abs(item.was)) * 100;
      row.appendChild(el("td", "num delta",
        percent === null ? "\u2014"
          : (percent >= 0 ? "+" : "") + percent.toFixed(0) + "%"));
      table.appendChild(row);
    });
    wrap.appendChild(table);
    host.appendChild(wrap);
    if (held.length) {
      host.appendChild(el("p", "sheet-note",
        "Unchanged: " + held.join(", ") + "."));
    }
    show(host, true);
  }

  function applyDesign() {
    if (!designResult) {
      return;
    }
    show(designApply, false);
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
    var genCircuit = current.id;
    show(designError, false);
    show(designReason, false);
    show(designApply, false);
    show(id("design-changed"), false);

    var targets = collectTargets();
    if (targets === null) {
      return;
    }

    designGenerate.disabled = true;
    designStart.disabled = true;
    designGenerateLabel.textContent = "Generating";

    api("/api/design/seed", {
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
          if (current.id !== genCircuit) {
            // The seed belongs to the circuit it was asked for; the page
            // has moved on.
            return null;
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
      .then(function (confirmed) {
        if (current.id !== genCircuit) {
          // The user moved on mid-confirmation. Iterating would start a
          // search on whatever circuit is on the page now, which nobody
          // asked for.
          return;
        }
        // Judged on this run's own measurement: a failed confirming run
        // must not be papered over by whatever succeeded earlier, and it
        // must not launch a search either.
        if (!confirmed) {
          designGenerate.disabled = false;
          designStart.disabled = false;
          designGenerateLabel.textContent = "Generate design from specs";
          designShowError("The confirming run did not finish, so nothing "
            + "was searched. Fix the error above and generate again.");
          return;
        }
        if (meetsTargets(confirmed, targets)) {
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

  on(designGenerate, "click", generateDesign);
  on(designStart, "click", function () { startDesign("manual"); });
  on(designStop, "click", stopDesign);
  on(designApply, "click", applyDesign);


  /* ---- ask for a design --------------------------------------------------- */

  var adviseForm = id("advise-form");
  var adviseInput = id("advise-input");
  var adviseLog = id("advise-log");
  var adviseNow = id("advise-now");
  var adviseProvider = id("advise-provider");
  var adviseSend = id("advise-send");
  var adviseStop = id("advise-stop");
  var adviseSendLabel = id("advise-send-label");
  var adviseReset = id("advise-reset");
  var adviseHint = id("advise-hint");
  var adviseError = id("advise-error");

  var adviseJob = null;
  var adviseTimer = null;
  var adviseRendered = 0;
  var adviseProviders = [];
  // The last tool card of this turn that met every target. When the
  // strategist finishes its turn, this is the design that goes on the
  // bench; until then, nothing touches the form.
  var adviseWinner = null;
  // Which circuit this turn has already put on the page, or null. The
  // first sizing the strategist touches switches the schematic to the
  // topology under design; the candidates after it do not, so a
  // forty-evaluation search moves the page twice: start and finish.
  var adviseTurnPreviewed = null;

  function adviseShowError(message) {
    adviseError.textContent = message;
    show(adviseError, true);
  }

  function adviseSetBusy(busy) {
    adviseSend.disabled = busy;
    adviseSendLabel.textContent = busy ? "Working" : (adviseJob ? "Reply" : "Send");
    // A working strategist can be stopped; an idle one has nothing to stop.
    show(adviseStop, busy && Boolean(adviseJob));
    if (!busy) {
      adviseStop.disabled = false;
      adviseStop.textContent = "Stop";
    }
  }

  function adviseMessage(role, text, extraClass) {
    var block = el("div", "advise-msg" + (extraClass ? " " + extraClass : ""));
    block.appendChild(el("span", "advise-role", role));
    var body = el("p", "advise-text", text);
    block.appendChild(body);
    adviseLog.appendChild(block);
    return block;
  }

  function providerLabel() {
    for (var i = 0; i < adviseProviders.length; i++) {
      if (adviseProviders[i].name === adviseProvider.value) {
        return adviseProviders[i].label;
      }
    }
    return "Strategist";
  }

  /* Pull the interesting numbers out of one tool card's display payload. */
  function cardPairs(display) {
    var pairs = [];
    var measured = display.measured ||
      (display.best && display.best.measured) || null;
    if (measured) {
      Object.keys(measured).forEach(function (key) {
        var value = measured[key];
        if (typeof value === "number" && isFinite(value)) {
          pairs.push([key, window.formatEngineering(value, "")]);
        }
      });
    }
    if (typeof display.feasible === "boolean") {
      pairs.unshift(["spec", display.feasible ? "met" : "not met"]);
    }
    if (typeof display.evals === "number") {
      pairs.push(["simulations", String(display.evals)]);
    }
    return pairs.slice(0, 8);
  }

  function applyFromCard(circuitId, params) {
    if (!known(circuitId)) {
      return;
    }
    if (circuitId !== current.id) {
      select(circuitId);
    }
    Object.keys(params).forEach(function (key) {
      if (inputs[key]) {
        inputs[key].value = String(params[key]);
      }
    });
    onEdit();
    run();
  }

  /* Put the circuit under design on the page without spending a
     simulation: the schematic redraws from the sizing, and the measured
     numbers stay empty until a real run fills them. */
  function previewFromCard(circuitId, params) {
    if (!known(circuitId)) {
      return;
    }
    if (circuitId !== current.id) {
      select(circuitId);
    }
    Object.keys(params).forEach(function (key) {
      if (inputs[key]) {
        inputs[key].value = String(params[key]);
      }
    });
    onEdit();
  }

  /* The ask panel finishes its own story, the same contract the design
     panel keeps: when the strategist ends its turn, the last sizing that
     met every target lands in the form, the schematic redraws from it,
     and the bench measures it again. Intermediates stay as cards with
     their own load buttons; only the turn's end touches the form, so a
     search that tries forty candidates moves the schematic once. */
  function adviseFinish() {
    if (adviseWinner) {
      var winner = adviseWinner;
      adviseWinner = null;
      applyFromCard(winner.circuit, winner.params);
      adviseMessage("Bench", "The winning sizing is on the bench: the form "
        + "holds it, the schematic is drawn from it, and it is being "
        + "measured again now.");
      return;
    }
    // A turn can end without a search verdict: the seed already met the
    // spec, so no card carries "feasible". The sizing it previewed is
    // already in the form; measuring the form is always honest, so the
    // bench fills in rather than sitting at a dash under a conversation
    // that says the targets are met. Only while the page still shows
    // the circuit the turn previewed: a user who browsed elsewhere
    // mid-turn is not interrupted with a measurement of the wrong page.
    if (adviseTurnPreviewed && adviseTurnPreviewed === current.id
        && !isStatic) {
      adviseMessage("Bench", "Measuring the sizing the strategist left "
        + "on the bench.");
      run();
    }
  }

  function adviseCard(event) {
    var block = el("div", "advise-msg");
    var card = el("div", "advise-card" + (event.ok ? "" : " is-failed"));
    card.appendChild(el("span", "card-title",
      event.tool + (event.ok ? "" : " failed")));

    if (!event.ok) {
      var reason = el("p", "advise-text", event.display.error || "");
      card.appendChild(reason);
    } else {
      var pairs = cardPairs(event.display);
      if (pairs.length) {
        var grid = el("div", "card-pairs");
        pairs.forEach(function (pair) {
          grid.appendChild(el("span", "k", pair[0]));
          grid.appendChild(el("span", "v", pair[1]));
        });
        card.appendChild(grid);
      }
      var params = event.display.params ||
        (event.display.best && event.display.best.params) || null;
      var circuitId = event.display.circuit;
      if (params && circuitId) {
        var apply = el("button", "chip", "Load into the form and run");
        apply.type = "button";
        apply.addEventListener("click", function () {
          applyFromCard(circuitId, params);
        });
        card.appendChild(apply);
        // Only a card that says the spec is met can be the turn's answer.
        // A card without a verdict is an exploration, not a claim.
        if (event.display.feasible === true) {
          adviseWinner = { circuit: circuitId, params: params };
        }
        // The first sizing of the turn shows the user what is being
        // designed: the schematic switches to the working topology now
        // rather than after minutes of silence.
        if (!adviseTurnPreviewed) {
          adviseTurnPreviewed = circuitId;
          previewFromCard(circuitId, params);
        }
      }
    }
    block.appendChild(card);
    adviseLog.appendChild(block);
  }

  function renderAdviseEvents(events, first) {
    // The server sends a window onto the full log with its absolute
    // start position, so a long conversation cannot shift under the
    // renderer's index.
    var start = first || 0;
    for (var index = 0; index < events.length; index++) {
      if (start + index < adviseRendered) {
        continue;
      }
      var event = events[index];
      if (event.kind === "user") {
        // A new turn owes nothing to the last one: neither its winner
        // nor its preview may leak into this turn's ending.
        adviseTurnPreviewed = null;
        adviseWinner = null;
        adviseMessage("You", event.text);
      } else if (event.kind === "text") {
        adviseMessage(providerLabel(), event.text);
      } else if (event.kind === "done") {
        adviseMessage(providerLabel(), event.text);
        adviseFinish();
      } else if (event.kind === "question") {
        adviseMessage(providerLabel(), event.text, "is-question");
      } else if (event.kind === "tool") {
        adviseCard(event);
      } else if (event.kind === "error") {
        // A turn that ended in an error or a stop produced no answer: a
        // later turn's "done" must not apply what this one left behind.
        adviseWinner = null;
        adviseTurnPreviewed = null;
        adviseMessage(providerLabel(), event.message, "is-error");
      }
    }
    adviseRendered = Math.max(adviseRendered, start + events.length);
    adviseLog.scrollTop = adviseLog.scrollHeight;
  }

  /* The search's heartbeat: one line, updated in place, so a minute of
     iterating reads as work instead of silence. */
  function renderAdviseNow(now) {
    if (!now) {
      show(adviseNow, false);
      return;
    }
    var text = "Searching: simulation " + (now.evals || "?");
    if (now.error) {
      text += ", candidate could not be measured";
    } else if (typeof now.score === "number" && now.margins) {
      var binding = null;
      Object.keys(now.margins).forEach(function (key) {
        if (binding === null || now.margins[key] < now.margins[binding]) {
          binding = key;
        }
      });
      text += ", worst margin " + (now.score >= 0 ? "+" : "")
        + (now.score * 100).toFixed(1) + "% on " + binding;
    }
    adviseNow.textContent = text;
    show(adviseNow, true);
  }

  function pollAdvise() {
    if (!adviseJob) {
      return;
    }
    api("/api/advise/status?job=" + encodeURIComponent(adviseJob))
      .then(function (response) {
        return response.json().then(function (snapshot) {
          return { ok: response.ok, snapshot: snapshot };
        });
      })
      .then(function (got) {
        if (!adviseJob) {
          return;
        }
        if (!got.ok) {
          // The session is gone: the server restarted or evicted it.
          adviseMessage(providerLabel(),
            (got.snapshot && got.snapshot.error)
            || "The session is gone. Start over.", "is-error");
          adviseSetBusy(false);
          renderAdviseNow(null);
          show(adviseReset, true);
          return;
        }
        var snapshot = got.snapshot;
        renderAdviseEvents(snapshot.events, snapshot.first);
        renderAdviseNow(snapshot.status === "running" ? snapshot.now : null);
        if (snapshot.status === "running") {
          adviseTimer = setTimeout(pollAdvise, 1000);
          return;
        }
        adviseSetBusy(false);
        show(adviseReset, true);
        window.FaradaemNotify.done(snapshot.status === "question"
          ? "The strategist has a question for you."
          : "The strategist finished its turn.");
        if (snapshot.status === "question") {
          adviseInput.focus();
        }
      })
      .catch(function () {
        if (adviseJob) {
          adviseTimer = setTimeout(pollAdvise, 2500);
        }
      });
  }

  function sendAdvise(event) {
    if (event) {
      event.preventDefault();
    }
    var message = adviseInput.value.trim();
    if (!message || adviseSend.disabled) {
      return;
    }
    show(adviseError, false);
    show(adviseLog, true);
    adviseInput.value = "";
    adviseSetBusy(true);

    var url = adviseJob ? "/api/advise/reply" : "/api/advise";
    var body = adviseJob
      ? { job: adviseJob, message: message }
      : { message: message, provider: adviseProvider.value || "anthropic" };

    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) {
            throw new Error(payload && payload.error
              ? payload.error
              : "The server refused the request.");
          }
          adviseJob = payload.job;
          adviseSetBusy(true);
          window.FaradaemNotify.ask();
          pollAdvise();
        });
      })
      .catch(function (error) {
        adviseSetBusy(false);
        adviseShowError(String(error.message || error));
      });
  }

  function resetAdvise() {
    if (adviseTimer) {
      clearTimeout(adviseTimer);
      adviseTimer = null;
    }
    adviseJob = null;
    adviseRendered = 0;
    adviseWinner = null;
    adviseTurnPreviewed = null;
    show(adviseNow, false);
    clear(adviseLog);
    show(adviseLog, false);
    show(adviseReset, false);
    adviseSetBusy(false);
  }

  function startAdvisePanel() {
    api("/api/advise/providers")
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        adviseProviders = payload.providers || [];
        clear(adviseProvider);
        adviseProviders.forEach(function (item) {
          var option = document.createElement("option");
          option.value = item.name;
          option.textContent = item.label;
          adviseProvider.appendChild(option);
        });
        show(adviseProvider, adviseProviders.length > 1);
        if (adviseProviders.length === 0) {
          adviseSend.disabled = true;
          adviseHint.textContent =
            "No model key is set. Run setx FARADAEM_ANTHROPIC_KEY or setx " +
            "FARADAEM_OPENAI_KEY with your key, then reload this page.";
          show(adviseHint, true);
        }
      })
      .catch(function () {
        adviseSend.disabled = true;
        adviseHint.textContent = "Could not check for model keys. Reload the page.";
        show(adviseHint, true);
      });
  }

  /* The whole panel is absent from the static document. Binding to it
     would throw on the first null, and startAdvisePanel() would ask a
     server that is not there -- which is exactly what the published site
     used to do, once per load, before the mode was known. */
  if (adviseForm) {
    adviseForm.addEventListener("submit", sendAdvise);
    adviseReset.addEventListener("click", resetAdvise);
    adviseStop.addEventListener("click", function () {
      if (!adviseJob) {
        return;
      }
      adviseStop.disabled = true;
      adviseStop.textContent = "Stopping";
      api("/api/advise/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job: adviseJob })
      }).catch(function () {
        adviseStop.disabled = false;
        adviseStop.textContent = "Stop";
      });
    });
    adviseInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        sendAdvise();
      }
    });
    startAdvisePanel();
  }



  /* ---- figure scale ------------------------------------------------------- */

  /* How many CSS pixels one drawing unit may become. A three-part divider
     stops growing at MAX so it does not fill the column with white space;
     a dense circuit stops shrinking at MIN and scrolls sideways instead of
     collapsing into a thumbnail nobody can read. */
  var UNIT_PX_MAX = 1.15;
  var UNIT_PX_MIN = 0.85;

  //: The size the drawer chose, before any fitting. Resizing refits from
  //: this, never from the padded box a previous fit left behind.
  var naturalView = null;

  function fitSchematic() {
    var svg = id("schematic");
    var lane = svg.parentNode;
    var laneWidth = lane.clientWidth;
    if (!naturalView || !naturalView.w || !laneWidth) {
      return;
    }

    var unit = Math.min(UNIT_PX_MAX, laneWidth / naturalView.w);
    if (unit < UNIT_PX_MIN) {
      unit = UNIT_PX_MIN;
    }
    var drawn = naturalView.w * unit;

    if (drawn <= laneWidth + 0.5) {  // half a pixel of float slack
      // It fits: widen the box around the drawing instead of stretching the
      // drawing, which centres it and pins the scale at exactly `unit`.
      var boxWidth = laneWidth / unit;
      var inset = (boxWidth - naturalView.w) / 2;
      svg.setAttribute("viewBox", (-inset).toFixed(2) + " 0 "
        + boxWidth.toFixed(2) + " " + naturalView.h);
      svg.style.width = "";
      svg.style.maxWidth = "";
    } else {
      svg.setAttribute("viewBox",
        "0 0 " + naturalView.w + " " + naturalView.h);
      svg.style.width = Math.round(drawn) + "px";
      svg.style.maxWidth = "none";
    }
  }

  //: The last plot drawn, kept so a resize can redraw it at the new width.
  var lastBode = null;

  function refitFigures() {
    fitSchematic();
    panels.forEach(function (panel) {
      if (panel.refit) {
        panel.refit();
      }
    });
    if (lastBode && bodePanel && !bodePanel.classList.contains("hidden")) {
      window.drawBode(id("bode"), lastBode);
    }
  }

  window.addEventListener("resize", refitFigures);



  /* ---- the bench: where every verdict lands ----------------------------
     The checks run in different panels at different times. Each reports
     here, so the state of the circuit on the bench is one look. The slots
     reset when the circuit changes, because a verdict about one circuit
     says nothing about the next. */
  var BENCH_STATES = {
    idle: ["", "not yet"],
    run: ["is-run", "running"],
  };

  function benchSet(slot, state, text) {
    var host = id("bench-" + slot);
    if (!host) {
      return;
    }
    host.classList.remove("is-pass", "is-fail", "is-run");
    var label = host.querySelector(".bench-state");
    if (state === "pass") {
      host.classList.add("is-pass");
      label.textContent = text || "clean";
    } else if (state === "fail") {
      host.classList.add("is-fail");
      label.textContent = text || "failed";
    } else if (state === "run") {
      host.classList.add("is-run");
      label.textContent = text || "running";
    } else {
      label.textContent = text || "not yet";
    }
  }

  function benchReset() {
    // The verification strip reports checks only the local tool can run.
    if (!id("bench")) {
      return;
    }
    benchSet("sim", "idle", "not run");
    var laid = Boolean(current && current.floorplan);
    ["drc", "signoff", "lvs"].forEach(function (slot) {
      benchSet(slot, "idle", laid ? "not run" : "no layout");
    });
  }

  /* The layout panel lives in its own file; this is how its verdicts
     reach the strip. */
  window.FaradaemBench = { set: benchSet };

  /* ---- elapsed time on anything slow ------------------------------------
     One clock, shared. A panel starts it on its status line and stops it
     with the text the line should end on; the reader sees the seconds
     climb instead of wondering whether anything is happening. */
  var ticking = [];

  window.setInterval(function () {
    var now = Date.now();
    ticking.forEach(function (entry) {
      entry.node.textContent = entry.base + " \u00b7 "
        + Math.round((now - entry.started) / 1000) + " s";
    });
  }, 1000);

  function tickStart(node) {
    tickStop(node, null);
    ticking.push({ node: node, base: node.textContent,
                   started: Date.now() });
  }

  function tickStop(node, finalText) {
    ticking = ticking.filter(function (entry) { return entry.node !== node; });
    if (finalText !== null && finalText !== undefined) {
      node.textContent = finalText;
    }
  }

  /* A tab whose pane holds a result says so with a dot. */
  function markTab(key, bad) {
    var tab = document.querySelector(
      '.analysis-tab[data-analysis="' + key + '"]');
    if (tab) {
      tab.classList.remove("has-result", "has-error");
      tab.classList.add(bad ? "has-error" : "has-result");
    }
  }

  /* ---- the measurement panels -------------------------------------------
     Each lives in its own file and registers a factory on
     window.FaradaemPanels. They are handed the handful of things every
     panel needs and nothing else, which is what let them move out of this
     closure without their bodies changing. */
  var panelContext = {
    id: id,
    show: show,
    clear: clear,
    el: el,
    values: values,
    validate: validate,
    current: function () { return current; },
    tickStart: tickStart,
    tickStop: tickStop,
    markTab: markTab
  };

  var panels = (window.FaradaemPanels || []).map(function (make) {
    return make(panelContext);
  });


  /* ---- the analysis tabs -------------------------------------------------- */

  /* The four deeper analyses share one strip below the form. Which of them
     a circuit offers varies: only designable circuits can be designed to a
     spec, only the SKY130 amplifiers have a step or a rejection testbench,
     and only PDK circuits have corners. The strip is built from that, so a
     tab never appears for something the circuit cannot do. */
  var ANALYSES = [
    { key: "design", label: "Design to spec",
      available: function () { return Boolean(current.design); } },
    { key: "step", label: "Step response",
      available: function () { return Boolean(current.step); } },
    { key: "sheet", label: "Rejection and range",
      available: function () { return Boolean(current.datasheet); } },
    { key: "layout", label: "Layout",
      available: function () { return Boolean(current.floorplan); } },
    { key: "robust", label: "Robustness",
      available: function () { return Boolean(current.pdk); } },
    { key: "datasheet", label: "Datasheet",
      available: function () {
        return Boolean(current.design || current.pdk);
      } }
  ];

  var analysisSection = id("analysis");
  var analysisTabs = id("analysis-tabs");

  /* The circuit chips already rove with the arrow keys; the analysis
     strip carries the same role="tab" markup, so it keeps the same
     promise. */
  if (analysisTabs) {
    analysisTabs.addEventListener("keydown", function (event) {
      var delta = event.key === "ArrowRight" ? 1
        : event.key === "ArrowLeft" ? -1 : 0;
      if (!delta) { return; }
      var buttons = Array.prototype.slice.call(
        analysisTabs.querySelectorAll(".analysis-tab"));
      var index = buttons.indexOf(document.activeElement);
      if (index < 0) { return; }
      event.preventDefault();
      var next = (index + delta + buttons.length) % buttons.length;
      buttons[next].focus();
      buttons[next].click();
    });
  }
  var openAnalysis = null;

  function showAnalysis(key) {
    openAnalysis = key;
    ANALYSES.forEach(function (item) {
      show(id("pane-" + item.key), item.key === key);
    });
    if (analysisTabs) {
      Array.prototype.forEach.call(
        analysisTabs.querySelectorAll(".analysis-tab"),
        function (button) {
          button.setAttribute("aria-selected",
            button.getAttribute("data-analysis") === key ? "true" : "false");
        }
      );
    }
    // A plot drawn while its pane was hidden measured zero width, so it is
    // redrawn on the way in, when the element finally has a size.
    panels.forEach(function (panel) {
      if (panel.key === key && panel.reveal) {
        panel.reveal();
      }
    });
  }

  function renderAnalysis() {
    // The whole analysis section is absent from the static document; there
    // is nothing to render and nothing to put away.
    if (!analysisTabs) {
      return;
    }
    clear(analysisTabs);
    var offered = ANALYSES.filter(function (item) {
      return item.available();
    });

    show(analysisSection, offered.length > 0);
    if (!offered.length) {
      ANALYSES.forEach(function (item) { show(id("pane-" + item.key), false); });
      openAnalysis = null;
      return;
    }

    offered.forEach(function (item) {
      var button = el("button", "analysis-tab", item.label);
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("data-analysis", item.key);
      button.addEventListener("click", function () { showAnalysis(item.key); });
      analysisTabs.appendChild(button);
    });

    // Keep the open tab across a circuit change when the new circuit has it.
    var keys = offered.map(function (item) { return item.key; });
    showAnalysis(keys.indexOf(openAnalysis) === -1 ? keys[0] : openAnalysis);
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

    text(id("panel-title"), current.analysis === "dc" ? "DC operating point" : "AC sweep");
    text(id("caption"), current.caption);

    renderTabs();
    benchReset();
    renderPresets();
    // This session's edits first, then what a previous visit left, then
    // the registry defaults renderInputs falls back to.
    renderInputs(memory[current.id] || storedFor(current));
    renderDesignPanel();
    // A run still measuring the previous circuit owns the caption's
    // ticker; the new circuit starts from "Run to measure."
    tickStop(captionState, "Run to measure.");
    // The mentor's answers were about the previous circuit, and a mentor
    // job still polling for it would render them onto this one. The
    // job itself stops server-side too: abandoned, it would hold the
    // circuit's one-job lock and burn simulations nobody reads.
    biasReset();
    if (blameJob) {
      api("/api/workbench/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job: blameJob })
      }).catch(function () {});
    }
    blameJob = null;
    if (mentorTimer) {
      clearTimeout(mentorTimer);
      mentorTimer = null;
    }
    mentorButtons(false);
    historyRender();
    // The comparison table pairs the held design with the LAST measured
    // result, which after a switch belongs to another circuit: hide it
    // until this circuit measures again.
    show(id("ab-block"), false);
    show(id("pin-row"), false);
    show(id("triage-line"), false);
    show(id("blame-out"), false);
    show(id("sweep-panel"), false);
    show(id("mentor-error"), false);
    show(id("mentor-state"), false);
    show(id("mentor"), Boolean(current.design));
    if (current.design && current.design.tunable) {
      text(id("blame-label"), "Explain the margins (" +
        (1 + 2 * current.design.tunable.length) + " simulations)");
    }
    show(id("sweep-run"),
         Boolean(current.design && current.design.sweep));

    panels.forEach(function (panel) {
      panel.render(current);
    });
    renderAnalysis();
    hideNetlist();
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

  /* Where the catalogue comes from is a property of the deployment, not
     something to discover by trying. The published site reads the copy the
     build wrote beside it and never touches /api, which would be a request
     that can only 404; the local page asks its own server. */
  async function loadCatalogue() {
    if (isStatic) {
      var published = await fetch("catalogue.json");
      if (!published.ok) {
        throw new Error("no catalogue");
      }
      return (await published.json()).circuits;
    }
    var live = await api("/api/circuits");
    if (!live.ok) {
      throw new Error("no catalogue");
    }
    return (await live.json()).circuits;
  }

  async function start() {
    try {
      catalogue = await loadCatalogue();
    } catch (networkError) {
      text(id("panel-title"), "Unavailable");
      showError(isStatic
        ? "Could not load the published circuit catalogue. Reload the page; "
          + "if it keeps failing the site is mid-deploy."
        : "Could not load the circuit catalogue. Start the server with "
          + "python server.py and reload.");
      return;
    }

    var wanted = window.location.hash.replace("#", "");
    select(known(wanted) ? wanted : catalogue[0].id);
  }

  /* ---- the pin chip ------------------------------------------------------
     A pin is the server's own measurement of this sizing, frozen. The
     chip re-measures rather than trusting the page, so what is pinned is
     what ngspice said, not what the DOM held. */
  on("pin-set", "click", function () {
    var chip = id("pin-set");
    var note = id("pin-note");
    chip.disabled = true;
    note.textContent = "measuring";
    tickStart(note);
    api("/api/pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ circuit: current.id, params: values() })
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) {
            throw new Error(payload.error || "Could not pin.");
          }
          tickStop(note, "Pinned " + payload.pinned.pinned_utc +
            ". Checks live in the Datasheet tab.");
        });
      })
      .catch(function (error) {
        tickStop(note, String(error.message || error));
      })
      .then(function () { chip.disabled = false; });
  });

  /* ---- the mentor: triage, blame, the sweep ------------------------------
     Three questions against the same targets the design panel holds.
     Every number rendered here is a difference or a point ngspice
     measured; the page only arranges them. */
  var mentorState = id("mentor-state");
  var mentorError = id("mentor-error");

  function mentorTargets() {
    // The design inputs are the targets when they are filled; otherwise
    // the registry's own goals stand, which triage reports as such.
    var targets = {};
    var any = false;
    Object.keys(designInputs).forEach(function (key) {
      var value = window.parseEngineering(designInputs[key].value);
      if (isFinite(value) && value > 0) {
        targets[key] = value;
        any = true;
      }
    });
    return any ? targets : null;
  }

  function mentorFail(error) {
    // The failed action's ticker must die with it, or it keeps writing
    // over whatever status the next mentor run puts here.
    tickStop(mentorState, null);
    show(mentorState, false);
    mentorError.textContent = String(error.message || error);
    show(mentorError, true);
  }

  /* One mentor run at a time: the buttons disable while one works, so a
     double click cannot trip the server's one-job-per-circuit refusal. */
  function mentorButtons(disabled) {
    ["triage-run", "blame-run", "sweep-run"].forEach(function (name) {
      var button = id(name);
      if (button) {
        button.disabled = disabled;
      }
    });
  }

  on("triage-run", "click", function () {
    var asked = current.id;
    show(mentorError, false);
    mentorButtons(true);
    mentorState.textContent = "Measuring once";
    show(mentorState, true);
    tickStart(mentorState);
    api("/api/triage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ circuit: asked, params: values(),
                             targets: mentorTargets() })
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (current.id !== asked) {
            // The verdict is about a circuit no longer on the page.
            tickStop(mentorState, null);
            show(mentorState, false);
            return;
          }
          if (!response.ok) {
            throw new Error(payload.error || "Refused.");
          }
          tickStop(mentorState, null);
          show(mentorState, false);
          var line = id("triage-line");
          line.textContent = payload.sentence +
            " Measured at tt, nominal supply, 27 \u00b0C.";
          line.className = "triage-line " +
            (payload.feasible_here === false ? "is-missed" : "is-met");
          show(line, true);
        });
      })
      .catch(mentorFail)
      .then(function () { mentorButtons(false); });
  });

  var blameJob = null;
  var mentorTimer = null;

  function pollMentorJob(kind, onDone) {
    var job = blameJob;
    var misses = 0;

    function tick() {
      if (job !== blameJob) {
        return;
      }
      api("/api/workbench/status?job=" + job)
        .then(function (r) {
          return r.json().then(function (snap) {
            return { ok: r.ok, snap: snap };
          });
        })
        .then(function (got) {
          if (job !== blameJob) {
            return;
          }
          if (!got.ok) {
            // The job is gone: the server restarted or evicted it. Stop
            // asking and say so, instead of rendering nothing forever.
            blameJob = null;
            mentorButtons(false);
            mentorFail(new Error((got.snap && got.snap.error)
              || "The run's job is gone. Start it again."));
            return;
          }
          var snap = got.snap;
          misses = 0;
          if (snap.status === "running") {
            mentorState.textContent = "Running: " + snap.stage + " · " +
              Math.round(snap.seconds) + " s";
            mentorTimer = setTimeout(tick, 1200);
            return;
          }
          blameJob = null;
          mentorButtons(false);
          if (snap.status === "failed") {
            mentorFail(new Error(snap.error || "The run failed."));
            return;
          }
          if (snap.status === "stopped") {
            mentorFail(new Error("Stopped before it finished."));
            return;
          }
          show(mentorState, false);
          window.FaradaemNotify.done(
            (kind === "blame" ? "The sensitivity run" : "The bias sweep")
            + " finished.");
          onDone(snap);
        })
        .catch(function () {
          if (job !== blameJob) {
            return;
          }
          misses += 1;
          if (misses >= 4) {
            blameJob = null;
            mentorButtons(false);
            mentorFail(new Error("Could not reach the Faradaem server."));
            return;
          }
          mentorTimer = setTimeout(tick, 2500);
        });
    }

    tick();
  }

  function startMentorJob(kind, onDone) {
    show(mentorError, false);
    mentorButtons(true);
    mentorState.textContent = "Starting";
    show(mentorState, true);
    api("/api/workbench", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ circuit: current.id, params: values(),
                             kind: kind, targets: mentorTargets() })
    })
      .then(function (response) {
        return response.json().then(function (payload) {
          if (!response.ok) {
            throw new Error(payload.error || "Refused.");
          }
          blameJob = payload.job;
          pollMentorJob(kind, onDone);
        });
      })
      .catch(function (error) {
        mentorButtons(false);
        mentorFail(error);
      });
  }

  on("blame-run", "click", function () {
    startMentorJob("blame", renderBlame);
  });

  function renderBlame(snap) {
    var found = snap.result;
    var out = id("blame-out");
    clear(out);

    var binding = null;
    (found.margins || []).forEach(function (m) {
      if (m.binding) { binding = m; }
    });

    if (binding) {
      var opening = binding.label + " is " +
        (binding.met ? "the binding target" : "failing") + " at " +
        present(binding.measured, findSpec(binding.key)) + " against " +
        binding.op + " " + present(binding.target, findSpec(binding.key)) +
        ".";
      out.appendChild(el("p", "triage-line " +
        (binding.met ? "is-met" : "is-missed"), opening));
    }

    var table = el("table", "sheet-table");
    var head = el("tr");
    head.appendChild(el("th", null, "knob"));
    head.appendChild(el("th", null, "step measured"));
    (found.margins || []).forEach(function (m) {
      head.appendChild(el("th", null, m.label));
    });
    table.appendChild(head);

    var ranked = found.knobs.slice();
    if (binding) {
      ranked.sort(function (a, b) {
        return Math.abs(b.slopes[binding.key] || 0) -
          Math.abs(a.slopes[binding.key] || 0);
      });
    }
    ranked.forEach(function (knob) {
      var row = el("tr");
      row.appendChild(el("td", null, knob.label));
      row.appendChild(el("td", "sheet-at",
        window.formatEngineering(knob.step_lo, knob.unit) + " to " +
        window.formatEngineering(knob.step_hi, knob.unit)));
      (found.margins || []).forEach(function (m) {
        var slope = knob.slopes[m.key];
        var cell = el("td", "num");
        if (typeof slope === "number") {
          cell.textContent = present(slope, findSpec(m.key)) + " / " +
            (knob.unit || "unit");
          if (binding && m.key === binding.key) {
            cell.className = "num is-binding";
          }
        } else {
          cell.textContent = "\u2014";
        }
        row.appendChild(cell);
      });
      table.appendChild(row);
    });

    var wrap = el("div", "sheet-table-wrap");
    wrap.appendChild(table);
    out.appendChild(wrap);
    out.appendChild(el("p", "sheet-note",
      "Measured: " + found.method + ". " + found.sims +
      " simulations. Local slopes at this sizing, not a model."));
    show(out, true);
  }

  function findSpec(key) {
    var readout = current.readout || {};
    var all = [readout.headline].concat(readout.stats || []);
    for (var i = 0; i < all.length; i++) {
      if (all[i] && all[i].key === key) { return all[i]; }
    }
    return { key: key };
  }

  on("sweep-run", "click", function () {
    startMentorJob("sweep", renderSweep);
  });

  function renderSweep(snap) {
    var found = snap.result;
    var svg = id("sweep-plot");
    clear(svg);
    var good = found.points.filter(function (p) {
      return p.measured && typeof p.measured.power === "number" &&
        typeof p.measured.f_crossover === "number";
    });
    var broken = found.points.filter(function (p) { return p.error; });

    if (good.length) {
      var xs = good.map(function (p) { return p.measured.power; });
      var ys = good.map(function (p) { return p.measured.f_crossover; });
      var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
      var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
      function px(v) {
        return 46 + 440 * (Math.log(v / x0) / Math.log(x1 / x0 || 2));
      }
      function py(v) {
        return 262 - 230 * (Math.log(v / y0) / Math.log(y1 / y0 || 2));
      }
      var ns = "http://www.w3.org/2000/svg";
      var path = document.createElementNS(ns, "path");
      path.setAttribute("d", good.map(function (p, i) {
        return (i ? "L" : "M") + px(p.measured.power).toFixed(1) + " " +
          py(p.measured.f_crossover).toFixed(1);
      }).join(" "));
      path.setAttribute("class", "sweep-line");
      svg.appendChild(path);
      good.forEach(function (p) {
        var dot = document.createElementNS(ns, "circle");
        dot.setAttribute("cx", px(p.measured.power).toFixed(1));
        dot.setAttribute("cy", py(p.measured.f_crossover).toFixed(1));
        dot.setAttribute("r", "4");
        var pm = p.measured.phase_margin;
        dot.setAttribute("class", "sweep-dot" +
          (typeof pm === "number" && pm < 45 ? " is-missed" : ""));
        var title = document.createElementNS(ns, "title");
        title.textContent =
          found.knob_label + " = " +
          window.formatEngineering(p.value, found.knob_unit) + ": " +
          window.formatEngineering(p.measured.power, "W") + ", " +
          window.formatEngineering(p.measured.f_crossover, "Hz") +
          (typeof pm === "number"
            ? ", " + pm.toFixed(1) + "\u00b0 margin" : "");
        dot.appendChild(title);
        svg.appendChild(dot);
      });
      var xa = document.createElementNS(ns, "text");
      xa.setAttribute("x", "266"); xa.setAttribute("y", "292");
      xa.setAttribute("class", "sweep-axis");
      xa.textContent = "power, log";
      svg.appendChild(xa);
      var ya = document.createElementNS(ns, "text");
      ya.setAttribute("x", "10"); ya.setAttribute("y", "20");
      ya.setAttribute("class", "sweep-axis");
      ya.textContent = "crossover, log";
      svg.appendChild(ya);
    }

    var caption = "One-knob slice along " + found.knob_label +
      ", everything else at this sizing. Not a Pareto front. " +
      found.sims + " simulations at tt.";
    if (broken.length) {
      caption += " " + broken.length + " point" +
        (broken.length > 1 ? "s" : "") + " did not bias.";
    }
    text(id("sweep-caption"), caption);
    show(id("sweep-panel"), true);
  }

  /* The netlist is exactly what a user pastes into their own ngspice or
     a bug report, so it copies in one click. */
  on(netlistCopy, "click", function () {
    var text = id("netlist-view").textContent;
    if (!text) {
      return;
    }
    navigator.clipboard.writeText(text).then(function () {
      netlistCopy.textContent = "Copied";
      setTimeout(function () {
        netlistCopy.textContent = "Copy netlist";
      }, 1500);
    }, function () {
      netlistCopy.textContent = "Select and copy by hand";
    });
  });

  /* Ctrl+Enter runs from anywhere on the page -- except the advise box,
     where it sends the message, because that is what Ctrl+Enter means in
     a message box. */
  document.addEventListener("keydown", function (event) {
    if (!(event.ctrlKey || event.metaKey) || event.key !== "Enter") {
      return;
    }
    var advise = id("advise-input");
    if (advise && document.activeElement === advise) {
      var send = id("advise-send");
      if (send && !send.disabled) {
        event.preventDefault();
        send.click();
      }
      return;
    }
    if (!runButton.disabled) {
      event.preventDefault();
      runButton.click();
    }
  });

  start();
})(window, document);
