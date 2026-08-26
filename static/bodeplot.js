/* Faradaem Bode plot -- pure SVG, no libraries.
 *
 * Two stacked axes sharing one log-frequency x: magnitude in dB on top,
 * phase in degrees below. Drawn on the paper panel in the same datasheet
 * idiom as the schematic, so the figure and the plot read as one document.
 *
 * Colours come from the stylesheet via class names; geometry lives here.
 * Attached to window: drawBode.
 */

(function (window, document) {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  //: Fallback size. The plot is really drawn at the width it will occupy,
  //: measured at draw time, so nothing is scaled up and no text stretches.
  var VIEW = { width: 520, height: 360 };
  var MIN_WIDTH = 400;
  //: The height of the box before there is anything to draw in it.
  var EMPTY_HEIGHT = 96;
  var MAX_WIDTH = 1000;
  //: Top margin holds three staggered rows of marker labels. The left margin
  //: holds the tick values and, outside them, the rotated axis title.
  var MARGIN = { left: 58, right: 30, top: 56, bottom: 38 };
  //: Marker labels closer than this on screen get bumped to the second row.
  var LABEL_CLEARANCE = 78;
  var GAP = 18;
  var MAGNITUDE_SHARE = 0.6;

  var DB_TICK_STEP = 20;
  var PHASE_TICKS = [0, -45, -90];

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

  function line(parent, x1, y1, x2, y2, className) {
    return add(parent, "line", {
      "class": className,
      x1: x1, y1: y1, x2: x2, y2: y2
    });
  }

  function text(parent, options) {
    var node = add(parent, "text", {
      "class": options.className || "bode-text",
      x: options.x,
      y: options.y,
      "text-anchor": options.anchor || "start",
      "font-size": options.size || 10
    });
    if (options.transform) {
      node.setAttribute("transform", options.transform);
    }
    node.textContent = options.text;
    return node;
  }

  function trace(parent, points, className) {
    return add(parent, "polyline", {
      "class": className,
      points: points.map(function (point) {
        return point[0].toFixed(2) + "," + point[1].toFixed(2);
      }).join(" "),
      "stroke-linecap": "round",
      "stroke-linejoin": "round"
    });
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  /* Length of a polyline, so it can be dashed to exactly its own length. */
  function polylineLength(points) {
    var total = 0;
    for (var i = 1; i < points.length; i++) {
      total += Math.hypot(points[i][0] - points[i - 1][0],
                          points[i][1] - points[i - 1][1]);
    }
    return total;
  }

  /* Draw the trace on by animating its dash offset to zero. Reduced motion is
   * handled by the stylesheet, which collapses the duration; the resting state
   * is a fully drawn line either way. */
  function drawIn(node, points) {
    var length = Math.ceil(polylineLength(points)) + 1;
    node.setAttribute("style",
      "--trace-length:" + length + ";stroke-dasharray:" + length +
      ";stroke-dashoffset:0");
    node.setAttribute("class", node.getAttribute("class") + " is-drawing");
  }

  /* Index of the swept sample nearest a position on the log-frequency axis. */
  function nearestIndex(logFreqs, target) {
    var best = 0;
    var bestGap = Infinity;
    for (var i = 0; i < logFreqs.length; i++) {
      var gap = Math.abs(logFreqs[i] - target);
      if (gap < bestGap) {
        bestGap = gap;
        best = i;
      }
    }
    return best;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  /* A linear map from a data range onto a pixel range. */
  function scale(dataLow, dataHigh, pixelLow, pixelHigh) {
    var span = dataHigh - dataLow;
    return function (value) {
      if (span === 0) {
        return (pixelLow + pixelHigh) / 2;
      }
      return pixelLow + ((value - dataLow) / span) * (pixelHigh - pixelLow);
    };
  }

  //: Axis ticks say 10 kHz, not 10.00 kHz: four significant digits on a
  //: decade line is noise, and the long form crowds its neighbours.
  var DECADE_UNITS = ["Hz", "kHz", "MHz", "GHz", "THz"];

  function decadeLabel(decade) {
    if (decade < 0) {
      return window.formatEngineering(Math.pow(10, decade), "Hz");
    }
    var step = Math.floor(decade / 3);
    if (step >= DECADE_UNITS.length) {
      return window.formatEngineering(Math.pow(10, decade), "Hz");
    }
    return Math.pow(10, decade - step * 3) + " " + DECADE_UNITS[step];
  }

  /* A panel title, rotated up the left margin beside its frame. */
  function axisTitle(svg, label, top, bottom) {
    var middle = (top + bottom) / 2;
    text(svg, {
      x: 13,
      y: middle,
      text: label,
      anchor: "middle",
      className: "bode-title",
      transform: "rotate(-90 13 " + middle + ")"
    });
  }

  function drawBode(svg, data) {
    var freq = (data && data.freq) || [];
    var magDb = (data && data.mag_db) || [];
    var phaseDeg = (data && data.phase_deg) || [];
    var f3db = data && data.f3db !== undefined ? data.f3db : null;

    clear(svg);

    // Draw at the size the element actually has, so one drawing unit is one
    // CSS pixel and the type renders at the size it was designed at.
    var measured = svg.getBoundingClientRect();
    var view = {
      width: Math.round(clamp(
        measured && measured.width ? measured.width : VIEW.width,
        MIN_WIDTH, MAX_WIDTH
      )),
      height: VIEW.height
    };
    svg.setAttribute("viewBox", "0 0 " + view.width + " " + view.height);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("role", "img");

    if (freq.length < 2 || magDb.length !== freq.length) {
      // Nothing to plot yet, so do not reserve a full plot's worth of empty
      // ground for it: a strip with the invitation in it is enough.
      view.height = EMPTY_HEIGHT;
      svg.setAttribute("viewBox", "0 0 " + view.width + " " + view.height);
      svg.setAttribute("aria-label", "Frequency response plot, no data yet.");
      text(svg, {
        x: view.width / 2,
        y: view.height / 2,
        text: "run a sweep to plot the frequency response",
        anchor: "middle",
        size: 12,
        className: "bode-placeholder"
      });
      return svg;
    }

    var left = MARGIN.left;
    var right = view.width - MARGIN.right;
    var magTop = MARGIN.top;
    var plotHeight = view.height - MARGIN.top - MARGIN.bottom;
    var magHeight = Math.round((plotHeight - GAP) * MAGNITUDE_SHARE);
    var magBottom = magTop + magHeight;
    var phaseTop = magBottom + GAP;
    var phaseBottom = view.height - MARGIN.bottom;

    svg.setAttribute(
      "aria-label",
      "Bode plot from " + window.formatEngineering(freq[0], "Hz") + " to " +
      window.formatEngineering(freq[freq.length - 1], "Hz") +
      ", magnitude falling from " + magDb[0].toFixed(1) + " to " +
      magDb[magDb.length - 1].toFixed(1) + " dB" +
      (f3db !== null && isFinite(f3db)
        ? ", 3 dB corner at " + window.formatEngineering(f3db, "Hz") + "."
        : (Array.isArray(data.markers) && data.markers.length
          ? ", marked at " + data.markers.map(function (marker) {
            return marker.label + " " + window.formatEngineering(marker.freq, "Hz");
          }).join(", ") + "."
          : "."))
    );

    // ---- scales ----
    var logMin = Math.log10(freq[0]);
    var logMax = Math.log10(freq[freq.length - 1]);
    var xOf = scale(logMin, logMax, left, right);
    function xFreq(value) {
      return xOf(Math.log10(value));
    }

    var magMax = Math.max.apply(null, magDb);
    var magMin = Math.min.apply(null, magDb);
    var dbTop = Math.ceil((magMax + 1) / 10) * 10;
    var dbBottom = Math.floor((magMin - 1) / 10) * 10;
    var yMag = scale(dbTop, dbBottom, magTop, magBottom);

    var phaseMax = Math.max.apply(null, phaseDeg);
    var phaseMin = Math.min.apply(null, phaseDeg);
    var phaseHigh = Math.max(5, phaseMax + 5);
    var phaseLow = Math.min(-95, phaseMin - 5);
    var yPhase = scale(phaseHigh, phaseLow, phaseTop, phaseBottom);

    // ---- decade gridlines and frequency ticks ----
    for (var decade = Math.floor(logMin); decade <= Math.ceil(logMax); decade++) {
      var x = xOf(decade);
      if (x < left - 1 || x > right + 1) {
        continue;
      }
      x = clamp(x, left, right);
      line(svg, x, magTop, x, magBottom, "bode-grid");
      line(svg, x, phaseTop, x, phaseBottom, "bode-grid");
      text(svg, {
        x: x,
        y: phaseBottom + 15,
        text: decadeLabel(decade),
        anchor: "middle"
      });
    }

    // ---- magnitude gridlines ----
    var firstDbTick = Math.ceil(dbBottom / DB_TICK_STEP) * DB_TICK_STEP;
    for (var db = firstDbTick; db <= dbTop; db += DB_TICK_STEP) {
      var yLevel = yMag(db);
      line(svg, left, yLevel, right, yLevel, "bode-grid");
      text(svg, { x: left - 8, y: yLevel + 3.5, text: String(db), anchor: "end" });
    }

    // ---- phase gridlines ----
    PHASE_TICKS.forEach(function (degrees) {
      if (degrees > phaseHigh || degrees < phaseLow) {
        return;
      }
      var yLevel = yPhase(degrees);
      line(svg, left, yLevel, right, yLevel, "bode-grid");
      text(svg, { x: left - 8, y: yLevel + 3.5, text: String(degrees), anchor: "end" });
    });

    // ---- axis frames ----
    add(svg, "rect", {
      "class": "bode-axis",
      x: left, y: magTop, width: right - left, height: magBottom - magTop
    });
    add(svg, "rect", {
      "class": "bode-axis",
      x: left, y: phaseTop, width: right - left, height: phaseBottom - phaseTop
    });

    // ---- traces ----
    var magPoints = [];
    var phasePoints = [];
    for (var i = 0; i < freq.length; i++) {
      var px = xFreq(freq[i]);
      magPoints.push([px, clamp(yMag(magDb[i]), magTop, magBottom)]);
      if (i < phaseDeg.length) {
        phasePoints.push([px, clamp(yPhase(phaseDeg[i]), phaseTop, phaseBottom)]);
      }
    }
    var magTrace = trace(svg, magPoints, "bode-mag");
    var phaseTrace = phasePoints.length > 1
      ? trace(svg, phasePoints, "bode-phase")
      : null;

    // On arrival, a fresh result draws itself in once. Redraws while typing
    // pass animate:false so the figure does not twitch on every keystroke.
    if (data.animate) {
      drawIn(magTrace, magPoints);
    }

    // ---- markers, each spanning both plots ----
    //
    // Callers may pass a markers list ({freq, label}); the older single f3db
    // field still works and renders exactly as it always did.
    var markers = [];
    if (Array.isArray(data.markers)) {
      markers = data.markers.filter(function (marker) {
        return marker && isFinite(marker.freq) && marker.freq > 0;
      });
    } else if (f3db !== null && isFinite(f3db) && f3db > 0) {
      markers = [{ freq: f3db, label: "f-3dB" }];
    }

    markers = markers.slice().sort(function (a, b) {
      return a.freq - b.freq;
    });

    // Each label takes the lowest row whose previous label is far enough
    // left. Alternating two rows was not enough: three markers inside one
    // clearance put the first and third on the same row, printed on top
    // of each other, which is exactly what happened on the RLC band-pass.
    var rowLastX = [-Infinity, -Infinity, -Infinity];
    markers.forEach(function (marker) {
      var markerX = xFreq(marker.freq);
      if (markerX < left || markerX > right) {
        return;
      }

      line(svg, markerX, magTop, markerX, magBottom, "bode-marker");
      line(svg, markerX, phaseTop, markerX, phaseBottom, "bode-marker");

      var labelX = clamp(markerX, left + 34, right - 34);
      var row = 0;
      while (row < rowLastX.length - 1 &&
             labelX - rowLastX[row] < LABEL_CLEARANCE) {
        row += 1;
      }
      rowLastX[row] = labelX;

      text(svg, {
        x: labelX,
        y: magTop - (10 + row * 14),
        text: marker.label + " " + window.formatEngineering(marker.freq, "Hz"),
        anchor: "middle",
        className: "bode-marker-text"
      });
    });

    // ---- axis titles, rotated in the margin ----
    // Inside the frame they sat where the trace runs. Out here they cannot
    // collide with anything, and each panel still says what it plots.
    axisTitle(svg, "MAGNITUDE (dB)", magTop, magBottom);
    axisTitle(svg, "PHASE (deg)", phaseTop, phaseBottom);
    text(svg, {
      x: (left + right) / 2,
      y: view.height - 7,
      text: "FREQUENCY",
      anchor: "middle",
      className: "bode-title"
    });

    attachReadout(svg, {
      freq: freq,
      magDb: magDb,
      phaseDeg: phaseDeg,
      magPoints: magPoints,
      phasePoints: phasePoints,
      left: left,
      right: right,
      magTop: magTop,
      phaseBottom: phaseBottom,
      viewWidth: view.width
    });

    return svg;
  }

  /* ---- hover readout ------------------------------------------------
   *
   * A transparent hit area over both plots. Moving the pointer snaps a
   * crosshair to the nearest swept sample and reads its frequency, magnitude
   * and phase into a small paper tooltip. No click, no library, and the whole
   * thing lives in one group that is cleared on every move.
   */

  var TIP_LINE = 13;
  var TIP_PAD = 7;

  function attachReadout(svg, ctx) {
    // The fake DOM used by the render checks has no event plumbing.
    if (typeof svg.addEventListener !== "function") {
      return;
    }

    var layer = add(svg, "g", { "class": "bode-hover" });
    var hit = add(svg, "rect", {
      "class": "bode-hit",
      x: ctx.left,
      y: ctx.magTop,
      width: ctx.right - ctx.left,
      height: ctx.phaseBottom - ctx.magTop
    });

    var logFreqs = ctx.freq.map(function (value) {
      return Math.log10(value);
    });
    var logMin = logFreqs[0];
    var logMax = logFreqs[logFreqs.length - 1];

    function viewBoxX(event) {
      var box = svg.getBoundingClientRect();
      if (!box || !box.width) {
        return null;
      }
      return ((event.clientX - box.left) / box.width) * ctx.viewWidth;
    }

    function tooltip(index, x) {
      var lines = [
        window.formatEngineering(ctx.freq[index], "Hz"),
        ctx.magDb[index].toFixed(2) + " dB",
        (ctx.phaseDeg[index] === undefined ? "—" : ctx.phaseDeg[index].toFixed(1) + "°")
      ];
      var width = Math.max.apply(null, lines.map(function (line) {
        return line.length * 6.9;
      })) + TIP_PAD * 2;
      var height = lines.length * TIP_LINE + TIP_PAD * 2 - 3;

      // Flip to the left of the crosshair when it would overflow the frame.
      var boxX = x + 10 + width > ctx.right ? x - 10 - width : x + 10;
      var boxY = ctx.magTop + 6;

      add(layer, "rect", {
        "class": "bode-tip-box",
        x: boxX, y: boxY, width: width, height: height
      });
      lines.forEach(function (line, row) {
        text(layer, {
          x: boxX + TIP_PAD,
          y: boxY + TIP_PAD + TIP_LINE * row + 8,
          text: line,
          className: "bode-readout-text",
          size: 11
        });
      });
    }

    function move(event) {
      var x = viewBoxX(event);
      clear(layer);
      if (x === null || x < ctx.left || x > ctx.right) {
        return;
      }

      var fraction = (x - ctx.left) / (ctx.right - ctx.left);
      var index = nearestIndex(logFreqs, logMin + fraction * (logMax - logMin));
      var snapped = ctx.magPoints[index][0];

      line(layer, snapped, ctx.magTop, snapped, ctx.phaseBottom, "bode-crosshair");
      add(layer, "circle", {
        "class": "bode-dot-mag",
        cx: snapped, cy: ctx.magPoints[index][1], r: 3.5
      });
      if (ctx.phasePoints[index]) {
        add(layer, "circle", {
          "class": "bode-dot-phase",
          cx: snapped, cy: ctx.phasePoints[index][1], r: 3.5
        });
      }
      tooltip(index, snapped);
    }

    hit.addEventListener("pointermove", move);
    hit.addEventListener("pointerleave", function () {
      clear(layer);
    });
  }

  window.drawBode = drawBode;
  window.FaradaemBodeInternals = {
    nearestIndex: nearestIndex,
    polylineLength: polylineLength
  };
})(window, document);
