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
    # to be drawn again on the way in. Each panel owns that redraw now, so
    # what this checks is that the reveal reaches every panel rather than
    # naming three of them.
    reveal = app.split("function showAnalysis")[1][:900]
    assert "panels.forEach" in reveal
    assert "panel.reveal()" in reveal
    for name in ("panel-step", "panel-sheet", "panel-layout"):
        assert "reveal:" in read("static/%s.js" % name), name


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

    # The renderers live with their panels now.
    for container, source, renderer in (
            ("stepMetrics", "panel-step", "renderStepResult"),
            ("sheetMetrics", "panel-sheet", "renderSheetResult")):
        panel = read("static/%s.js" % source)
        assert container + '.appendChild(el("span", "goal-label"' in panel
        assert container + '.appendChild(el("span", "goal-value"' in panel
        # No wrapper survives in either renderer.
        body = panel.split("function " + renderer)[1][:1200]
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


def test_every_symbol_declares_what_and_where_it_is():
    """The browser-side geometry audit can only prove a wire misses every
    transistor body if the symbols say where their bodies and terminals
    are. Each primitive tags its group; the audit reads the tags."""
    schematic = read("static/schematic.js")
    assert "function tagSymbol(" in schematic
    for kind in ('"nmos"', '"pmos"', '"isource"', '"dcsource"', '"ground"',
                 '"capacitor"', '"resistor"', '"inductor"', '"opamp"'):
        assert "tagSymbol(group, " + kind in schematic, kind
    # The three amplifiers draw their bias source with the primitive, not
    # an untagged inline circle the audit cannot see.
    assert schematic.count("isource(svg, {") == 3


def test_gate_buses_stay_out_of_transistor_bodies():
    """A bias bus drawn along the gate row runs straight through the body
    of every device between the gates it ties, because a gate lead ends at
    the symbol's edge and the body extends from there. All three MOS
    amplifiers route their buses in a clear channel and drop onto each
    gate from outside its symbol."""
    schematic = read("static/schematic.js")
    assert "wire(svg, 60, bY, 188, bY)" not in schematic
    assert "wire(svg, 60, bY, 524, bY)" not in schematic
    assert "wire(svg, 234, 464, 74, 464)" not in schematic


def test_the_folded_cascode_bottom_row_reaches_ground():
    """The first drawing left every bottom-row source dangling eighteen
    pixels above the ground rail: five wires to nowhere."""
    schematic = read("static/schematic.js")
    for tap in ("wire(svg, 74, 534, 74, yGnd)",
                "wire(svg, 166, 534, 166, yGnd)",
                "wire(svg, 312, 534, 312, yGnd)",
                "wire(svg, 470, 534, 470, yGnd)",
                "wire(svg, 590, 534, 590, yGnd)"):
        assert tap in schematic, tap


def test_plot_titles_live_outside_the_frames():
    """Inside the frame they sat where the trace runs."""
    bode = read("static/bodeplot.js")
    assert "function axisTitle(" in bode
    assert "rotate(-90 13 " in bode
    # The old inline trace labels are gone with the legend they replaced.
    assert 'text: "mag"' not in bode
    # Ticks read 10 kHz, not 10.00 kHz, so neighbours cannot touch.
    assert "function decadeLabel(" in bode


# ---------------------------------------------------------------------------
# the layout drawing shows what the file holds
# ---------------------------------------------------------------------------


def test_the_layout_drawing_shows_the_taps_and_the_routing():
    """A picture that leaves out what the GDS holds is not a picture of that
    GDS. The taps and both metal layers are in the file, so they are in the
    drawing."""
    plot = read("static/layoutplot.js")
    for drawn in ("fp-tap", "fp-track", "fp-stub", "fp-well"):
        assert drawn in plot, drawn
    # And each has somewhere to be styled.
    style = read("static/style.css")
    for rule in (".fp-tap", ".fp-track", ".fp-stub"):
        assert rule in style, rule


def test_the_drawing_sizes_itself_to_everything_it_draws():
    """The taps sit above the device row and the routing above those. A
    drawing that measures only the devices clips the rest."""
    plot = read("static/layoutplot.js")
    assert "plan.taps" in plot
    assert "data.routing" in plot
    # One span covering all of it, not the device row alone.
    assert "function cover(box)" in plot


def test_every_shape_goes_through_one_mapping():
    """Two ways of turning microns into pixels is two chances to disagree
    with the geometry the numbers were measured over."""
    plot = read("static/layoutplot.js")
    assert "function pageX(" in plot
    assert "function pageY(" in plot


