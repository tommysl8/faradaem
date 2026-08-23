/* Faradaem schematic renderer -- pure SVG, no libraries.
 *
 * Shape of this module: a small library of symbol primitives (wire, resistor,
 * dcSource, ground, nodeDot, label, valueTag) plus one compose function per
 * circuit. Adding the V0.1 RC network or the V0.4 op-amp means writing another
 * compose function, not touching the primitives.
 *
 * Colours come from the stylesheet via class names, so the design tokens stay
 * in one place. Geometry lives here.
 *
 * Attached to window: drawDivider, formatEngineering, FaradaemSymbols.
 */

(function (window, document) {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  /* ---- engineering notation ---------------------------------------- */

  var PREFIXES = {
    "12": "T", "9": "G", "6": "M", "3": "k", "0": "",
    "-3": "m", "-6": "µ", "-9": "n", "-12": "p"
  };

  var MAX_EXP = 12;
  var MIN_EXP = -12;

  /* 10000, "Ω" -> "10.00 kΩ"   |   5, "V" -> "5.000 V"
   * Four significant figures, exponent snapped to a multiple of three. */
  function formatEngineering(value, unit) {
    var suffix = unit || "";
    var number = typeof value === "string" ? Number(value) : value;

    if (typeof number !== "number" || !isFinite(number)) {
      return suffix ? "— " + suffix : "—";
    }
    if (number === 0) {
      return ("0.000 " + suffix).trim();
    }

    var sign = number < 0 ? "-" : "";
    var magnitude = Math.abs(number);
    var exponent = Math.floor(Math.log10(magnitude) / 3) * 3;
    exponent = Math.max(MIN_EXP, Math.min(MAX_EXP, exponent));

    var mantissa = magnitude / Math.pow(10, exponent);
    // Floating point can land the mantissa just outside [1, 1000).
    if (mantissa >= 1000 && exponent < MAX_EXP) {
      mantissa = mantissa / 1000;
      exponent = exponent + 3;
    }

    function digitsFor(value) {
      return Math.max(0, Math.min(3, 3 - Math.floor(Math.log10(value))));
    }

    var text = mantissa.toFixed(digitsFor(mantissa));

    // toFixed can round up across a decade: 999.95 -> "1000.0", which belongs
    // in the next prefix at its own precision (1.000 k, not 1.00 k).
    if (Number(text) >= 1000 && exponent < MAX_EXP) {
      mantissa = Number(text) / 1000;
      exponent = exponent + 3;
      text = mantissa.toFixed(digitsFor(mantissa));
    }

    return (sign + text + " " + PREFIXES[String(exponent)] + suffix).trim();
  }

  /* ---- svg primitives ---------------------------------------------- */

  function add(parent, name, attributes) {
    var node = document.createElementNS(NS, name);
    for (var key in attributes) {
      if (Object.prototype.hasOwnProperty.call(attributes, key)) {
        node.setAttribute(key, String(attributes[key]));
      }
    }
    parent.appendChild(node);
    return node;
  }

  function polyline(parent, points) {
    return add(parent, "polyline", {
      "class": "sch-stroke",
      points: points.map(function (point) {
        return point[0] + "," + point[1];
      }).join(" "),
      "stroke-linecap": "round",
      "stroke-linejoin": "round"
    });
  }

  /* Orthogonal wire: a straight run between two points. */
  function wire(parent, x1, y1, x2, y2) {
    return polyline(parent, [[x1, y1], [x2, y2]]);
  }

  /* IEEE zigzag resistor, six peaks by default.
   * Vertical (x, y1, y2) by default; pass orientation "horizontal" with
   * (y, x1, x2) to lay it along a rail instead. */
  function resistor(parent, options) {
    var peaks = options.peaks || 6;
    var amplitude = options.amplitude || 11;
    var horizontal = options.orientation === "horizontal";

    var start = horizontal ? options.x1 : options.y1;
    var end = horizontal ? options.x2 : options.y2;
    var axis = horizontal ? options.y : options.x;
    var step = (end - start) / peaks;

    // "along" runs down the body, "across" is the zigzag excursion.
    function at(along, across) {
      return horizontal ? [along, across] : [across, along];
    }

    var points = [at(start, axis)];
    for (var i = 0; i < peaks; i++) {
      points.push(at(
        start + (i + 0.5) * step,
        axis + (i % 2 === 0 ? amplitude : -amplitude)
      ));
    }
    points.push(at(end, axis));

    return polyline(parent, points);
  }

  function path(parent, d) {
    return add(parent, "path", { "class": "sch-stroke", d: d });
  }

  /* Non-polarised capacitor: two parallel plates with a gap between them.
   * Vertical (x, y1, y2) by default; horizontal takes (y, x1, x2). */
  function capacitor(parent, options) {
    var horizontal = options.orientation === "horizontal";
    var plate = options.plate || 30;
    var gap = options.gap || 9;

    var start = horizontal ? options.x1 : options.y1;
    var end = horizontal ? options.x2 : options.y2;
    var axis = horizontal ? options.y : options.x;
    var centre = options.centre === undefined ? (start + end) / 2 : options.centre;
    var group = add(parent, "g", {});

    function at(along, across) {
      return horizontal ? [along, across] : [across, along];
    }

    polyline(group, [at(start, axis), at(centre - gap / 2, axis)]);
    polyline(group, [
      at(centre - gap / 2, axis - plate / 2),
      at(centre - gap / 2, axis + plate / 2)
    ]);
    polyline(group, [
      at(centre + gap / 2, axis - plate / 2),
      at(centre + gap / 2, axis + plate / 2)
    ]);
    polyline(group, [at(centre + gap / 2, axis), at(end, axis)]);

    return group;
  }

  /* Inductor: four semicircular humps along the run. */
  function inductor(parent, options) {
    var horizontal = options.orientation !== "vertical";
    var humps = options.humps || 4;

    var start = horizontal ? options.x1 : options.y1;
    var end = horizontal ? options.x2 : options.y2;
    var axis = horizontal ? options.y : options.x;
    var radius = (end - start) / (humps * 2);

    var head = horizontal ? "M " + start + " " + axis : "M " + axis + " " + start;
    var step = horizontal
      ? " a " + radius + "," + radius + " 0 0,1 " + (radius * 2) + ",0"
      : " a " + radius + "," + radius + " 0 0,1 0," + (radius * 2);

    var d = head;
    for (var i = 0; i < humps; i++) {
      d += step;
    }

    return path(parent, d);
  }

  /* Op-amp: triangle pointing at its output, with the input marks inside.
   * The inverting input is the upper one, as drawn in every textbook. */
  function opamp(parent, options) {
    var x = options.x;
    var y = options.y;
    var width = options.width || 68;
    var height = options.height || 86;
    var group = add(parent, "g", {});

    var top = y - height / 2;
    var bottom = y + height / 2;
    polyline(group, [[x, top], [x, bottom], [x + width, y], [x, top]]);

    var invertingY = top + height / 3;
    var nonInvertingY = bottom - height / 3;
    var mark = 5;

    // Minus on the inverting input.
    polyline(group, [[x + 9, invertingY], [x + 9 + mark * 2, invertingY]]);
    // Plus on the non-inverting input.
    polyline(group, [[x + 9, nonInvertingY], [x + 9 + mark * 2, nonInvertingY]]);
    polyline(group, [
      [x + 9 + mark, nonInvertingY - mark],
      [x + 9 + mark, nonInvertingY + mark]
    ]);

    return group;
  }

  /* DC voltage source: circle with + above and - below the centre. */
  function dcSource(parent, options) {
    var cx = options.cx;
    var cy = options.cy;
    var radius = options.radius || 22;
    var group = add(parent, "g", {});

    add(group, "circle", { "class": "sch-stroke", cx: cx, cy: cy, r: radius });
    polyline(group, [[cx - 5, cy - 10], [cx + 5, cy - 10]]);
    polyline(group, [[cx, cy - 15], [cx, cy - 5]]);
    polyline(group, [[cx - 5, cy + 11], [cx + 5, cy + 11]]);

    return group;
  }

  /* Ground: a short stem into three shrinking horizontal bars. */
  function ground(parent, options) {
    var x = options.x;
    var y = options.y;
    var group = add(parent, "g", {});
    var bars = [[13, 0], [8, 5], [3.5, 10]];

    polyline(group, [[x, y], [x, y + 11]]);
    bars.forEach(function (bar) {
      polyline(group, [[x - bar[0], y + 11 + bar[1]], [x + bar[0], y + 11 + bar[1]]]);
    });

    return group;
  }

  function nodeDot(parent, options) {
    return add(parent, "circle", {
      "class": "sch-fill",
      cx: options.x,
      cy: options.y,
      r: options.radius || 3.4
    });
  }

  function label(parent, options) {
    var node = add(parent, "text", {
      "class": "sch-text"
        + (options.strong ? " is-strong" : "")
        + (options.value ? " is-value" : ""),
      x: options.x,
      y: options.y,
      "text-anchor": options.anchor || "start",
      "font-size": options.size || 12
    });
    node.textContent = options.text;
    return node;
  }

  /* The measured-value annotation: a copper tag pinned beside a node.
   * options.x is the left edge, or the centre when anchor is "middle". */
  function valueTag(parent, options) {
    var text = options.text;
    var height = 20;
    var width = Math.max(46, text.length * 7.2 + 16);
    var left = options.anchor === "middle" ? options.x - width / 2 : options.x;
    var group = add(parent, "g", {});

    add(group, "rect", {
      "class": "sch-tag-box",
      x: left,
      y: options.y,
      width: width,
      height: height,
      rx: 3
    });

    var node = add(group, "text", {
      "class": "sch-tag-text",
      x: left + width / 2,
      y: options.y + height / 2 + 4,
      "text-anchor": "middle",
      "font-size": 12
    });
    node.textContent = text;

    return group;
  }

  var symbols = {
    add: add,
    path: path,
    polyline: polyline,
    wire: wire,
    resistor: resistor,
    capacitor: capacitor,
    inductor: inductor,
    opamp: opamp,
    dcSource: dcSource,
    ground: ground,
    nodeDot: nodeDot,
    label: label,
    valueTag: valueTag
  };

  /* ---- circuit composition ------------------------------------------ */

  var VIEW = { width: 380, height: 300 };

  var GEOMETRY = {
    xSource: 100,
    xDivider: 260,
    yTop: 50,
    yBottom: 260,
    sourceCy: 155,
    sourceR: 22,
    r1Top: 78,
    r1Bottom: 134,
    yOut: 155,
    r2Top: 176,
    r2Bottom: 232,
    labelRight: 280,
    tagX: 280
  };

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  /* Draw the V0.0 divider. vout may be null, meaning "not simulated yet". */
  function drawDivider(svg, values) {
    var g = GEOMETRY;
    var vdd = values.vdd;
    var r1 = values.r1;
    var r2 = values.r2;
    var vout = values.vout === undefined ? null : values.vout;

    clear(svg);
    svg.setAttribute("viewBox", "0 0 " + VIEW.width + " " + VIEW.height);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      "Resistive divider: source V1 at " + formatEngineering(vdd, "V") +
      ", R1 " + formatEngineering(r1, "Ω") +
      " from node in to node out, R2 " + formatEngineering(r2, "Ω") +
      " from node out to ground" +
      (vout === null ? "." : ", measured v(out) " + formatEngineering(vout, "V") + ".")
    );

    // Wires: top rail, source branch, bottom rail, divider branch.
    wire(svg, g.xSource, g.yTop, g.xDivider, g.yTop);
    wire(svg, g.xSource, g.yTop, g.xSource, g.sourceCy - g.sourceR);
    wire(svg, g.xSource, g.sourceCy + g.sourceR, g.xSource, g.yBottom);
    wire(svg, g.xSource, g.yBottom, g.xDivider, g.yBottom);
    wire(svg, g.xDivider, g.yTop, g.xDivider, g.r1Top);
    wire(svg, g.xDivider, g.r1Bottom, g.xDivider, g.r2Top);
    wire(svg, g.xDivider, g.r2Bottom, g.xDivider, g.yBottom);

    // Components.
    dcSource(svg, { cx: g.xSource, cy: g.sourceCy, radius: g.sourceR });
    resistor(svg, { x: g.xDivider, y1: g.r1Top, y2: g.r1Bottom, peaks: 6 });
    resistor(svg, { x: g.xDivider, y1: g.r2Top, y2: g.r2Bottom, peaks: 6 });
    ground(svg, { x: (g.xSource + g.xDivider) / 2, y: g.yBottom });

    // Nodes.
    nodeDot(svg, { x: g.xDivider, y: g.yTop });
    nodeDot(svg, { x: g.xDivider, y: g.yOut });
    label(svg, { x: g.xDivider - 12, y: g.yTop - 9, text: "in", anchor: "end", strong: true });
    label(svg, { x: g.xDivider - 12, y: g.yOut + 4, text: "out", anchor: "end", strong: true });

    // Reference designators and live values.
    var leftEdge = g.xSource - g.sourceR - 13;
    label(svg, { x: leftEdge, y: g.sourceCy - 2, text: "V1", anchor: "end", strong: true });
    label(svg, { value: true,
      x: leftEdge, y: g.sourceCy + 14, text: formatEngineering(vdd, "V"), anchor: "end"
    });

    label(svg, { x: g.labelRight, y: 100, text: "R1", strong: true });
    label(svg, { value: true, x: g.labelRight, y: 116, text: formatEngineering(r1, "Ω") });

    label(svg, { x: g.labelRight, y: 198, text: "R2", strong: true });
    label(svg, { value: true, x: g.labelRight, y: 214, text: formatEngineering(r2, "Ω") });

    // Only after a real run does the out node carry a measured value.
    if (vout !== null && isFinite(vout)) {
      valueTag(svg, { x: g.tagX, y: g.yOut - 10, text: formatEngineering(vout, "V") });
    }

    return svg;
  }

  var RC_VIEW = { width: 420, height: 300 };

  var RC_GEOMETRY = {
    xSource: 110,
    xOut: 310,
    yTop: 72,
    yBottom: 250,
    sourceCy: 155,
    sourceR: 22,
    r1Left: 180,
    r1Right: 240,
    inDotX: 150,
    outDotX: 270,
    capCentre: 155
  };

  /* Draw the V0.1 RC low-pass. f3db may be null, meaning "not swept yet". */
  function drawRCLowpass(svg, values) {
    var g = RC_GEOMETRY;
    var r = values.r;
    var c = values.c;
    var f3db = values.f3db === undefined ? null : values.f3db;

    clear(svg);
    svg.setAttribute("viewBox", "0 0 " + RC_VIEW.width + " " + RC_VIEW.height);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      "RC low-pass filter: a 1 volt AC source V1 drives node in, R1 " +
      formatEngineering(r, "Ω") + " in series to node out, C1 " +
      formatEngineering(c, "F") + " from out to ground" +
      (f3db === null
        ? "."
        : ", measured 3 dB corner " + formatEngineering(f3db, "Hz") + ".")
    );

    // Source branch and rails.
    wire(svg, g.xSource, g.yTop, g.r1Left, g.yTop);
    wire(svg, g.r1Right, g.yTop, g.xOut, g.yTop);
    wire(svg, g.xSource, g.yTop, g.xSource, g.sourceCy - g.sourceR);
    wire(svg, g.xSource, g.sourceCy + g.sourceR, g.xSource, g.yBottom);
    wire(svg, g.xSource, g.yBottom, g.xOut, g.yBottom);

    // Components: series R along the top rail, shunt C down to ground.
    dcSource(svg, { cx: g.xSource, cy: g.sourceCy, radius: g.sourceR });
    resistor(svg, {
      orientation: "horizontal",
      y: g.yTop,
      x1: g.r1Left,
      x2: g.r1Right,
      peaks: 6,
      amplitude: 10
    });
    capacitor(svg, {
      x: g.xOut,
      y1: g.yTop,
      y2: g.yBottom,
      centre: g.capCentre
    });
    ground(svg, { x: (g.xSource + g.xOut) / 2, y: g.yBottom });

    // Nodes, labelled below the rail so the reference designators sit above.
    nodeDot(svg, { x: g.inDotX, y: g.yTop });
    nodeDot(svg, { x: g.outDotX, y: g.yTop });
    label(svg, { x: g.inDotX, y: g.yTop + 20, text: "in", anchor: "middle", strong: true });
    label(svg, { x: g.outDotX, y: g.yTop + 20, text: "out", anchor: "middle", strong: true });

    // Reference designators and live values.
    var leftEdge = g.xSource - g.sourceR - 13;
    label(svg, { x: leftEdge, y: g.sourceCy - 2, text: "V1", anchor: "end", strong: true });
    label(svg, { x: leftEdge, y: g.sourceCy + 14, text: "AC 1 V", anchor: "end" });

    var midR = (g.r1Left + g.r1Right) / 2;
    label(svg, { x: midR, y: g.yTop + 44, text: "R1", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midR, y: g.yTop + 58, text: formatEngineering(r, "Ω"), anchor: "middle"
    });

    var capEdge = g.xOut + 24;
    label(svg, { x: capEdge, y: g.capCentre - 2, text: "C1", strong: true });
    label(svg, { value: true, x: capEdge, y: g.capCentre + 14, text: formatEngineering(c, "F") });

    // Only a completed sweep may put a corner frequency on the figure.
    if (f3db !== null && isFinite(f3db)) {
      wire(svg, g.outDotX, g.yTop - 6, g.outDotX, g.yTop - 24);
      valueTag(svg, {
        x: g.outDotX,
        y: g.yTop - 44,
        anchor: "middle",
        text: "-3 dB @ " + formatEngineering(f3db, "Hz")
      });
    }

    return svg;
  }

  /* Shared: start a compose function off with a clean, framed canvas. */
  function begin(svg, width, height, description) {
    clear(svg);
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", description);
    return svg;
  }

  /* Shared: the measured-value tag, pinned above a node with a short leader. */
  function annotate(svg, x, nodeY, text) {
    wire(svg, x, nodeY - 6, x, nodeY - 24);
    valueTag(svg, { x: x, y: nodeY - 44, anchor: "middle", text: text });
  }

  /* ---- RC high-pass: series C, shunt R ------------------------------ */

  var HP_GEOMETRY = {
    xSource: 110, xOut: 310, yTop: 72, yBottom: 250,
    sourceCy: 155, sourceR: 22,
    c1Left: 180, c1Right: 240,
    r1Top: 126, r1Bottom: 182,
    inDotX: 150, outDotX: 270
  };

  function drawRCHighpass(svg, values) {
    var g = HP_GEOMETRY;
    var f3db = values.f3db === undefined ? null : values.f3db;

    begin(svg, 420, 300,
      "RC high-pass filter: a 1 volt AC source drives node in, C1 " +
      formatEngineering(values.c, "F") + " in series to node out, R1 " +
      formatEngineering(values.r, "Ω") + " from out to ground" +
      (f3db === null ? "." :
        ", measured 3 dB corner " + formatEngineering(f3db, "Hz") + "."));

    wire(svg, g.xSource, g.yTop, g.c1Left, g.yTop);
    wire(svg, g.c1Right, g.yTop, g.xOut, g.yTop);
    wire(svg, g.xSource, g.yTop, g.xSource, g.sourceCy - g.sourceR);
    wire(svg, g.xSource, g.sourceCy + g.sourceR, g.xSource, g.yBottom);
    wire(svg, g.xSource, g.yBottom, g.xOut, g.yBottom);
    wire(svg, g.xOut, g.yTop, g.xOut, g.r1Top);
    wire(svg, g.xOut, g.r1Bottom, g.xOut, g.yBottom);

    dcSource(svg, { cx: g.xSource, cy: g.sourceCy, radius: g.sourceR });
    capacitor(svg, {
      orientation: "horizontal", y: g.yTop, x1: g.c1Left, x2: g.c1Right
    });
    resistor(svg, { x: g.xOut, y1: g.r1Top, y2: g.r1Bottom, peaks: 6 });
    ground(svg, { x: (g.xSource + g.xOut) / 2, y: g.yBottom });

    nodeDot(svg, { x: g.inDotX, y: g.yTop });
    nodeDot(svg, { x: g.outDotX, y: g.yTop });
    label(svg, { x: g.inDotX, y: g.yTop + 20, text: "in", anchor: "middle", strong: true });
    label(svg, { x: g.outDotX, y: g.yTop + 20, text: "out", anchor: "middle", strong: true });

    var leftEdge = g.xSource - g.sourceR - 13;
    label(svg, { x: leftEdge, y: g.sourceCy - 2, text: "V1", anchor: "end", strong: true });
    label(svg, { x: leftEdge, y: g.sourceCy + 14, text: "AC 1 V", anchor: "end" });

    var midC = (g.c1Left + g.c1Right) / 2;
    label(svg, { x: midC, y: g.yTop + 44, text: "C1", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midC, y: g.yTop + 58, text: formatEngineering(values.c, "F"), anchor: "middle"
    });

    label(svg, { x: g.xOut + 24, y: g.sourceCy - 2, text: "R1", strong: true });
    label(svg, { value: true, x: g.xOut + 24, y: g.sourceCy + 14, text: formatEngineering(values.r, "Ω") });

    if (f3db !== null && isFinite(f3db)) {
      annotate(svg, g.outDotX, g.yTop, "-3 dB @ " + formatEngineering(f3db, "Hz"));
    }
    return svg;
  }

  /* ---- Series RLC band-pass: L and C in the rail, R to ground -------- */

  var RLC_GEOMETRY = {
    xSource: 90, xOut: 390, yTop: 72, yBottom: 250,
    sourceCy: 155, sourceR: 22,
    l1Left: 150, l1Right: 215,
    c1Left: 250, c1Right: 290,
    r1Top: 130, r1Bottom: 190,
    inDotX: 115, midDotX: 232, outDotX: 340
  };

  function drawRLCBandpass(svg, values) {
    var g = RLC_GEOMETRY;
    var f0 = values.f0 === undefined ? null : values.f0;

    begin(svg, 480, 300,
      "Series RLC band-pass: a 1 volt AC source drives L1 " +
      formatEngineering(values.l, "H") + " in series with C1 " +
      formatEngineering(values.c, "F") + " into R1 " +
      formatEngineering(values.r, "Ω") + " to ground, output taken across R1" +
      (f0 === null ? "." :
        ", measured resonance " + formatEngineering(f0, "Hz") + "."));

    wire(svg, g.xSource, g.yTop, g.l1Left, g.yTop);
    wire(svg, g.l1Right, g.yTop, g.c1Left, g.yTop);
    wire(svg, g.c1Right, g.yTop, g.xOut, g.yTop);
    wire(svg, g.xSource, g.yTop, g.xSource, g.sourceCy - g.sourceR);
    wire(svg, g.xSource, g.sourceCy + g.sourceR, g.xSource, g.yBottom);
    wire(svg, g.xSource, g.yBottom, g.xOut, g.yBottom);
    wire(svg, g.xOut, g.yTop, g.xOut, g.r1Top);
    wire(svg, g.xOut, g.r1Bottom, g.xOut, g.yBottom);

    dcSource(svg, { cx: g.xSource, cy: g.sourceCy, radius: g.sourceR });
    inductor(svg, {
      orientation: "horizontal", y: g.yTop, x1: g.l1Left, x2: g.l1Right, humps: 4
    });
    capacitor(svg, {
      orientation: "horizontal", y: g.yTop, x1: g.c1Left, x2: g.c1Right
    });
    resistor(svg, { x: g.xOut, y1: g.r1Top, y2: g.r1Bottom, peaks: 6 });
    ground(svg, { x: (g.xSource + g.xOut) / 2, y: g.yBottom });

    nodeDot(svg, { x: g.inDotX, y: g.yTop });
    nodeDot(svg, { x: g.midDotX, y: g.yTop });
    nodeDot(svg, { x: g.outDotX, y: g.yTop });
    label(svg, { x: g.inDotX, y: g.yTop + 20, text: "in", anchor: "middle", strong: true });
    label(svg, { x: g.midDotX, y: g.yTop + 20, text: "nlc", anchor: "middle", strong: true });
    label(svg, { x: g.outDotX, y: g.yTop + 20, text: "out", anchor: "middle", strong: true });

    var leftEdge = g.xSource - g.sourceR - 13;
    label(svg, { x: leftEdge, y: g.sourceCy - 2, text: "V1", anchor: "end", strong: true });
    label(svg, { x: leftEdge, y: g.sourceCy + 14, text: "AC 1 V", anchor: "end" });

    var midL = (g.l1Left + g.l1Right) / 2;
    label(svg, { x: midL, y: g.yTop + 52, text: "L1", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midL, y: g.yTop + 66, text: formatEngineering(values.l, "H"), anchor: "middle"
    });

    var midC = (g.c1Left + g.c1Right) / 2;
    label(svg, { x: midC, y: g.yTop + 52, text: "C1", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midC, y: g.yTop + 66, text: formatEngineering(values.c, "F"), anchor: "middle"
    });

    label(svg, { x: g.xOut + 22, y: g.sourceCy - 2, text: "R1", strong: true });
    label(svg, { value: true, x: g.xOut + 22, y: g.sourceCy + 14, text: formatEngineering(values.r, "Ω") });

    if (f0 !== null && isFinite(f0)) {
      annotate(svg, g.outDotX, g.yTop, "f0 " + formatEngineering(f0, "Hz"));
    }
    return svg;
  }

  /* ---- Inverting amplifier around a single-pole op-amp -------------- */

  var AMP_GEOMETRY = {
    xSource: 90, sourceCy: 215, sourceR: 20,
    yIn: 171, yPlus: 199, yRail: 265,
    inDotX: 115, rinLeft: 125, rinRight: 185, vmX: 205,
    ampX: 225, ampY: 185, ampWidth: 68, ampHeight: 86,
    yFeedback: 105, rfLeft: 240, rfRight: 310,
    xOutRail: 385, outDotX: 345, plusDropX: 190
  };

  function drawInvertingAmp(svg, values) {
    var g = AMP_GEOMETRY;
    var gain = values.gain_db === undefined ? null : values.gain_db;

    begin(svg, 470, 300,
      "Inverting amplifier: Rin " + formatEngineering(values.rin, "Ω") +
      " from node in to the inverting node vm, Rf " +
      formatEngineering(values.rf, "Ω") +
      " feeding back from vm to out, non-inverting input grounded, op-amp with " +
      "open-loop gain " + formatEngineering(values.a0, "") + " and gain-bandwidth " +
      formatEngineering(values.gbw, "Hz") +
      (gain === null ? "." : ", measured midband gain " + gain.toFixed(2) + " dB."));

    // Source into the input rail.
    wire(svg, g.xSource, g.yIn, g.rinLeft, g.yIn);
    wire(svg, g.xSource, g.yIn, g.xSource, g.sourceCy - g.sourceR);
    wire(svg, g.xSource, g.sourceCy + g.sourceR, g.xSource, g.yRail);
    wire(svg, g.xSource, g.yRail, g.plusDropX, g.yRail);

    // Rin into the summing node, then into the inverting input.
    resistor(svg, {
      orientation: "horizontal", y: g.yIn, x1: g.rinLeft, x2: g.rinRight,
      peaks: 6, amplitude: 10
    });
    wire(svg, g.rinRight, g.yIn, g.ampX, g.yIn);

    // Non-inverting input pulled down to the ground rail.
    wire(svg, g.ampX, g.yPlus, g.plusDropX, g.yPlus);
    wire(svg, g.plusDropX, g.yPlus, g.plusDropX, g.yRail);

    // Feedback over the top, from vm across Rf to the output rail.
    wire(svg, g.vmX, g.yIn, g.vmX, g.yFeedback);
    wire(svg, g.vmX, g.yFeedback, g.rfLeft, g.yFeedback);
    resistor(svg, {
      orientation: "horizontal", y: g.yFeedback, x1: g.rfLeft, x2: g.rfRight,
      peaks: 6, amplitude: 10
    });
    wire(svg, g.rfRight, g.yFeedback, g.xOutRail, g.yFeedback);
    wire(svg, g.xOutRail, g.yFeedback, g.xOutRail, g.ampY);
    wire(svg, g.ampX + g.ampWidth, g.ampY, g.xOutRail, g.ampY);

    dcSource(svg, { cx: g.xSource, cy: g.sourceCy, radius: g.sourceR });
    opamp(svg, {
      x: g.ampX, y: g.ampY, width: g.ampWidth, height: g.ampHeight
    });
    ground(svg, { x: (g.xSource + g.plusDropX) / 2, y: g.yRail });

    nodeDot(svg, { x: g.inDotX, y: g.yIn });
    nodeDot(svg, { x: g.vmX, y: g.yIn });
    nodeDot(svg, { x: g.outDotX, y: g.ampY });
    label(svg, { x: g.inDotX, y: g.yIn + 21, text: "in", anchor: "middle", strong: true });
    label(svg, { x: g.vmX, y: g.yIn + 21, text: "vm", anchor: "middle", strong: true });
    label(svg, { x: g.outDotX, y: g.ampY - 14, text: "out", anchor: "middle", strong: true });

    var leftEdge = g.xSource - g.sourceR - 13;
    label(svg, { x: leftEdge, y: g.sourceCy - 2, text: "V1", anchor: "end", strong: true });
    label(svg, { x: leftEdge, y: g.sourceCy + 14, text: "AC 1 V", anchor: "end" });

    var midRin = (g.rinLeft + g.rinRight) / 2;
    label(svg, { x: midRin, y: g.yIn - 33, text: "Rin", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midRin, y: g.yIn - 19, text: formatEngineering(values.rin, "Ω"), anchor: "middle"
    });

    var midRf = (g.rfLeft + g.rfRight) / 2;
    label(svg, { x: midRf, y: g.yFeedback - 33, text: "Rf", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midRf, y: g.yFeedback - 19, text: formatEngineering(values.rf, "Ω"),
      anchor: "middle"
    });

    // The macromodel is a component too, so it carries its live values.
    label(svg, { value: true,
      x: g.ampX + 15, y: g.yRail - 17,
      text: "A0 " + formatEngineering(values.a0, ""), size: 11
    });
    label(svg, { value: true,
      x: g.ampX + 15, y: g.yRail - 3,
      text: "GBW " + formatEngineering(values.gbw, "Hz"), size: 11
    });

    if (gain !== null && isFinite(gain)) {
      wire(svg, g.outDotX, g.ampY + 6, g.outDotX, g.ampY + 20);
      valueTag(svg, {
        x: g.outDotX, y: g.ampY + 20, anchor: "middle",
        text: gain.toFixed(2) + " dB"
      });
    }
    return svg;
  }

  window.drawDivider = drawDivider;
  window.drawRCLowpass = drawRCLowpass;
  window.drawRCHighpass = drawRCHighpass;
  window.drawRLCBandpass = drawRLCBandpass;
  window.drawInvertingAmp = drawInvertingAmp;
  window.formatEngineering = formatEngineering;
  window.FaradaemSymbols = symbols;
})(window, document);
