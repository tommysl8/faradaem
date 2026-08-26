/* Faradaem floorplan view -- pure SVG, no libraries.
 *
 * The devices of a circuit drawn to scale on silicon, in microns. Every
 * rectangle here is the one whose area was reported: the picture and the
 * number come from the same placement, so the drawing cannot flatter it.
 *
 * This is a floorplan, not a layout. Nothing in it has been routed or
 * design rule checked, and the caption under it says so.
 *
 * Attached to window: drawFloorplan.
 */

(function (window, document) {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";

  var VIEW = { width: 520, height: 300 };
  var MIN_WIDTH = 400;
  var MAX_WIDTH = 1000;
  var MARGIN = { left: 62, right: 28, top: 24, bottom: 46 };

  //: A device narrower than this on screen gets no width label inside it.
  var LABEL_MIN_PX = 16;

  //: Every device carries a name, so consecutive rows have to stay at least
  //: this far apart or the names collide. Below that the drawing keeps its
  //: scale and grows past the frame instead, which the figure scrolls.
  var ROW_PITCH_PX = 15;

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

  /* A round number of microns for the scale bar: 1, 2 or 5 times a power
     of ten, at most a third of what is on screen. */
  function scaleStep(span) {
    var raw = span / 3;
    var power = Math.pow(10, Math.floor(Math.log10(raw)));
    var scaled = raw / power;
    return (scaled >= 5 ? 5 : (scaled >= 2 ? 2 : 1)) * power;
  }

  function drawFloorplan(svg, data) {
    var plan = data && data.floorplan;
    var devices = (plan && plan.devices) || [];

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

    if (!devices.length) {
      svg.setAttribute("aria-label", "Floorplan, not computed yet.");
      text(svg, {
        x: view.width / 2, y: view.height / 2,
        text: "run the floorplan to see the devices to scale",
        anchor: "middle", size: 12, className: "bode-placeholder"
      });
      return svg;
    }

    var left = MARGIN.left;
    var right = view.width - MARGIN.right;
    var top = MARGIN.top;
    var bottom = view.height - MARGIN.bottom;

    // Transposed: the row of devices runs down the page and each device's
    // channel width runs across it. The placement is unchanged and the
    // drawing is still to scale; this is the orientation that fits a wide
    // figure, since a row of narrow devices is tall and thin the other way
    // up. Both axes are labelled with what they measure.
    // Everything the file holds, not just the devices: the taps sit above
    // the row and the routing above those, and a drawing that stops at the
    // devices is not a drawing of this layout.
    var routing = (data && data.routing) || {};
    var span = { x1: 0, y1: 0, x2: plan.width_um, y2: plan.height_um };
    function cover(box) {
      if (!box) { return; }
      span.x1 = Math.min(span.x1, box.x1);
      span.y1 = Math.min(span.y1, box.y1);
      span.x2 = Math.max(span.x2, box.x2);
      span.y2 = Math.max(span.y2, box.y2);
    }
    (plan.wells || []).forEach(cover);
    (plan.taps || []).forEach(cover);
    Object.keys(routing).forEach(function (net) {
      cover(routing[net].span);
      (routing[net].stubs || []).forEach(cover);
    });

    var alongUm = span.x2 - span.x1;
    var acrossUm = span.y2 - span.y1;

    var fit = Math.min((right - left) / acrossUm,
                       (bottom - top) / alongUm);

    // Shrinking to fit is only honest until the names stop being readable.
    // Below that the scale holds and the drawing grows, the same bargain
    // the schematic makes.
    var pitch = plan.width_um / devices.length;
    var perMicron = Math.max(fit, ROW_PITCH_PX / pitch);

    var drawnWidth = acrossUm * perMicron;
    var drawnHeight = alongUm * perMicron;

    // Grown past the frame: widen the box and let the figure scroll it.
    if (drawnWidth > right - left || drawnHeight > bottom - top) {
      view.width = Math.round(drawnWidth + MARGIN.left + MARGIN.right);
      view.height = Math.round(drawnHeight + MARGIN.top + MARGIN.bottom);
      svg.setAttribute("viewBox", "0 0 " + view.width + " " + view.height);
      svg.style.width = view.width + "px";
      svg.style.maxWidth = "none";
      right = view.width - MARGIN.right;
      bottom = view.height - MARGIN.bottom;
    } else {
      svg.style.width = "";
      svg.style.maxWidth = "";
    }

    var originX = left + ((right - left) - drawnWidth) / 2;
    var originY = top + ((bottom - top) - drawnHeight) / 2;

    // The drawing is transposed: the row runs down the page and each
    // device's channel width runs across it. Every shape goes through
    // these two, so nothing can be placed by a different rule.
    function pageX(acrossUm) {
      return originX + (acrossUm - span.y1) * perMicron;
    }
    function pageY(alongUm) {
      return originY + (alongUm - span.x1) * perMicron;
    }
    function box(cls, item, title) {
      var node = add(svg, "rect", {
        "class": cls,
        x: pageX(item.y1), y: pageY(item.x1),
        width: (item.y2 - item.y1) * perMicron,
        height: (item.x2 - item.x1) * perMicron
      });
      if (title) {
        add(node, "title", {}).textContent = title;
      }
      return node;
    }

    svg.setAttribute(
      "aria-label",
      "Floorplan " + plan.width_um.toFixed(2) + " by "
      + plan.height_um.toFixed(2) + " microns, "
      + devices.length + " devices drawn to scale, with their taps and "
      + Object.keys(routing).length + " routed nets."
    );

    // The bounding box the area was measured over.
    add(svg, "rect", {
      "class": "fp-bounds",
      x: pageX(0), y: pageY(0),
      width: plan.height_um * perMicron,
      height: plan.width_um * perMicron
    });

    // The n-well the PMOS group sits in, drawn behind them.
    (plan.wells || []).forEach(function (well) {
      box("fp-well", well, "n-well holding " + (well.holds || []).join(", "));
    });

    // The taps that give the well and the substrate their voltage. Without
    // one a well floats, which is how a CMOS circuit latches up.
    (plan.taps || []).forEach(function (tap) {
      box("fp-tap", tap,
        (tap.kind === "ntap" ? "n-well tap" : "substrate tap")
        + " for " + (tap.serves || []).join(", "));
    });

    // The routing, on the two metal layers it is actually drawn on.
    Object.keys(routing).sort().forEach(function (net) {
      var item = routing[net];
      (item.stubs || []).forEach(function (stub) {
        box("fp-stub", stub, net + " to " + stub.device + "." + stub.terminal);
      });
      box("fp-track", item.span, net + " track");
      text(svg, {
        x: pageX(item.span.y2) + 5,
        y: pageY((item.span.x1 + item.span.x2) / 2) + 3.5,
        text: net, anchor: "start", className: "fp-net"
      });
    });

    devices.forEach(function (device) {
      // Each device is a bar as long as it is wide: the channel width is
      // the dimension that varies between them, so it is the one to see.
      var w = device.height * perMicron;
      var h = device.width * perMicron;
      var x = pageX(device.y);
      var y = pageY(device.x);

      add(svg, "rect", {
        "class": device.kind === "pfet" ? "fp-device is-pfet" : "fp-device",
        x: x, y: y, width: w, height: h
      });

      // The name sits outside the bar, in the margin, where it cannot
      // collide with a neighbour however thin the device is.
      text(svg, {
        x: originX - 6, y: y + h / 2 + 3.5, text: device.name,
        anchor: "end", className: "fp-label"
      });

      if (w >= LABEL_MIN_PX * 3) {
        text(svg, {
          x: x + w - 6, y: y + h / 2 + 3.5,
          text: device.device_width.toFixed(2) + " µm",
          anchor: "end", className: "fp-label"
        });
      }
    });

    // A scale bar, because a drawing to scale is only useful with one.
    var step = scaleStep(plan.height_um);
    var barLength = step * perMicron;
    var barY = bottom + 16;
    add(svg, "line", {
      "class": "fp-scale", x1: originX, y1: barY,
      x2: originX + barLength, y2: barY
    });
    add(svg, "line", {
      "class": "fp-scale", x1: originX, y1: barY - 4, x2: originX, y2: barY + 4
    });
    add(svg, "line", {
      "class": "fp-scale", x1: originX + barLength, y1: barY - 4,
      x2: originX + barLength, y2: barY + 4
    });
    text(svg, {
      x: originX + barLength + 8, y: barY + 3.5,
      text: step + " µm", className: "bode-text"
    });

    // Both dimensions, named for what they actually measure.
    text(svg, {
      x: originX + drawnWidth / 2, y: originY - 8,
      text: "widest device " + plan.height_um.toFixed(2) + " µm",
      anchor: "middle", className: "bode-text"
    });
    var middle = originY + drawnHeight / 2;
    text(svg, {
      x: 13, y: middle,
      text: "row " + plan.width_um.toFixed(2) + " µm",
      anchor: "middle", className: "bode-text",
      transform: "rotate(-90 13 " + middle + ")"
    });

    return svg;
  }

  window.drawFloorplan = drawFloorplan;
})(window, document);
