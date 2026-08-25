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


def test_a_wide_drawing_cannot_push_the_page():
    """A grid item will not shrink below its content unless told to.

    Without this the op-amp at its floor scale, 646 pixels wide, made the
    single column on a phone 646 wide too, and every panel in it went off
    the screen edge. The figure has to scroll inside itself instead.
    """
    css = read("static/style.css")
    block = css[css.index(".sim > * {"):]
    assert "min-width: 0;" in block[:80]


def test_the_analyses_live_in_one_tabbed_section():
    """They are not part of setting a circuit up, so they sit below the form
    that is, one at a time. Stacked, they made the left column 6356 pixels
    against a 962 pixel right one."""
    page = read("index.html")
    assert '<section class="analysis hidden" id="analysis"' in page
    for key in ("design", "step", "sheet", "robust"):
        assert '<div class="analysis-pane hidden" id="pane-%s">' % key in page
        # The panel inside must not carry its own hidden class: the pane
        # owns visibility now, and two owners means neither works.
        panel = page[page.index('id="pane-%s"' % key):]
        head = panel[:panel.index(">", panel.index("<section"))]
        assert "hidden" not in head, key

    app = read("static/app.js")
    assert "function renderAnalysis()" in app
    assert "function showAnalysis(" in app
    # A plot drawn while its pane was hidden measures zero width, so it has
    # to be drawn again on the way in.
    reveal = app.split("function showAnalysis")[1][:900]
    assert "drawStep" in reveal and "drawTransfer" in reveal


def test_the_hidden_utility_cannot_be_overridden():
    """At equal specificity a later display wins, which is how a badge
    marked hidden went on rendering as an empty box beside the reading."""
    css = read("static/style.css")
    block = css[css.index(".hidden {"):]
    assert "display: none !important;" in block[:60]


def test_metric_pairs_are_cells_not_wrapped_rows():
    """The grid lays out its own children. A pair wrapped in a div lands in
    one cell with the label and the value drawn on top of each other."""
    css = read("static/style.css")
    grid = css[css.index(".metric-pairs {"):]
    assert "display: grid;" in grid[:120]
    assert "grid-template-columns: max-content 1fr;" in grid[:200]

    app = read("static/app.js")
    for container in ("stepMetrics", "sheetMetrics"):
        assert container + '.appendChild(el("span", "goal-label"' in app
        assert container + '.appendChild(el("span", "goal-value"' in app
    # No wrapper survives in either renderer.
    for renderer in ("renderStepResult", "renderSheetResult"):
        body = app.split("function " + renderer)[1][:1200]
        assert 'el("div")' not in body, renderer


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
