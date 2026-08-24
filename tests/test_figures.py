"""The figure contract: scale limits, scroll lanes, and honest wire ends.

The drawing itself is JavaScript and is checked in the browser against the
real geometry. What Python can pin is the wiring those checks depend on:
that the schematic sits in a scroll lane, that the scale band exists in
one place with both limits, and that wires are not drawn with the round
caps that used to overshoot every corner.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_the_schematic_sits_in_a_scroll_lane():
    """A drawing too dense to shrink scrolls; it never overflows the page."""
    page = read("index.html")
    assert '<div class="figure-scroll">' in page
    assert '<svg id="schematic"' in page
    lane = page.index('<div class="figure-scroll">')
    assert lane < page.index('<svg id="schematic"')

    css = read("static/style.css")
    block = css[css.index(".figure-scroll {"):]
    assert "overflow-x: auto;" in block[:200]


def test_the_scale_band_has_both_limits():
    """Growth stops so small circuits do not fill the column; shrinking
    stops so dense ones stay readable."""
    app = read("static/app.js")
    assert "var UNIT_PX_MAX = 1.15;" in app
    assert "var UNIT_PX_MIN = 0.85;" in app
    assert "function fitSchematic()" in app
    # The fit must run off the size the drawer chose, not off a box a
    # previous fit already padded.
    assert "naturalView" in app


def test_wires_end_where_they_are_drawn():
    """A round cap adds half a stroke past the endpoint, which read as a
    stub at every corner."""
    schematic = read("static/schematic.js")
    assert '"stroke-linecap": "butt"' in schematic
    assert '"stroke-linecap": "round"' not in schematic


def test_plot_titles_live_outside_the_frames():
    """Inside the frame they sat where the trace runs."""
    bode = read("static/bodeplot.js")
    assert "function axisTitle(" in bode
    assert "rotate(-90 13 " in bode
    # The old inline trace labels are gone with the legend they replaced.
    assert 'text: "mag"' not in bode
    # Ticks read 10 kHz, not 10.00 kHz, so neighbours cannot touch.
    assert "function decadeLabel(" in bode
