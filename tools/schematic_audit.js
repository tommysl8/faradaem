/* Schematic geometry audit. Machine tooling, not shipped to any page.
 *
 * Paste the whole file into the browser console on http://localhost:8000
 * (or run it through the devtools snippet runner). It draws all ten
 * circuits offscreen with measured values rendered, then checks each one
 * mechanically: no label sits on a stroke or another label, no wire ends
 * in space, no wire transits a symbol body, no terminal is unconnected,
 * no crossing sits near a junction dot or a wire end, no two symbols
 * overlap, and nothing leaves the frame. The symbol bodies and terminals
 * come from the data-sym / data-terminals attributes every primitive in
 * static/schematic.js tags itself with.
 *
 * The result is an object keyed by circuit; empty means clean. Text boxes
 * are shrunk a few pixels vertically before comparison because getBBox
 * inflates ascent and descent past the drawn glyphs, and the house style
 * stacks a name over its value 14 pixels apart on purpose.
 */
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var host = document.createElement("div");
  host.style.cssText = "position:absolute;left:-12000px;top:0;";
  document.body.appendChild(host);

  var CASES = [
    ["divider", window.drawDivider, { vdd: 5, r1: 10000, r2: 10000, vout: 2.5 }],
    ["rc_lowpass", window.drawRCLowpass, { r: 10000, c: 100e-9, f3db: 159.2 }],
    ["rc_highpass", window.drawRCHighpass, { r: 10000, c: 100e-9, f3db: 159.2 }],
    ["rlc_bandpass", window.drawRLCBandpass, { r: 1000, l: 10e-3, c: 100e-9, f0: 5033 }],
    ["inverting", window.drawInvertingAmp, { rin: 10000, rf: 100000, a0: 100000, gbw: 1e6, gain_db: 20.0 }],
    ["twopole", window.drawTwopoleAmp, { rin: 10000, rf: 100000, a0: 100000, gbw: 1e6, fp2: 2e6, phase_margin: 61.4 }],
    ["nfet_cs", window.drawNfetCsAmp, { w: 10e-6, l: 0.15e-6, vgs: 0.7, rd: 10000, vdd: 1.8, cl: 2e-12, gain_db: 18.33 }],
    ["ota5t", window.drawOta5t, { wpair: 20e-6, wload: 40e-6, ibias: 20e-6, cl: 2e-12, phase_margin: 88.9 }],
    ["opamp", window.drawOpampTwoStage, { wpair: 20e-6, wload: 40e-6, ibias: 20e-6, w6: 100e-6, w7: 50e-6, cc: 2e-12, rz: 1000, cl: 2e-12, phase_margin: 61.2 }],
    ["folded", window.drawFoldedCascode, { wpair: 20e-6, wfold: 100e-6, wcasc: 100e-6, ibias: 20e-6, cl: 2e-12, phase_margin: 61.2 }]
  ];

  function pt(svg, m, x, y) {
    var p = svg.createSVGPoint(); p.x = x; p.y = y;
    var q = p.matrixTransform(m);
    return [q.x, q.y];
  }
  function d2(a, b) {
    var dx = a[0] - b[0], dy = a[1] - b[1];
    return Math.sqrt(dx * dx + dy * dy);
  }
  function distPointSeg(p, a, b) {
    var vx = b[0] - a[0], vy = b[1] - a[1];
    var wx = p[0] - a[0], wy = p[1] - a[1];
    var L = vx * vx + vy * vy;
    var t = L ? Math.max(0, Math.min(1, (wx * vx + wy * vy) / L)) : 0;
    return d2(p, [a[0] + t * vx, a[1] + t * vy]);
  }
  // Proper crossing of open interiors; null for touches, tees, collinear.
  function crossing(a, b, c, d) {
    var r = [b[0] - a[0], b[1] - a[1]], s = [d[0] - c[0], d[1] - c[1]];
    var den = r[0] * s[1] - r[1] * s[0];
    if (Math.abs(den) < 1e-9) return null;
    var t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den;
    var u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den;
    if (t < 0.02 || t > 0.98 || u < 0.02 || u > 0.98) return null;
    return [a[0] + t * r[0], a[1] + t * r[1]];
  }
  // Chord length of segment inside axis-aligned rect (Liang-Barsky).
  function chordInRect(a, b, R) {
    var t0 = 0, t1 = 1, dx = b[0] - a[0], dy = b[1] - a[1];
    var p = [-dx, dx, -dy, dy];
    var q = [a[0] - R.x0, R.x1 - a[0], a[1] - R.y0, R.y1 - a[1]];
    for (var i = 0; i < 4; i++) {
      if (Math.abs(p[i]) < 1e-9) { if (q[i] < 0) return 0; continue; }
      var r = q[i] / p[i];
      if (p[i] < 0) { if (r > t1) return 0; if (r > t0) t0 = r; }
      else { if (r < t0) return 0; if (r < t1) t1 = r; }
    }
    return (t1 - t0) * Math.sqrt(dx * dx + dy * dy);
  }
  function rectsOverlap(A, B, pad) {
    var x = Math.min(A.x1, B.x1) - Math.max(A.x0, B.x0) + pad;
    var y = Math.min(A.y1, B.y1) - Math.max(A.y0, B.y0) + pad;
    return x > 0 && y > 0 ? Math.min(x, 1000) * Math.min(y, 1000) : 0;
  }
  function shrinkY(R, s) {
    return { x0: R.x0, x1: R.x1, y0: R.y0 + s, y1: R.y1 - s };
  }
  function fmt(p) { return Math.round(p[0]) + "," + Math.round(p[1]); }

  var report = {};

  CASES.forEach(function (kase) {
    var name = kase[0], fn = kase[1], values = kase[2];
    var issues = [];
    if (typeof fn !== "function") { report[name] = ["drawer missing"]; return; }
    var svg = document.createElementNS(NS, "svg");
    host.appendChild(svg);
    try { fn(svg, values); } catch (e) {
      report[name] = ["draw threw: " + e.message]; host.removeChild(svg); return;
    }
    var vb = svg.getAttribute("viewBox").split(" ").map(Number);
    svg.setAttribute("width", vb[2]); svg.setAttribute("height", vb[3]);

    // ---- collect ----
    var strokes = []; // {el, segs:[[p,q],...], pts:[..], sym:groupEl|null, closed}
    var symGroups = [];
    Array.prototype.forEach.call(svg.querySelectorAll("[data-sym]"), function (g) {
      var m = g.getCTM();
      var bb = g.getBBox();
      var c1 = pt(svg, m, bb.x, bb.y), c2 = pt(svg, m, bb.x + bb.width, bb.y + bb.height);
      var terms = (g.getAttribute("data-terminals") || "").split(" ").filter(Boolean)
        .map(function (s) { var xy = s.split(",").map(Number); return pt(svg, m, xy[0], xy[1]); });
      symGroups.push({
        el: g, kind: g.getAttribute("data-sym"), terms: terms,
        rect: { x0: Math.min(c1[0], c2[0]), y0: Math.min(c1[1], c2[1]),
                x1: Math.max(c1[0], c2[0]), y1: Math.max(c1[1], c2[1]) }
      });
    });
    function owningSym(el) {
      for (var n = el; n && n !== svg; n = n.parentNode) {
        if (n.getAttribute && n.getAttribute("data-sym")) {
          for (var i = 0; i < symGroups.length; i++) {
            if (symGroups[i].el === n) return symGroups[i];
          }
        }
      }
      return null;
    }
    Array.prototype.forEach.call(svg.querySelectorAll(".sch-stroke"), function (el) {
      var m = el.getCTM(), pts = [], closed = false;
      if (el.tagName === "polyline") {
        for (var i = 0; i < el.points.numberOfItems; i++) {
          var p = el.points.getItem(i);
          pts.push(pt(svg, m, p.x, p.y));
        }
      } else if (el.tagName === "circle") {
        var cx = +el.getAttribute("cx"), cy = +el.getAttribute("cy"), r = +el.getAttribute("r");
        for (var k = 0; k <= 24; k++) {
          var a = k / 24 * 2 * Math.PI;
          pts.push(pt(svg, m, cx + r * Math.cos(a), cy + r * Math.sin(a)));
        }
        closed = true;
      } else if (el.tagName === "path") {
        var L = el.getTotalLength();
        var n = Math.max(2, Math.ceil(L / 3));
        for (var j = 0; j <= n; j++) {
          var q = el.getPointAtLength(L * j / n);
          pts.push(pt(svg, m, q.x, q.y));
        }
      } else { return; }
      var segs = [];
      for (var s = 0; s + 1 < pts.length; s++) segs.push([pts[s], pts[s + 1]]);
      strokes.push({ el: el, segs: segs, pts: pts, sym: owningSym(el), closed: closed });
    });
    var texts = [];
    Array.prototype.forEach.call(svg.querySelectorAll("text"), function (el) {
      var m = el.getCTM(), bb = el.getBBox();
      var c1 = pt(svg, m, bb.x, bb.y), c2 = pt(svg, m, bb.x + bb.width, bb.y + bb.height);
      texts.push({
        el: el, label: el.textContent,
        rect: { x0: Math.min(c1[0], c2[0]), y0: Math.min(c1[1], c2[1]),
                x1: Math.max(c1[0], c2[0]), y1: Math.max(c1[1], c2[1]) }
      });
    });
    var dots = [];
    Array.prototype.forEach.call(svg.querySelectorAll("circle.sch-fill"), function (el) {
      var m = el.getCTM();
      dots.push(pt(svg, m, +el.getAttribute("cx"), +el.getAttribute("cy")));
    });

    function minDistToOtherStrokes(p, self) {
      var best = 1e9;
      strokes.forEach(function (st) {
        if (st.el === self) return;
        st.segs.forEach(function (sg) {
          var d = distPointSeg(p, sg[0], sg[1]);
          if (d < best) best = d;
        });
      });
      return best;
    }

    // (a) text vs stroke, (b) text vs text
    texts.forEach(function (t, ti) {
      var S = shrinkY(t.rect, 2.5);
      var R = { x0: S.x0 - 1, y0: S.y0, x1: S.x1 + 1, y1: S.y1 };
      var hit = null;
      strokes.forEach(function (st) {
        if (hit) return;
        st.segs.forEach(function (sg) {
          if (hit) return;
          var dx = Math.abs(sg[1][0] - sg[0][0]), dy = Math.abs(sg[1][1] - sg[0][1]);
          if (dx > 6 && dy > 6) return; // diagonals: arrow marks, triangle
          if (chordInRect(sg[0], sg[1], R) > 1.5) hit = fmt(sg[0]) + "-" + fmt(sg[1]);
        });
      });
      if (hit) issues.push("TEXT-ON-STROKE: '" + t.label + "' vs seg " + hit);
      for (var tj = ti + 1; tj < texts.length; tj++) {
        if (rectsOverlap(shrinkY(t.rect, 3), shrinkY(texts[tj].rect, 3), 0) > 4) {
          issues.push("TEXT-ON-TEXT: '" + t.label + "' vs '" + texts[tj].label + "'");
        }
      }
      if (t.rect.x0 < vb[0] - 0.5 || t.rect.y0 < vb[1] - 0.5 ||
          t.rect.x1 > vb[0] + vb[2] + 0.5 || t.rect.y1 > vb[1] + vb[3] + 0.5) {
        issues.push("TEXT-OFF-CANVAS: '" + t.label + "'");
      }
    });

    // (c) wire transits a symbol body
    symGroups.forEach(function (sym) {
      var R = { x0: sym.rect.x0 + 2, y0: sym.rect.y0 + 2,
                x1: sym.rect.x1 - 2, y1: sym.rect.y1 - 2 };
      if (R.x1 <= R.x0 || R.y1 <= R.y0) return;
      strokes.forEach(function (st) {
        if (st.sym === sym) return;
        st.segs.forEach(function (sg) {
          if (chordInRect(sg[0], sg[1], R) > 3) {
            issues.push("WIRE-THROUGH-" + sym.kind.toUpperCase() + ": seg " +
              fmt(sg[0]) + "-" + fmt(sg[1]) + " through body at " + fmt([sym.rect.x0, sym.rect.y0]));
          }
        });
      });
    });

    // (d) dangling endpoints of plain wires
    strokes.forEach(function (st) {
      if (st.sym || st.closed) return;
      var axis = st.segs.every(function (sg) {
        return Math.abs(sg[0][0] - sg[1][0]) < 0.01 || Math.abs(sg[0][1] - sg[1][1]) < 0.01;
      });
      if (!axis) return;
      [st.pts[0], st.pts[st.pts.length - 1]].forEach(function (p) {
        var horiz = st.segs.length === 1 &&
          Math.abs(st.segs[0][0][1] - st.segs[0][1][1]) < 0.01;
        var len = 0;
        st.segs.forEach(function (sg) { len += d2(sg[0], sg[1]); });
        if (horiz && len >= 200) return; // rails carry bare ends
        if (minDistToOtherStrokes(p, st.el) <= 7) return;
        var nearTerm = symGroups.some(function (sym) {
          return sym.terms.some(function (t) { return d2(p, t) <= 2.5; });
        });
        if (nearTerm) return;
        var nearDot = dots.some(function (dt) { return d2(p, dt) <= 4.5; });
        if (nearDot) return;
        var nearText = texts.some(function (t) {
          return p[0] > t.rect.x0 - 20 && p[0] < t.rect.x1 + 20 &&
                 p[1] > t.rect.y0 - 20 && p[1] < t.rect.y1 + 20;
        });
        if (nearText) return;
        issues.push("DANGLING: wire end at " + fmt(p));
      });
    });

    // (e) unconnected terminals
    symGroups.forEach(function (sym) {
      sym.terms.forEach(function (t) {
        var d = 1e9;
        strokes.forEach(function (st) {
          if (st.sym === sym) return;
          st.segs.forEach(function (sg) {
            var dd = distPointSeg(t, sg[0], sg[1]);
            if (dd < d) d = dd;
          });
        });
        if (d <= 2) return;
        var nearOther = symGroups.some(function (o) {
          return o !== sym && o.terms.some(function (u) { return d2(t, u) <= 2.5; });
        });
        if (nearOther) return;
        var nearText = texts.some(function (tx) {
          return t[0] > tx.rect.x0 - 24 && t[0] < tx.rect.x1 + 24 &&
                 t[1] > tx.rect.y0 - 24 && t[1] < tx.rect.y1 + 24;
        });
        if (nearText) return;
        issues.push("OPEN-TERMINAL: " + sym.kind + " at " + fmt(t));
      });
    });

    // (f) crossings too close to dots or stroke endpoints
    var endpoints = [];
    strokes.forEach(function (st) {
      if (st.closed) return;
      endpoints.push(st.pts[0], st.pts[st.pts.length - 1]);
    });
    for (var i = 0; i < strokes.length; i++) {
      for (var j = i + 1; j < strokes.length; j++) {
        // A symbol may cross itself: the plus sign in a source is one.
        if (strokes[i].sym && strokes[i].sym === strokes[j].sym) continue;
        strokes[i].segs.forEach(function (sa) {
          strokes[j].segs.forEach(function (sb) {
            var x = crossing(sa[0], sa[1], sb[0], sb[1]);
            if (!x) return;
            dots.forEach(function (dt) {
              var d = d2(x, dt);
              if (d > 2.5 && d < 10) {
                issues.push("CROSSING-NEAR-DOT: " + fmt(x) + " is " + Math.round(d) + "px from dot " + fmt(dt));
              }
            });
            var eBest = 1e9;
            endpoints.forEach(function (ep) {
              var d = d2(x, ep);
              if (d < eBest) eBest = d;
            });
            if (eBest > 0.5 && eBest < 8) {
              issues.push("CROSSING-NEAR-ELBOW: " + fmt(x) + " is " + Math.round(eBest) + "px from a wire end");
            }
          });
        });
      }
    }

    // (g) overlapping symbol bodies
    for (var a = 0; a < symGroups.length; a++) {
      for (var b = a + 1; b < symGroups.length; b++) {
        var ov = rectsOverlap(symGroups[a].rect, symGroups[b].rect, -2);
        if (ov > 4) {
          issues.push("SYMBOL-OVERLAP: " + symGroups[a].kind + " at " +
            fmt([symGroups[a].rect.x0, symGroups[a].rect.y0]) + " vs " + symGroups[b].kind);
        }
      }
    }

    // (h) strokes off canvas
    strokes.forEach(function (st) {
      st.pts.forEach(function (p) {
        if (p[0] < vb[0] - 0.5 || p[1] < vb[1] - 0.5 ||
            p[0] > vb[0] + vb[2] + 0.5 || p[1] > vb[1] + vb[3] + 0.5) {
          issues.push("STROKE-OFF-CANVAS: point " + fmt(p));
        }
      });
    });

    host.removeChild(svg);
    var seen = {};
    issues = issues.filter(function (s) {
      if (seen[s]) return false; seen[s] = true; return true;
    });
    if (issues.length) report[name] = issues.slice(0, 30);
  });

  document.body.removeChild(host);
  return report;
})();
