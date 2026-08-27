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
      "stroke-linecap": "butt",
      "stroke-linejoin": "round"
    });
  }

  /* Orthogonal wire: a straight run between two points. */
  function wire(parent, x1, y1, x2, y2) {
    return polyline(parent, [[x1, y1], [x2, y2]]);
  }

  /* Every symbol group declares what it is and where its terminals are, so
   * a checker can verify mechanically that no wire transits a symbol body
   * and that every terminal is actually connected. The attributes are in
   * the group's own coordinates; a reader maps them through the CTM. */
  function tagSymbol(group, kind, terminals) {
    group.setAttribute("data-sym", kind);
    group.setAttribute("data-terminals", terminals.map(function (point) {
      return point[0] + "," + point[1];
    }).join(" "));
    return group;
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
    var group = add(parent, "g", {});

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

    polyline(group, points);
    return tagSymbol(group, "resistor", [at(start, axis), at(end, axis)]);
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

    return tagSymbol(group, "capacitor", [at(start, axis), at(end, axis)]);
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

    var group = add(parent, "g", {});
    path(group, d);
    return tagSymbol(group, "inductor", horizontal
      ? [[start, axis], [end, axis]]
      : [[axis, start], [axis, end]]);
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

    return tagSymbol(group, "opamp", [
      [x, invertingY], [x, nonInvertingY], [x + width, y]
    ]);
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

    return tagSymbol(group, "dcsource",
      [[cx, cy - radius], [cx, cy + radius]]);
  }

  /* DC current source: circle with the arrow pointing the way the current
   * flows. Drawn inline three times before it became a primitive. */
  function isource(parent, options) {
    var cx = options.cx;
    var cy = options.cy;
    var radius = options.radius || 22;
    var group = add(parent, "g", {});

    add(group, "circle", { "class": "sch-stroke", cx: cx, cy: cy, r: radius });
    polyline(group, [[cx, cy - 12], [cx, cy + 12]]);
    polyline(group, [[cx - 5, cy + 5], [cx, cy + 12], [cx + 5, cy + 5]]);

    return tagSymbol(group, "isource",
      [[cx, cy - radius], [cx, cy + radius]]);
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

    return tagSymbol(group, "ground", [[x, y]]);
  }

  /* Enhancement-mode NMOS, gate on the left, drain up and source down.
   * The channel is drawn as three dashes, which is what marks it enhancement
   * mode, and the bulk arrow points at the channel, which is what marks it
   * n-type. Bulk is tied into the source lead because that is how the netlist
   * wires it. (x, y) is the centre of the channel.
   *
   * Terminals, for a caller placing wires: the gate lead starts at
   * x - gap - lead, and the drain and source leads both leave on the column
   * x + lead, at y - half - 14 and y + half + 14. */
  function nmos(parent, options) {
    var x = options.x;
    var y = options.y;
    var half = options.half || 24;
    var gap = options.gap || 10;
    var lead = options.lead || 26;
    var group = add(parent, "g", {});

    var gateX = x - gap;
    var railX = x + lead;
    var drainY = y - half + 7;
    var sourceY = y + half - 7;

    // Gate plate and its lead.
    polyline(group, [[gateX, y - half], [gateX, y + half]]);
    polyline(group, [[gateX - lead, y], [gateX, y]]);

    // Channel: three dashes with the gaps an enhancement device is drawn with.
    polyline(group, [[x, y - half], [x, y - half + 14]]);
    polyline(group, [[x, y - 7], [x, y + 7]]);
    polyline(group, [[x, y + half - 14], [x, y + half]]);

    // Drain lead: out to the rail column, then up. One polyline per lead,
    // so the turn is a join rather than two ends meeting.
    polyline(group, [[x, drainY], [railX, drainY], [railX, y - half - 14]]);

    // Source lead: out to the rail column, then down.
    polyline(group, [[x, sourceY], [railX, sourceY], [railX, y + half + 14]]);

    // Bulk, tied into the source lead, arrow pointing at the channel.
    polyline(group, [[x, y], [railX, y], [railX, sourceY]]);
    polyline(group, [[x + 9, y - 5], [x, y], [x + 9, y + 5]]);

    if (options.name) {
      // The instance name ties this symbol to the deck's X-card, so the
      // bias annotations know which measured device they belong to.
      group.setAttribute("data-device", options.name);
    }
    return tagSymbol(group, "nmos", [
      [gateX - lead, y],                // gate
      [railX, y - half - 14],           // drain
      [railX, y + half + 14]            // source
    ]);
  }

  /* Enhancement-mode PMOS: the nmos mirrored top for bottom. Source and bulk
   * sit at the top, toward the supply, drain leaves at the bottom, and the
   * bulk arrow points away from the channel, which is what marks it p-type.
   * (x, y) is the centre of the channel. */
  function pmos(parent, options) {
    var x = options.x;
    var y = options.y;
    var half = options.half || 24;
    var gap = options.gap || 10;
    var lead = options.lead || 26;
    var group = add(parent, "g", {});

    var gateX = x - gap;
    var railX = x + lead;
    var sourceY = y - half + 7;
    var drainY = y + half - 7;

    // Gate plate and its lead.
    polyline(group, [[gateX, y - half], [gateX, y + half]]);
    polyline(group, [[gateX - lead, y], [gateX, y]]);

    // Channel: three dashes.
    polyline(group, [[x, y - half], [x, y - half + 14]]);
    polyline(group, [[x, y - 7], [x, y + 7]]);
    polyline(group, [[x, y + half - 14], [x, y + half]]);

    // Source lead: out to the rail column, then up toward the supply.
    polyline(group, [[x, sourceY], [railX, sourceY], [railX, y - half - 14]]);

    // Drain lead: out to the rail column, then down.
    polyline(group, [[x, drainY], [railX, drainY], [railX, y + half + 14]]);

    // Bulk, tied into the source lead, arrow pointing away from the channel.
    polyline(group, [[x, y], [railX, y], [railX, sourceY]]);
    polyline(group, [[railX - 9, y - 5], [railX, y], [railX - 9, y + 5]]);

    if (options.name) {
      group.setAttribute("data-device", options.name);
    }
    return tagSymbol(group, "pmos", [
      [gateX - lead, y],                // gate
      [railX, y - half - 14],           // source
      [railX, y + half + 14]            // drain
    ]);
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
        + (options.node ? " is-node" : "")
        + (options.value ? " is-value" : ""),
      x: options.x,
      y: options.y,
      "text-anchor": options.anchor || "start",
      "font-size": options.size || 12
    });
    node.textContent = options.text;
    return node;
  }

  /* The measured-value annotation: a filled accent tag pinned beside a node.
   * Square, like every other box in the app.
   * options.x is the left edge, or the centre when anchor is "middle". */
  function valueTag(parent, options) {
    var node = add(parent, "text", {
      "class": "sch-tag-text",
      x: options.x,
      y: options.y + 14,
      "text-anchor": options.anchor === "middle" ? "middle" : "start",
      "font-size": 12
    });
    node.textContent = options.text;
    return node;
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
    nmos: nmos,
    pmos: pmos,
    dcSource: dcSource,
    isource: isource,
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
    label(svg, { x: g.xDivider - 12, y: g.yTop - 9, text: "in", anchor: "end", node: true });
    label(svg, { x: g.xDivider - 12, y: g.yOut + 4, text: "out", anchor: "end", node: true });

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
    label(svg, { x: g.inDotX, y: g.yTop + 20, text: "in", anchor: "middle", node: true });
    label(svg, { x: g.outDotX, y: g.yTop + 20, text: "out", anchor: "middle", node: true });

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
    label(svg, { x: g.inDotX, y: g.yTop + 20, text: "in", anchor: "middle", node: true });
    label(svg, { x: g.outDotX, y: g.yTop + 20, text: "out", anchor: "middle", node: true });

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
    label(svg, { x: g.inDotX, y: g.yTop + 20, text: "in", anchor: "middle", node: true });
    label(svg, { x: g.midDotX, y: g.yTop + 20, text: "nlc", anchor: "middle", node: true });
    label(svg, { x: g.outDotX, y: g.yTop + 20, text: "out", anchor: "middle", node: true });

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
    label(svg, { x: g.inDotX, y: g.yIn + 21, text: "in", anchor: "middle", node: true });
    label(svg, { x: g.vmX, y: g.yIn + 21, text: "vm", anchor: "middle", node: true });
    label(svg, { x: g.outDotX, y: g.ampY - 14, text: "out", anchor: "middle", node: true });

    var leftEdge = g.xSource - g.sourceR - 13;
    label(svg, { x: leftEdge, y: g.sourceCy - 2, text: "V1", anchor: "end", strong: true });
    label(svg, { x: leftEdge, y: g.sourceCy + 14, text: "AC 1 V", anchor: "end" });

    var midRin = (g.rinLeft + g.rinRight) / 2;
    label(svg, { x: midRin, y: g.yIn - 35, text: "Rin", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midRin, y: g.yIn - 19, text: formatEngineering(values.rin, "Ω"), anchor: "middle"
    });

    var midRf = (g.rfLeft + g.rfRight) / 2;
    label(svg, { x: midRf, y: g.yFeedback - 35, text: "Rf", anchor: "middle", strong: true });
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

  /* ---- Two-pole op-amp, drawn with its loop break shown -------------- */

  /* Same inverting frame as the single-pole amp, plus the mark that matters
   * here: where the loop is opened to measure its gain. The break is drawn
   * because it is the whole method, not an implementation detail. */
  function drawTwopoleAmp(svg, values) {
    var g = AMP_GEOMETRY;
    var pm = values.phase_margin === undefined ? null : values.phase_margin;

    begin(svg, 470, 300,
      "Two-pole op-amp in an inverting configuration: Rin " +
      formatEngineering(values.rin, "\u03a9") + " from node in to the inverting " +
      "node vm, Rf " + formatEngineering(values.rf, "\u03a9") +
      " feeding back from vm to out, open-loop gain " +
      formatEngineering(values.a0, "") + ", gain-bandwidth " +
      formatEngineering(values.gbw, "Hz") + ", second pole at " +
      formatEngineering(values.fp2, "Hz") +
      (pm === null ? "." : ", measured phase margin " + pm.toFixed(1) + " degrees."));

    // Source into the input rail.
    wire(svg, g.xSource, g.yIn, g.rinLeft, g.yIn);
    wire(svg, g.xSource, g.yIn, g.xSource, g.sourceCy - g.sourceR);
    wire(svg, g.xSource, g.sourceCy + g.sourceR, g.xSource, g.yRail);
    wire(svg, g.xSource, g.yRail, g.plusDropX, g.yRail);

    resistor(svg, {
      orientation: "horizontal", y: g.yIn, x1: g.rinLeft, x2: g.rinRight,
      peaks: 6, amplitude: 10
    });
    wire(svg, g.rinRight, g.yIn, g.ampX, g.yIn);

    wire(svg, g.ampX, g.yPlus, g.plusDropX, g.yPlus);
    wire(svg, g.plusDropX, g.yPlus, g.plusDropX, g.yRail);

    // Feedback over the top.
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
    opamp(svg, { x: g.ampX, y: g.ampY, width: g.ampWidth, height: g.ampHeight });
    ground(svg, { x: (g.xSource + g.plusDropX) / 2, y: g.yRail });

    nodeDot(svg, { x: g.inDotX, y: g.yIn });
    nodeDot(svg, { x: g.vmX, y: g.yIn });
    nodeDot(svg, { x: g.outDotX, y: g.ampY });
    label(svg, { x: g.inDotX, y: g.yIn + 21, text: "in", anchor: "middle", node: true });
    label(svg, { x: g.vmX, y: g.yIn + 21, text: "vm", anchor: "middle", node: true });
    label(svg, { x: g.outDotX, y: g.ampY - 14, text: "out", anchor: "middle", node: true });

    var leftEdge = g.xSource - g.sourceR - 13;
    label(svg, { x: leftEdge, y: g.sourceCy - 2, text: "V1", anchor: "end", strong: true });
    label(svg, { x: leftEdge, y: g.sourceCy + 14, text: "AC 1 V", anchor: "end" });

    var midRin = (g.rinLeft + g.rinRight) / 2;
    label(svg, { x: midRin, y: g.yIn - 35, text: "Rin", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midRin, y: g.yIn - 19, text: formatEngineering(values.rin, "\u03a9"), anchor: "middle"
    });

    var midRf = (g.rfLeft + g.rfRight) / 2;
    label(svg, { x: midRf, y: g.yFeedback - 35, text: "Rf", anchor: "middle", strong: true });
    label(svg, { value: true,
      x: midRf, y: g.yFeedback - 19, text: formatEngineering(values.rf, "\u03a9"), anchor: "middle"
    });

    // The model is a component, so it carries its three live values.
    label(svg, { value: true,
      x: g.ampX + 24, y: g.yRail - 31,
      text: "A0 " + formatEngineering(values.a0, ""), size: 11
    });
    label(svg, { value: true,
      x: g.ampX + 15, y: g.yRail - 17,
      text: "GBW " + formatEngineering(values.gbw, "Hz"), size: 11
    });
    label(svg, { value: true,
      x: g.ampX + 15, y: g.yRail - 3,
      text: "fp2 " + formatEngineering(values.fp2, "Hz"), size: 11
    });

    // The loop break. Two short strokes across the feedback path, marking the
    // point the loop gain is measured at.
    var breakX = g.rfRight + 18;
    polyline(svg, [[breakX - 5, g.yFeedback - 9], [breakX + 1, g.yFeedback + 9]]);
    polyline(svg, [[breakX + 5, g.yFeedback - 9], [breakX + 11, g.yFeedback + 9]]);
    label(svg, {
      x: breakX + 3, y: g.yFeedback - 16, text: "loop", anchor: "middle", size: 11
    });

    if (pm !== null && isFinite(pm)) {
      wire(svg, g.outDotX, g.ampY + 6, g.outDotX, g.ampY + 20);
      valueTag(svg, {
        x: g.outDotX, y: g.ampY + 20, anchor: "middle",
        text: "PM " + pm.toFixed(1) + "\u00b0"
      });
    }
    return svg;
  }

  /* ---- SKY130 NFET common-source amplifier -------------------------- */

  var CS_GEOMETRY = {
    yVdd: 44, yOut: 140, yDev: 196, yBottom: 272,
    xGate: 96, gateCy: 224, gateR: 20,
    chanX: 232, xRail: 258, xCL: 350,
    rdTop: 64, rdBottom: 120,
    railEnd: 330, gateDotX: 150, capCentre: 200
  };

  /* Draw the V0.2 common-source stage. gain_db may be null, meaning "not
   * swept yet". W and L arrive in metres and are shown in engineering units,
   * so 1.5e-7 reads as 150 nm rather than as a raw exponent. */
  function drawNfetCsAmp(svg, values) {
    var g = CS_GEOMETRY;
    var gain = values.gain_db === undefined ? null : values.gain_db;

    begin(svg, 470, 330,
      "SKY130 NFET common-source amplifier: an n-channel transistor of width " +
      formatEngineering(values.w, "m") + " and length " +
      formatEngineering(values.l, "m") + ", gate biased at " +
      formatEngineering(values.vgs, "V") + " and carrying the 1 volt AC " +
      "excitation, source and body grounded, drain loaded by RD " +
      formatEngineering(values.rd, "\u03a9") + " to a " +
      formatEngineering(values.vdd, "V") + " supply and by CL " +
      formatEngineering(values.cl, "F") + " to ground, output taken at the drain" +
      (gain === null ? "." : ", measured midband gain " + gain.toFixed(2) + " dB."));

    // Supply rail into RD, then down to the drain.
    wire(svg, g.xRail, g.yVdd, g.railEnd, g.yVdd);
    wire(svg, g.xRail, g.yVdd, g.xRail, g.rdTop);
    wire(svg, g.xRail, g.rdBottom, g.xRail, g.yDev - 24 - 14);

    // Output node across to the load capacitor.
    wire(svg, g.xRail, g.yOut, g.xCL, g.yOut);

    // Source down to the ground rail, and the gate drive in from the left.
    wire(svg, g.xRail, g.yDev + 24 + 14, g.xRail, g.yBottom);
    wire(svg, g.chanX - 10 - 26, g.yDev, g.xGate, g.yDev);
    wire(svg, g.xGate, g.yDev, g.xGate, g.gateCy - g.gateR);
    wire(svg, g.xGate, g.gateCy + g.gateR, g.xGate, g.yBottom);
    wire(svg, g.xGate, g.yBottom, g.xCL, g.yBottom);

    // Components.
    resistor(svg, { x: g.xRail, y1: g.rdTop, y2: g.rdBottom, peaks: 6 });
    nmos(svg, { x: g.chanX, y: g.yDev, name: "M1" });
    capacitor(svg, {
      x: g.xCL, y1: g.yOut, y2: g.yBottom, centre: g.capCentre
    });
    dcSource(svg, { cx: g.xGate, cy: g.gateCy, radius: g.gateR });
    ground(svg, { x: 170, y: g.yBottom });

    // Nodes.
    nodeDot(svg, { x: g.xRail, y: g.yOut });
    nodeDot(svg, { x: g.gateDotX, y: g.yDev });
    label(svg, { x: g.xRail - 12, y: g.yOut + 4, text: "out", anchor: "end", node: true });
    label(svg, { x: g.gateDotX, y: g.yDev - 12, text: "g", anchor: "middle", node: true });

    // The supply is a rail, so it carries its label rather than a symbol.
    label(svg, { x: g.railEnd + 8, y: g.yVdd - 4, text: "VDD", strong: true });
    label(svg, { value: true,
      x: g.railEnd + 8, y: g.yVdd + 12, text: formatEngineering(values.vdd, "V")
    });

    label(svg, { x: g.xRail + 16, y: 88, text: "RD", strong: true });
    label(svg, { value: true,
      x: g.xRail + 16, y: 104, text: formatEngineering(values.rd, "\u03a9")
    });

    label(svg, { x: g.xCL + 16, y: g.capCentre - 2, text: "CL", strong: true });
    label(svg, { value: true,
      x: g.xCL + 16, y: g.capCentre + 14, text: formatEngineering(values.cl, "F")
    });

    // Bias and excitation on separate lines: one run of text long enough to
    // hold both is wide enough to fall off the left edge of the frame.
    var gateEdge = g.xGate - g.gateR - 13;
    label(svg, { x: gateEdge, y: g.gateCy - 18, text: "Vg", anchor: "end", strong: true });
    label(svg, { value: true,
      x: gateEdge, y: g.gateCy - 2,
      text: formatEngineering(values.vgs, "V"), anchor: "end"
    });
    label(svg, { value: true,
      x: gateEdge, y: g.gateCy + 14, text: "+ AC 1 V", anchor: "end"
    });

    // The device is a component too, so it carries its live geometry.
    var devEdge = g.chanX - 36;
    label(svg, { x: devEdge, y: 236, text: "XM1", anchor: "end", strong: true });
    label(svg, { value: true,
      x: devEdge, y: 250, text: "W " + formatEngineering(values.w, "m"),
      anchor: "end", size: 11
    });
    label(svg, { value: true,
      x: devEdge, y: 264, text: "L " + formatEngineering(values.l, "m"),
      anchor: "end", size: 11
    });

    // Only a completed sweep may put a gain on the figure. The tag hangs below
    // the output node: RD occupies the space above it.
    if (gain !== null && isFinite(gain)) {
      wire(svg, 305, g.yOut, 305, g.yOut + 18);
      valueTag(svg, {
        x: 305, y: g.yOut + 18, anchor: "middle", text: gain.toFixed(2) + " dB"
      });
    }
    return svg;
  }

  /* ---- SKY130 5T OTA -------------------------------------------------- */

  /* One stage: the two-stage op-amp's front half, ending at M2's drain.
   * Same layout discipline: strict columns, labels in clear space, the one
   * crossing at the mirror gate tie. The DC servo is not drawn. */
  function drawOta5t(svg, values) {
    var yVdd = 40, yGnd = 420;
    var pY = 120, nY = 240, bY = 352;
    var pm = values.phase_margin === undefined ? null : values.phase_margin;

    begin(svg, 520, 448,
      "SKY130 five-transistor OTA: NMOS input pair of width " +
      formatEngineering(values.wpair, "m") + " under a PMOS mirror load of width " +
      formatEngineering(values.wload, "m") + ", tail current set by " +
      formatEngineering(values.ibias, "A") + " through a mirror, output at the " +
      "second drain, loaded by " + formatEngineering(values.cl, "F") +
      ". Measured open loop; the DC servo that sets the operating point is not drawn" +
      (pm === null ? "." : ", measured phase margin " + pm.toFixed(1) + " degrees."));

    // Rails span only the columns they feed.
    wire(svg, 110, yVdd, 350, yVdd);
    wire(svg, 110, yGnd, 440, yGnd);
    ground(svg, { x: 280, y: yGnd });
    label(svg, { x: 358, y: yVdd + 4, text: "VDD 1.8 V", size: 11, node: true });

    // Bias branch: Ib into diode-connected M8, tied left of the source.
    wire(svg, 122, yVdd, 122, 158);
    isource(svg, { cx: 122, cy: 180 });
    wire(svg, 122, 202, 122, 306);
    wire(svg, 122, 306, 122, 314);
    nmos(svg, { x: 96, y: bY, name: "M8" });
    wire(svg, 122, 306, 60, 306);
    wire(svg, 60, 306, 60, bY);
    nodeDot(svg, { x: 122, y: 306 });
    label(svg, { x: 130, y: 300, text: "nbias", size: 11, node: true });
    label(svg, { x: 92, y: 176, text: "Ib", anchor: "end", strong: true });
    label(svg, { value: true, x: 92, y: 192, anchor: "end",
                 text: formatEngineering(values.ibias, "A"), size: 11 });
    label(svg, { x: 134, y: 340, text: "M8", size: 11 });

    // The bias bus rides the diode-tie level, one clear channel above the
    // bottom row, then drops onto M5's gate from outside its symbol. Along
    // the gate row it would run straight through M8's body.
    wire(svg, 122, 306, 188, 306);
    wire(svg, 188, 306, 188, bY);
    label(svg, { x: 262, y: 340, text: "M5", size: 11 });

    // PMOS mirror.
    pmos(svg, { x: 200, y: pY, name: "M3" });
    pmos(svg, { x: 300, y: pY, name: "M4" });
    wire(svg, 226, yVdd, 226, 82);
    wire(svg, 326, yVdd, 326, 82);
    label(svg, { x: 158, y: 116, text: "M3", anchor: "end", size: 11 });
    label(svg, { x: 258, y: 116, text: "M4", anchor: "end", size: 11 });
    label(svg, { value: true, x: 340, y: 104, size: 11,
                 text: "W " + formatEngineering(values.wload, "m") });

    // Mirror gate tie, over the top; its M3-source crossing is the only one.
    wire(svg, 164, pY, 164, 66);
    wire(svg, 164, 66, 264, 66);
    wire(svg, 264, 66, 264, pY);

    // d1, tapping across to the tie.
    wire(svg, 226, 158, 226, 202);
    wire(svg, 226, 180, 164, 180);
    wire(svg, 164, 180, 164, pY);
    nodeDot(svg, { x: 226, y: 180 });

    // The pair. M2 is mirrored so its gate faces outward.
    nmos(svg, { x: 200, y: nY, name: "M1" });
    var flipped = add(svg, "g", { transform: "translate(600 0) scale(-1 1)" });
    nmos(flipped, { x: 300, y: nY, name: "M2" });
    label(svg, { x: 158, y: 262, text: "M1", anchor: "end", size: 11 });
    label(svg, { x: 266, y: 236, text: "M2", anchor: "end", size: 11 });

    // Tail into M5.
    wire(svg, 226, 278, 226, 292);
    wire(svg, 274, 278, 274, 292);
    wire(svg, 226, 292, 274, 292);
    wire(svg, 250, 292, 250, 314);
    nmos(svg, { x: 224, y: bY, name: "M5" });
    label(svg, { value: true, x: 258, y: 308, size: 11,
                 text: "W " + formatEngineering(values.wpair, "m") });

    // Inputs: the non-inverting side drives M1; M2's gate takes the feedback
    // in the servo, so it is drawn as the inverting input stub.
    wire(svg, 164, nY, 140, nY);
    nodeDot(svg, { x: 140, y: nY });
    label(svg, { x: 140, y: 228, text: "inp", anchor: "middle", node: true });
    wire(svg, 336, nY, 360, nY);
    nodeDot(svg, { x: 360, y: nY });
    label(svg, { x: 360, y: 258, text: "inn", anchor: "middle", node: true });

    // The output: M4's drain down to M2's drain, with the load hung right.
    wire(svg, 326, 158, 326, 196);
    wire(svg, 326, 196, 274, 196);
    wire(svg, 274, 196, 274, 202);
    nodeDot(svg, { x: 326, y: 180 });
    label(svg, { x: 334, y: 174, text: "out", node: true });
    wire(svg, 326, 180, 420, 180);
    wire(svg, 420, 180, 420, 240);
    capacitor(svg, { x: 420, y1: 240, y2: 284, centre: 262 });
    wire(svg, 420, 284, 420, yGnd);
    label(svg, { x: 444, y: 258, text: "CL", strong: true });
    label(svg, { value: true, x: 444, y: 274,
                 text: formatEngineering(values.cl, "F"), size: 11 });

    // Bottom-row sources to ground.
    wire(svg, 122, 390, 122, yGnd);
    wire(svg, 250, 390, 250, yGnd);

    if (pm !== null && isFinite(pm)) {
      wire(svg, 380, 180, 380, 202);
      valueTag(svg, {
        x: 380, y: 202, anchor: "middle", text: "PM " + pm.toFixed(1) + "\u00b0"
      });
    }
    return svg;
  }

  /* ---- SKY130 two-stage op-amp -------------------------------------- */

  /* The full eight-transistor amplifier, laid out in strict columns so that
   * nothing overlaps anything: bias | input pair under its mirror | second
   * stage. Every label sits in clear space, rails span only the columns they
   * feed, and the one deliberate wire crossing in the figure is the mirror
   * gate tie passing the M3 source. The DC servo that biases the open-loop
   * measurement is instrumentation, not the design, and is not drawn. */
  function drawOpampTwoStage(svg, values) {
    var yVdd = 40, yGnd = 420;
    var pY = 120;      // PMOS channel row
    var nY = 240;      // input pair channel row
    var bY = 352;      // bottom NMOS row; the bias bus runs along its gates
    var pm = values.phase_margin === undefined ? null : values.phase_margin;

    begin(svg, 760, 448,
      "SKY130 two-stage op-amp: NMOS input pair of width " +
      formatEngineering(values.wpair, "m") + " under a PMOS mirror load of width " +
      formatEngineering(values.wload, "m") + ", tail and output currents set by " +
      formatEngineering(values.ibias, "A") + " through a mirror, then a PMOS " +
      "common-source second stage of width " + formatEngineering(values.w6, "m") +
      " with an NMOS sink of width " + formatEngineering(values.w7, "m") +
      ", compensated by " + formatEngineering(values.cc, "F") + " in series with " +
      formatEngineering(values.rz, "\u03a9") + ", loaded by " +
      formatEngineering(values.cl, "F") +
      ". Measured open loop; the DC servo that sets the operating point is not drawn" +
      (pm === null ? "." : ", measured phase margin " + pm.toFixed(1) + " degrees."));

    // Rails span only the columns they feed.
    wire(svg, 110, yVdd, 640, yVdd);
    wire(svg, 110, yGnd, 680, yGnd);
    ground(svg, { x: 375, y: yGnd });
    label(svg, { x: 648, y: yVdd + 4, text: "VDD 1.8 V", size: 11, node: true });

    // ---- bias branch: Ib into diode-connected M8 ----
    wire(svg, 122, yVdd, 122, 158);
    isource(svg, { cx: 122, cy: 180 });
    wire(svg, 122, 202, 122, 306);
    wire(svg, 122, 306, 122, 314);
    nmos(svg, { x: 96, y: bY, name: "M8" });
    // Diode tie, routed left so it clears the source circle entirely.
    wire(svg, 122, 306, 60, 306);
    wire(svg, 60, 306, 60, bY);
    nodeDot(svg, { x: 122, y: 306 });
    label(svg, { x: 130, y: 300, text: "nbias", size: 11, node: true });
    label(svg, { x: 92, y: 176, text: "Ib", anchor: "end", strong: true });
    label(svg, { value: true, x: 92, y: 192, anchor: "end",
                 text: formatEngineering(values.ibias, "A"), size: 11 });
    label(svg, { x: 134, y: 340, text: "M8", size: 11 });

    // The bias bus rides the diode-tie level, one clear channel above the
    // bottom row, and drops onto each gate from outside its symbol. Along
    // the gate row it would run straight through M8's and M5's bodies. Its
    // one crossing is the tail wire on the way past.
    wire(svg, 122, 306, 524, 306);
    wire(svg, 188, 306, 188, bY);
    nodeDot(svg, { x: 188, y: 306 });
    wire(svg, 524, 306, 524, bY);
    label(svg, { x: 262, y: 340, text: "M5", size: 11 });
    label(svg, { x: 518, y: 344, text: "M7", anchor: "end", size: 11 });

    // ---- first stage ----
    // PMOS mirror above the pair: M3 diode on the left, M4 on the right.
    pmos(svg, { x: 200, y: pY, name: "M3" });
    pmos(svg, { x: 300, y: pY, name: "M4" });
    wire(svg, 226, yVdd, 226, 82);
    wire(svg, 326, yVdd, 326, 82);
    label(svg, { x: 158, y: 116, text: "M3", anchor: "end", size: 11 });
    label(svg, { x: 258, y: 116, text: "M4", anchor: "end", size: 11 });
    label(svg, { value: true, x: 340, y: 104, size: 11,
                 text: "W " + formatEngineering(values.wload, "m") });

    // The gate tie: down from both gate leads, joined over the top. Its one
    // crossing with the M3 source column is the figure's only crossing.
    wire(svg, 164, pY, 164, 66);
    wire(svg, 164, 66, 264, 66);
    wire(svg, 264, 66, 264, pY);

    // d1: mirror diode down to M1, tapping across to the gate tie.
    wire(svg, 226, 158, 226, 202);
    wire(svg, 226, 180, 164, 180);
    wire(svg, 164, 180, 164, pY);
    nodeDot(svg, { x: 226, y: 180 });

    // The pair. M2 is mirrored so its gate faces the second stage.
    nmos(svg, { x: 200, y: nY, name: "M1" });
    var flipped = add(svg, "g", { transform: "translate(600 0) scale(-1 1)" });
    nmos(flipped, { x: 300, y: nY, name: "M2" });
    label(svg, { x: 158, y: 262, text: "M1", anchor: "end", size: 11 });
    label(svg, { x: 266, y: 236, text: "M2", anchor: "end", size: 11 });

    // Tail into M5.
    wire(svg, 226, 278, 226, 292);
    wire(svg, 274, 278, 274, 292);
    wire(svg, 226, 292, 274, 292);
    wire(svg, 250, 292, 250, 314);
    nmos(svg, { x: 224, y: bY, name: "M5" });
    label(svg, { value: true, x: 258, y: 320, size: 11,
                 text: "W " + formatEngineering(values.wpair, "m") });

    // Inputs, pulled clear of the devices.
    wire(svg, 164, nY, 140, nY);
    nodeDot(svg, { x: 140, y: nY });
    label(svg, { x: 140, y: 228, text: "inn", anchor: "middle", node: true });
    wire(svg, 336, nY, 360, nY);
    nodeDot(svg, { x: 360, y: nY });
    label(svg, { x: 360, y: 258, text: "inp", anchor: "middle", node: true });

    // d2: down from M4, forking right twice: the upper branch drives the
    // second-stage gate, the lower branch is the compensation path.
    wire(svg, 326, 158, 326, 196);
    wire(svg, 326, 164, 492, 164);
    wire(svg, 492, 164, 492, pY);
    wire(svg, 492, pY, 524, pY);
    wire(svg, 326, 196, 274, 196);
    wire(svg, 274, 196, 274, 202);
    nodeDot(svg, { x: 326, y: 164 });
    nodeDot(svg, { x: 326, y: 196 });
    label(svg, { x: 334, y: 158, text: "d2", size: 11, node: true });

    // ---- second stage ----
    pmos(svg, { x: 560, y: pY, name: "M6" });
    wire(svg, 586, yVdd, 586, 82);
    nmos(svg, { x: 560, y: bY, name: "M7" });
    wire(svg, 586, 158, 586, 314);
    label(svg, { x: 518, y: 112, text: "M6", anchor: "end", size: 11 });
    label(svg, { value: true, x: 600, y: 104, size: 11,
                 text: "W " + formatEngineering(values.w6, "m") });
    label(svg, { value: true, x: 600, y: 344, size: 11,
                 text: "W " + formatEngineering(values.w7, "m") });

    // Compensation: Rz then Cc, from d2 across to the output.
    wire(svg, 326, 196, 344, 196);
    resistor(svg, { orientation: "horizontal", y: 196, x1: 344, x2: 400,
                    peaks: 4, amplitude: 8 });
    wire(svg, 400, 196, 416, 196);
    capacitor(svg, { orientation: "horizontal", y: 196, x1: 416, x2: 472 });
    wire(svg, 472, 196, 586, 196);
    nodeDot(svg, { x: 586, y: 196 });
    label(svg, { x: 372, y: 177, text: "Rz", anchor: "middle", strong: true });
    label(svg, { value: true, x: 372, y: 226, anchor: "middle",
                 text: formatEngineering(values.rz, "\u03a9"), size: 11 });
    label(svg, { x: 444, y: 177, text: "Cc", anchor: "middle", strong: true });
    label(svg, { value: true, x: 444, y: 226, anchor: "middle",
                 text: formatEngineering(values.cc, "F"), size: 11 });

    // Output node and load.
    nodeDot(svg, { x: 586, y: 246 });
    label(svg, { x: 596, y: 238, text: "out", node: true });
    wire(svg, 586, 246, 668, 246);
    wire(svg, 668, 246, 668, 300);
    capacitor(svg, { x: 668, y1: 300, y2: 344, centre: 322 });
    wire(svg, 668, 344, 668, yGnd);
    label(svg, { x: 692, y: 316, text: "CL", strong: true });
    label(svg, { value: true, x: 692, y: 332,
                 text: formatEngineering(values.cl, "F"), size: 11 });

    // Bottom-row sources to ground.
    wire(svg, 122, 390, 122, yGnd);
    wire(svg, 250, 390, 250, yGnd);
    wire(svg, 586, 390, 586, yGnd);

    // The measured phase margin, hung in the clear space under the load wire.
    if (pm !== null && isFinite(pm)) {
      wire(svg, 632, 246, 632, 268);
      valueTag(svg, {
        x: 632, y: 268, anchor: "middle", text: "PM " + pm.toFixed(1) + "\u00b0"
      });
    }
    return svg;
  }

  window.drawDivider = drawDivider;
  window.drawRCLowpass = drawRCLowpass;
  window.drawRCHighpass = drawRCHighpass;
  window.drawRLCBandpass = drawRLCBandpass;
  window.drawInvertingAmp = drawInvertingAmp;
  window.drawTwopoleAmp = drawTwopoleAmp;
  window.drawNfetCsAmp = drawNfetCsAmp;

  /* ---- SKY130 folded cascode ---------------------------------------- */

  /* Redrawn on a planned grid after the first version failed a mechanical
   * audit: gate buses ran through transistor bodies, and the bottom row's
   * sources stopped short of the ground rail. Discipline now: every wire
   * ends exactly on a symbol terminal or another wire, gate buses ride
   * clear channels and drop onto gates from outside the symbols, and each
   * unavoidable crossing sits well away from every junction dot. */
  function drawFoldedCascode(svg, values) {
    var yVdd = 40, yGnd = 540;
    var pm = values.phase_margin === undefined ? null : values.phase_margin;

    begin(svg, 740, 580,
      "SKY130 folded cascode: an NMOS input pair of width " +
      formatEngineering(values.wpair, "m") + " folding into PMOS sources of width " +
      formatEngineering(values.wfold, "m") + ", cascodes of width " +
      formatEngineering(values.wcasc, "m") + " building the output resistance, " +
      "bias " + formatEngineering(values.ibias, "A") + ", loaded by " +
      formatEngineering(values.cl, "F") +
      ". The cascode gate references are external; the DC servo is not drawn" +
      (pm === null ? "." : ", measured phase margin " + pm.toFixed(1) + " degrees."));

    // Rails.
    wire(svg, 60, yVdd, 600, yVdd);
    wire(svg, 60, yGnd, 660, yGnd);
    ground(svg, { x: 230, y: yGnd });
    label(svg, { x: 608, y: yVdd + 4, text: "VDD 1.8 V", size: 11, node: true });

    // Bias column: Ib into diode M8. The gate bus runs in the clear channel
    // above the bottom row and ties into the drain feed at the nbias dot.
    wire(svg, 74, yVdd, 74, 96);
    isource(svg, { cx: 74, cy: 118 });
    label(svg, { x: 66, y: 60, text: "Ib", anchor: "end", strong: true });
    label(svg, { value: true, x: 66, y: 76, anchor: "end",
                 text: formatEngineering(values.ibias, "A"), size: 11 });
    wire(svg, 74, 140, 74, 458);
    nmos(svg, { x: 48, y: 496, name: "M8" });
    label(svg, { x: 40, y: 460, text: "M8", anchor: "end", size: 11 });

    // nbias: one bus above the row, dropping onto each gate from outside
    // its symbol. Its one crossing is the pbias feed on the way past.
    wire(svg, 12, 446, 250, 446);
    wire(svg, 12, 446, 12, 496);
    wire(svg, 104, 446, 104, 496);
    wire(svg, 250, 446, 250, 496);
    nodeDot(svg, { x: 74, y: 446 });
    nodeDot(svg, { x: 104, y: 446 });
    label(svg, { x: 66, y: 440, text: "nbias", anchor: "end",
                 size: 11, node: true });

    // The second bias branch: M14 mirrors nbias, M13 turns it into pbias.
    pmos(svg, { x: 140, y: 96, name: "M13" });
    wire(svg, 166, yVdd, 166, 58);
    label(svg, { x: 176, y: 92, text: "M13", size: 11 });
    // Diode tie, routed left of the body; the riser clears the Ib circle.
    wire(svg, 104, 96, 104, 152);
    wire(svg, 104, 152, 166, 152);
    wire(svg, 166, 134, 166, 152);
    nodeDot(svg, { x: 166, y: 152 });
    label(svg, { x: 174, y: 146, text: "pbias", size: 11, node: true });
    wire(svg, 166, 152, 166, 458);
    nmos(svg, { x: 140, y: 496, name: "M14" });
    label(svg, { x: 174, y: 484, text: "M14", size: 11 });

    // The input pair, folding left and right.
    nmos(svg, { x: 236, y: 310, name: "M1" });
    nmos(svg, { x: 336, y: 310, name: "M2" });
    label(svg, { x: 254, y: 258, text: "M1", anchor: "end", size: 11 });
    label(svg, { value: true, x: 254, y: 272, anchor: "end", size: 11,
                 text: "W " + formatEngineering(values.wpair, "m") });
    label(svg, { x: 354, y: 268, text: "M2", anchor: "end", size: 11 });
    label(svg, { x: 192, y: 314, text: "inp", anchor: "end", size: 11, node: true });
    label(svg, { x: 296, y: 304, text: "inn", anchor: "end", size: 11, node: true });

    // Tail: both sources into M5, whose gate drops off the nbias bus.
    wire(svg, 262, 348, 262, 412);
    wire(svg, 362, 348, 362, 412);
    wire(svg, 262, 412, 362, 412);
    nodeDot(svg, { x: 312, y: 412 });
    label(svg, { x: 304, y: 406, text: "tail", anchor: "end",
                 size: 11, node: true });
    wire(svg, 312, 412, 312, 458);
    nmos(svg, { x: 286, y: 496, name: "M5" });
    label(svg, { x: 320, y: 484, text: "M5", size: 11 });

    // Top of the branches: the folding sources M3, M4 off pbias. The gate
    // tie hangs below the row and crosses the left drain column once.
    pmos(svg, { x: 444, y: 96, name: "M3" });
    pmos(svg, { x: 564, y: 96, name: "M4" });
    wire(svg, 470, yVdd, 470, 58);
    wire(svg, 590, yVdd, 590, 58);
    label(svg, { x: 400, y: 84, text: "M3", anchor: "end", size: 11 });
    label(svg, { x: 602, y: 84, text: "M4", size: 11 });
    label(svg, { value: true, x: 602, y: 104, size: 11,
                 text: "W " + formatEngineering(values.wfold, "m") });
    wire(svg, 166, 152, 390, 152);
    nodeDot(svg, { x: 390, y: 152 });
    wire(svg, 390, 152, 390, 96);
    wire(svg, 390, 96, 408, 96);
    wire(svg, 390, 152, 510, 152);
    wire(svg, 510, 152, 510, 96);
    wire(svg, 510, 96, 528, 96);

    // The fold nodes, where the pair's drains meet the cascode sources.
    wire(svg, 470, 134, 470, 214);
    wire(svg, 590, 134, 590, 214);
    nodeDot(svg, { x: 470, y: 172 });
    nodeDot(svg, { x: 590, y: 196 });
    label(svg, { x: 478, y: 168, text: "fold1", size: 11, node: true });
    label(svg, { x: 598, y: 200, text: "fold2", size: 11, node: true });
    wire(svg, 262, 172, 262, 272);
    wire(svg, 262, 172, 470, 172);
    wire(svg, 362, 196, 362, 272);
    wire(svg, 362, 196, 590, 196);

    // The PMOS cascodes M6, M7, gates tied below the row on the external
    // pcasc reference.
    pmos(svg, { x: 444, y: 252, name: "M6" });
    pmos(svg, { x: 564, y: 252, name: "M7" });
    label(svg, { x: 400, y: 240, text: "M6", anchor: "end", size: 11 });
    label(svg, { x: 602, y: 240, text: "M7", size: 11 });
    label(svg, { value: true, x: 602, y: 260, size: 11,
                 text: "W " + formatEngineering(values.wcasc, "m") });
    wire(svg, 408, 252, 390, 252);
    wire(svg, 390, 252, 390, 306);
    wire(svg, 390, 306, 510, 306);
    wire(svg, 510, 306, 510, 252);
    wire(svg, 510, 252, 528, 252);
    label(svg, { x: 532, y: 270, text: "pcasc", anchor: "middle",
                 size: 11, node: true });
    label(svg, { value: true, x: 532, y: 284, anchor: "middle",
                 text: "Vpc ext", size: 11 });

    // casc1 programs the mirror; out carries the load.
    wire(svg, 470, 290, 470, 344);
    nodeDot(svg, { x: 470, y: 322 });
    label(svg, { x: 478, y: 318, text: "casc1", size: 11, node: true });
    wire(svg, 590, 290, 590, 344);
    nodeDot(svg, { x: 590, y: 322 });
    label(svg, { x: 598, y: 316, text: "out", size: 11, node: true });
    wire(svg, 590, 322, 660, 322);
    capacitor(svg, { x: 660, y1: 322, y2: yGnd, plate: 26 });
    label(svg, { x: 678, y: 420, text: "CL", strong: true });
    label(svg, { value: true, x: 678, y: 436,
                 text: formatEngineering(values.cl, "F"), size: 11 });

    // The NMOS cascodes M9, M10, gates tied below the row on the external
    // ncasc reference.
    nmos(svg, { x: 444, y: 382, name: "M9" });
    nmos(svg, { x: 564, y: 382, name: "M10" });
    label(svg, { x: 400, y: 366, text: "M9", anchor: "end", size: 11 });
    label(svg, { x: 602, y: 370, text: "M10", size: 11 });
    wire(svg, 408, 382, 390, 382);
    wire(svg, 390, 382, 390, 432);
    wire(svg, 390, 432, 510, 432);
    wire(svg, 510, 432, 510, 382);
    wire(svg, 510, 382, 528, 382);
    label(svg, { x: 532, y: 400, text: "ncasc", anchor: "middle",
                 size: 11, node: true });
    label(svg, { value: true, x: 532, y: 414, anchor: "middle",
                 text: "Vnc ext", size: 11 });
    wire(svg, 470, 420, 470, 458);
    wire(svg, 590, 420, 590, 458);

    // The mirror M11, M12 under them, programmed from casc1. The gate net
    // comes down between the pair and the branch, feeds M11 at gate level,
    // and tees across to M12 through the channel above the row.
    nmos(svg, { x: 444, y: 496, name: "M11" });
    nmos(svg, { x: 564, y: 496, name: "M12" });
    label(svg, { x: 400, y: 484, text: "M11", anchor: "end", size: 11 });
    label(svg, { x: 598, y: 484, text: "M12", size: 11 });
    wire(svg, 372, 322, 470, 322);
    wire(svg, 372, 322, 372, 496);
    wire(svg, 372, 496, 408, 496);
    nodeDot(svg, { x: 372, y: 446 });
    wire(svg, 372, 446, 528, 446);
    wire(svg, 528, 446, 528, 496);

    // Every bottom-row source lands on the ground rail.
    wire(svg, 74, 534, 74, yGnd);
    wire(svg, 166, 534, 166, yGnd);
    wire(svg, 312, 534, 312, yGnd);
    wire(svg, 470, 534, 470, yGnd);
    wire(svg, 590, 534, 590, yGnd);

    if (pm !== null && isFinite(pm)) {
      // The branch continues above the out node, so the tag hangs on a
      // stub below the out run, the way the OTA and the op-amp do it.
      wire(svg, 626, 322, 626, 336);
      valueTag(svg, {
        x: 626, y: 336, anchor: "middle",
        text: "PM " + pm.toFixed(1) + "\u00b0"
      });
    }
  }

  window.drawOpampTwoStage = drawOpampTwoStage;
  window.drawOta5t = drawOta5t;
  window.drawFoldedCascode = drawFoldedCascode;
  /* The inverse direction: read a number the way an engineer writes one.
   * "10k", "2.2u", "4.7 pF", "1meg", "30 MHz", "1.8V" all parse; the
   * engineering prefix is case-sensitive where it must be (M is mega, m is
   * milli) and forgiving where it can be (K and k are both kilo, u and the
   * Greek mu are both micro, and SPICE's "meg" is honoured). A trailing
   * unit is accepted and ignored, because the field already says its unit.
   * Returns NaN for anything it cannot read, never a guess. */
  var PARSE_SCALES = {
    T: 1e12, G: 1e9, M: 1e6, k: 1e3, K: 1e3,
    m: 1e-3, u: 1e-6, "µ": 1e-6, n: 1e-9, p: 1e-12, f: 1e-15
  };

  function parseEngineering(text) {
    if (typeof text === "number") {
      return text;
    }
    var raw = String(text == null ? "" : text).trim();
    if (raw === "") {
      return NaN;
    }
    var match = raw.match(
      /^([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(meg|MEG|Meg|[TGMkKmunµpf])?\s*([a-zA-ZΩ°/%]{0,4})$/
    );
    if (!match) {
      return NaN;
    }
    var value = Number(match[1]);
    var prefix = match[2];
    if (prefix === "meg" || prefix === "Meg" || prefix === "MEG") {
      value *= 1e6;
    } else if (prefix) {
      value *= PARSE_SCALES[prefix];
    }
    return value;
  }

  window.formatEngineering = formatEngineering;
  window.parseEngineering = parseEngineering;
  window.FaradaemSymbols = symbols;
})(window, document);