def test_the_panel_is_no_longer_called_a_floorplan():
    """It routes, checks thirty-two rules and compares against the netlist.
    Calling that a floorplan undersells it and misleads."""
    page = read("index.html")
    app = read("static/app.js")
    assert '<h2 class="panel-head">Layout</h2>' in page
    assert 'label: "Layout"' in app


# ---------------------------------------------------------------------------
# the front end has no module system, so scope is the thing to watch
# ---------------------------------------------------------------------------


def test_no_script_declares_one_name_twice():
    """Two `function foo()` in one closure is not an error in JavaScript.
    The later one silently wins for the whole scope, including for calls
    written above it.

    This is not hypothetical. app.js declared stepValue twice, once for
    arrow-key stepping and once to format a step-response metric, and the
    formatter won: pressing the up arrow on any form field replaced its
    contents with an em dash. Nothing failed, nothing logged, and the
    feature was simply gone.
    """
    import re

    for name in ("app.js", "schematic.js", "bodeplot.js", "stepplot.js",
                 "layoutplot.js"):
        source = read("static/" + name)
        declared = re.findall(r"^  function ([A-Za-z_$][\w$]*)", source, re.M)
        repeated = sorted({n for n in declared if declared.count(n) > 1})
        assert not repeated, (name, repeated)


def test_the_arrow_key_stepper_is_the_one_exposed_for_testing():
    """FaradaemAppInternals exists so the stepping arithmetic can be
    checked. It has to be pointing at the stepper."""
    source = read("static/app.js")
    assert "window.FaradaemAppInternals = { stepValue: stepValue };" in source
    # Three arguments: the value, the direction, and whether shift was held.
    assert "function stepValue(value, direction, shift)" in source


# ---------------------------------------------------------------------------
# the hero, the bench, and the keyboard
# ---------------------------------------------------------------------------


def test_the_hero_shows_the_real_layout_and_not_an_illustration():
    """The image beside the claim is the folded cascode as the tool drew
    it, generated from the same shapes the GDS is written from. A stock
    picture would be decoration; this is the product."""
    page = read("index.html")
    assert "/static/hero-layout.svg" in page

    art = read("static/hero-layout.svg")
    assert art.count("<rect") > 300         # hundreds of real rectangles
    assert "foundry" in art                  # and it says what it is


def test_the_bench_has_a_slot_per_verdict():
    page = read("index.html")
    for slot in ("bench-sim", "bench-drc", "bench-signoff", "bench-lvs"):
        assert 'id="%s"' % slot in page, slot
    # And the panels can reach it.
    assert "window.FaradaemBench" in read("static/app.js")
    assert "FaradaemBench.set" in read("static/panel-layout.js")


def test_the_bench_resets_when_the_circuit_changes():
    """A verdict about one circuit says nothing about the next."""
    app = read("static/app.js")
    assert "function benchReset()" in app
    select_body = app.split("function select(")[1][:900]
    assert "benchReset()" in select_body


def test_every_circuit_chip_carries_a_glyph():
    from spice import circuits
    app = read("static/app.js")
    glyphs = app.split("var GLYPHS = {")[1].split("};")[0]
    for circuit_id in circuits.CIRCUIT_ORDER:
        assert circuit_id + ":" in glyphs, circuit_id


def test_ctrl_enter_runs_and_the_hint_says_so():
    app = read("static/app.js")
    assert 'event.ctrlKey || event.metaKey' in app
    page = read("index.html")
    assert "<kbd>Ctrl</kbd>" in page


def test_a_delta_formats_like_its_own_stat():
    """Half a decibel is 0.50 dB, never 500 mdB: the delta goes through
    the same presenter the number itself does."""
    app = read("static/app.js")
    delta = app.split("function deltaText")[1][:600]
    assert "present(Math.abs(change), spec)" in delta
    assert "formatEngineering" not in delta


def test_the_slow_paths_keep_time():
    """A reader watching a forty-second run sees seconds climb, not a
    frozen caption."""
    app = read("static/app.js")
    assert "function tickStart(" in app
    for panel in ("panel-step", "panel-sheet", "panel-layout"):
        assert "tickStart(" in read("static/" + panel + ".js"), panel


def test_the_manual_contents_is_a_rail_on_wide_screens():
    css = read("static/style.css")
    rail = css.split(".manual-grid {")[1][:800]
    assert "grid-template-columns" in rail
    assert "position: sticky" in css.split(".manual-grid .toc {")[1][:300]
    assert '<div class="manual-grid">' in read("manual.html")


# ---------------------------------------------------------------------------
# the workbench
# ---------------------------------------------------------------------------


