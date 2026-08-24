/* Faradaem step plot -- pure SVG, no libraries.
 *
 * One linear time axis and one voltage axis: the amplifier's output when it
 * is asked to move as fast as it can. The Bode plot next door shows the
 * amplifier being polite; this shows its limit.
 *
 * Drawn at the width it will occupy so nothing is scaled up, in the same
 * idiom as the schematic and the sweep: dark ground, hairline frame, one
 * cyan trace, axis titles rotated out into the margin where the trace
 * cannot reach them.
 *
 * Colours come from the stylesheet via class names; geometry lives here.
 * Attached to window: drawStep.
 */

(function (window, document) {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  //: Fallback size. The real width is measured at draw time.
  var VIEW = { width: 520, height: 260 };
  var MIN_WIDTH = 400;
  var MAX_WIDTH = 1000;
  var MARGIN = { left: 58, right: 30, top: 26, bottom: 38 };

  //: Roughly this many gridlines per axis, landed on 1, 2 or 5 times a
  //: power of ten so the labels read as round numbers.
  var TARGET_TICKS = 5;

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

  function clear(svg) {
    while (svg.firstChild) {
      svg.removeChild(svg.firstChild);
    }
  }

  function line(parent, x1, y1, x2, y2, className) {
    return add(parent, "line", {
      "class": className, x1: x1, y1: y1, x2: x2, y2: y2
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

  function scale(dataLow, dataHigh, pixelLow, pixelHigh) {
    var span = dataHigh - dataLow;
    return function (value) {
      if (span === 0) {
        return (pixelLow + pixelHigh) / 2;
      }
      return pixelLow + ((value - dataLow) / span) * (pixelHigh - pixelLow);
    };
  }

  /* A round step at or below the raw interval: 1, 2 or 5 times a power of
     ten, which is what makes an axis readable. */
  function niceStep(raw) {
    if (!(raw > 0)) {
      return 1;
    }
    var power = Math.pow(10, Math.floor(Math.log10(raw)));
    var scaled = raw / power;
    var step = scaled >= 5 ? 5 : (scaled >= 2 ? 2 : 1);
    return step * power;
  }

  function ticks(low, high) {
    var step = niceStep((high - low) / TARGET_TICKS);
    var first = Math.ceil(low / step) * step;
    var count = Math.floor((high - first) / step + 1e-6);
    var out = [];
    // Indexed, not accumulated: adding a step repeatedly leaves float dust
    // that turns a tick at one microsecond into 999.9999 nanoseconds.
    for (var i = 0; i <= count; i++) {
      out.push(first + i * step);
    }
    return out;
  }

  var TIME_UNITS = [
    [1e-12, "ps"], [1e-9, "ns"], [1e-6, "µs"], [1e-3, "ms"], [1, "s"]
  ];

  /* One unit for the whole axis, chosen from its full span and named in the
     axis title. Picking per tick is what puts "1000 ns" next to "1.2 us" on
     the same row and calls the origin "0 ps". */
  function timeUnit(span) {
    var pick = TIME_UNITS[0];
    for (var i = 0; i < TIME_UNITS.length; i++) {
      if (span >= TIME_UNITS[i][0] * 0.999) {
        pick = TIME_UNITS[i];
      }
    }
    return pick;
  }

  /* A tick in the axis unit, with only as many decimals as the spacing needs. */
  function tickLabel(value, step, unit) {
    var scaled = value / unit[0];
    var perStep = step / unit[0];
    var decimals = perStep < 0.1 ? 2 : (perStep < 1 ? 1 : 0);
    var shown = scaled.toFixed(decimals);
    if (shown.indexOf(".") !== -1) {
      shown = shown.replace(/0+$/, "").replace(/\.$/, "");
    }
    return shown === "-0" ? "0" : shown;
  }

  function drawStep(svg, data) {
    var points = (data && data.waveform) || [];

    clear(svg);

    var measured = svg.getBoundingClientRect();
    var view = {
      width: Math.round(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH,
        measured && measured.width ? measured.width : VIEW.width))),
      height: VIEW.height
    };
    svg.setAttribute("viewBox", "0 0 " + view.width + " " + view.height);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("role", "img");

    if (points.length < 2) {
      svg.setAttribute("aria-label", "Step response, no data yet.");
      text(svg, {
        x: view.width / 2,
        y: view.height / 2,
        text: "run the step response to plot it",
        anchor: "middle",
        size: 12,
        className: "bode-placeholder"
      });
      return svg;
    }

    var left = MARGIN.left;
    var right = view.width - MARGIN.right;
    var top = MARGIN.top;
    var bottom = view.height - MARGIN.bottom;

    var tMin = points[0][0];
    var tMax = points[points.length - 1][0];
    var vMin = points[0][1];
    var vMax = points[0][1];
    for (var i = 1; i < points.length; i++) {
      if (points[i][1] < vMin) { vMin = points[i][1]; }
      if (points[i][1] > vMax) { vMax = points[i][1]; }
    }
    // A tenth of the swing above and below, so the flats are not on the frame.
    var pad = Math.max((vMax - vMin) * 0.12, 0.01);
    var vLow = vMin - pad;
    var vHigh = vMax + pad;

    var xOf = scale(tMin, tMax, left, right);
    var yOf = scale(vHigh, vLow, top, bottom);

    svg.setAttribute(
      "aria-label",
      "Step response from " + vMin.toFixed(3) + " to " + vMax.toFixed(3)
      + " volts over " + ((tMax - tMin) / timeUnit(tMax - tMin)[0]).toFixed(1)
      + " " + timeUnit(tMax - tMin)[1] + "."
    );

    var timeTicks = ticks(tMin, tMax);
    var timeStep = timeTicks.length > 1 ? timeTicks[1] - timeTicks[0]
      : (tMax - tMin);
    var unit = timeUnit(tMax - tMin);
    timeTicks.forEach(function (value) {
      var x = xOf(value);
      line(svg, x, top, x, bottom, "bode-grid");
      text(svg, {
        x: x, y: bottom + 15, text: tickLabel(value, timeStep, unit),
        anchor: "middle"
      });
    });

    ticks(vLow, vHigh).forEach(function (value) {
      var y = yOf(value);
      line(svg, left, y, right, y, "bode-grid");
      text(svg, {
        x: left - 8, y: y + 3.5,
        text: value.toFixed(2),
        anchor: "end"
      });
    });

    add(svg, "rect", {
      "class": "bode-axis",
      x: left, y: top, width: right - left, height: bottom - top
    });

    var path = [];
    for (var p = 0; p < points.length; p++) {
      path.push(xOf(points[p][0]).toFixed(2) + "," + yOf(points[p][1]).toFixed(2));
    }
    add(svg, "polyline", { "class": "bode-mag", points: path.join(" ") });

    // Where the input stepped, so the eye can find the edge being measured.
    if (data.rise_at) {
      var edge = xOf(data.rise_at);
      if (edge >= left && edge <= right) {
        line(svg, edge, top, edge, bottom, "bode-marker");
      }
    }

    var middle = (top + bottom) / 2;
    text(svg, {
      x: 13, y: middle, text: "OUTPUT (V)", anchor: "middle",
      className: "bode-title", transform: "rotate(-90 13 " + middle + ")"
    });
    text(svg, {
      x: (left + right) / 2, y: view.height - 7,
      text: "TIME (" + unit[1] + ")",
      anchor: "middle", className: "bode-title"
    });

    return svg;
  }

  window.drawStep = drawStep;
})(window, document);