def test_the_datasheet_tab_and_its_sections_exist():
    page = read("index.html")
    for anchor in ("pane-datasheet", "charact-run", "charact-stored",
                   "compare-a", "compare-b", "pin-state", "packet-run",
                   "charact-stale"):
        assert 'id="%s"' % anchor in page, anchor


def test_the_pin_chip_lives_with_the_numbers():
    """Pinning is offered where the result lands, not in a tab the user
    would have to know to open."""
    page = read("index.html")
    result = page.split('id="result"')[1].split("</div>\n\n")[0]
    assert 'id="pin-set"' in page
    app = read("static/app.js")
    assert '"/api/pin"' in app
    # The chip appears only after a successful run.
    assert 'show(id("pin-row"), true)' in app


def test_the_mentor_asks_three_questions():
    page = read("index.html")
    for anchor in ("triage-run", "blame-run", "sweep-run"):
        assert 'id="%s"' % anchor in page, anchor
    # Each button that costs simulations says so before it is pressed.
    assert "1 simulation" in page
    assert "8 simulations" in page


def test_the_sweep_is_never_called_a_pareto_front():
    """The honesty rule from the review: a one-knob slice must not wear
    the name of the thing it is not."""
    for path in ("static/app.js", "spice/triage.py"):
        text = read(path)
        lowered = text.lower()
        for line in lowered.splitlines():
            if "pareto" in line:
                assert "not a pareto" in line, (path, line.strip())


def test_the_autopsy_lives_in_the_robustness_pane():
    page = read("index.html")
    assert 'id="autopsy-run"' in page
    panel = read("static/panel-robust.js")
    assert '"autopsy"' in panel
    assert "not exposed" in panel


def test_the_workbench_pages_are_wired():
    for path, needle in (
        ("notebook.html", "notebook.js"),
        ("datasheet.html", "datasheet.js"),
    ):
        assert needle in read(path), path
    server = read("server.py")
    for route in ("/api/workbench", "/api/triage", "/api/pin",
                  "/api/packet", "/api/notebook", "/api/doctor"):
        assert '"%s"' % route in server, route


def test_the_datasheet_columns_are_honest():
    """worst observed, never min or max: eleven deterministic corners are
    a sample, not a guarantee."""
    panel = read("static/panel-datasheet.js")
    assert "worst observed" in panel
    assert "no guardband" in panel
    printable = read("static/datasheet.js")
    assert "worst observed" in printable


# ---------------------------------------------------------------------------
# the second polish pass: width, light mode, side-by-side suites
# ---------------------------------------------------------------------------


def test_the_page_uses_its_width():
    css = read("static/style.css")
    assert "--shell: 1400px;" in css
    # Panes that are all panel take the whole row; the datasheet's tables
    # were unreadable in a form's width.
    assert "#pane-datasheet,\n#pane-design {" in css


def test_light_mode_exists_and_dark_is_the_default():
    css = read("static/style.css")
    assert ':root[data-theme="light"]' in css
    # The default needs no attribute: the dark tokens live on :root bare.
    for page in ("index.html", "manual.html", "about.html",
                 "changelog.html", "notebook.html", "datasheet.html"):
        text = read(page)
        assert 'id="theme-toggle"' in text, page
        assert "/static/theme.js" in text, page
        assert 'data-theme' not in text.split("<html")[1][:120], page
    theme = read("static/theme.js")
    assert "localStorage" in theme
    assert '"light"' in theme


def test_the_two_robust_suites_run_side_by_side():
    """PVT and Monte Carlo answer different questions and share nothing
    but the circuit; one waiting for the other wasted real minutes."""
    page = read("index.html")
    for anchor in ("robust-progress-pvt", "robust-progress-mc",
                   "robust-table-pvt", "robust-table-mc",
                   "robust-stop-pvt", "robust-stop-mc"):
        assert 'id="%s"' % anchor in page, anchor
    panel = read("static/panel-robust.js")
    assert "slots = {" in panel
    # Starting one suite never disables the other's button.
    assert "robustPvt.disabled = true" not in panel.replace(
        "slot.button.disabled = true", "")


def test_the_charact_button_stays_a_button():
    """The label is short; what the run covers is a sentence in prose."""
    panel = read("static/panel-datasheet.js")
    assert '"Write the datasheet (about "' in panel
    assert "charact-covers" in panel
    page = read("index.html")
    assert 'id="charact-covers"' in page


def test_the_marker_labels_get_three_rows():
    plot = read("static/bodeplot.js")
    assert "rowLastX = [-Infinity, -Infinity, -Infinity]" in plot
    assert "top: 56" in plot
