"""The user manual, checked against the thing it documents.

A manual that drifts from the code is worse than no manual, because it is
believed. These tests tie the page to facts the rest of the suite already
owns: the circuit catalogue, the environment variable names, and the bias
messages the server actually sends.
"""

import io
import re

import pytest

from spice import circuits, runner

MANUAL = io.open("manual.html", encoding="utf-8").read()


def text_of(html):
    """Strip tags so assertions match what a reader sees, not the markup."""
    without_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", without_tags)


MANUAL_TEXT = text_of(MANUAL)

#: The manual capitalises terms as list headings, so content assertions
#: compare case-insensitively. Only the wording has to match, not the case.
MANUAL_LOWER = MANUAL_TEXT.lower()


# ---- the page itself -------------------------------------------------------


def test_manual_uses_the_shared_shell():
    assert "<title>Manual - Faradaem</title>" in MANUAL
    assert '<meta name="description"' in MANUAL
    assert "/static/style.css" in MANUAL
    assert 'FARAD<span class="wordmark-ae">&AElig;</span>M<span class="wordmark-tm">&trade;</span>' in MANUAL
    assert "github.com/tommysl8/faradaem" in MANUAL


def test_manual_links_to_every_other_page():
    for href in ('href="/"', 'href="/about"', 'href="/changelog"'):
        assert href in MANUAL, href


def test_manual_marks_itself_as_the_current_page():
    assert 'href="/manual" aria-current="page"' in MANUAL


def test_manual_carries_no_inline_script_or_style():
    assert "<script>" not in MANUAL
    assert "<style>" not in MANUAL
    assert "style=" not in MANUAL


def test_manual_loads_nothing_from_the_network():
    assert "cdn." not in MANUAL
    assert "@import" not in MANUAL
    assert 'src="http' not in MANUAL
    remaining = MANUAL.replace("https://github.com/tommysl8/faradaem", "")
    assert "https://" not in remaining


# ---- house style -----------------------------------------------------------


def test_manual_uses_no_em_dashes():
    # Prose on this project does not use them.
    assert "—" not in MANUAL
    assert "&#8212;" not in MANUAL


# ---- the manual agrees with the code ---------------------------------------


@pytest.mark.parametrize("circuit_id", circuits.CIRCUIT_ORDER)
def test_manual_describes_every_circuit_in_the_catalogue(circuit_id):
    """Adding a circuit without documenting it should fail here."""
    name = circuits.get_circuit(circuit_id)["name"]
    assert name in MANUAL_TEXT, name


def test_manual_names_the_real_environment_variables():
    assert runner.NGSPICE_ENV_VAR in MANUAL_TEXT
    assert runner.PDK_ROOT_ENV_VAR in MANUAL_TEXT


def test_manual_names_the_console_executable_and_warns_off_the_gui_one():
    assert runner.NGSPICE_EXE_NAME in MANUAL_TEXT
    # The rule that matters most on this project.
    assert "ngspice.exe" in MANUAL_TEXT


def test_manual_quotes_the_real_sweep_resolution():
    assert str(circuits.POINTS_PER_DECADE) + " points per decade" in MANUAL_TEXT


def test_manual_explains_the_circuits_that_ship_no_analytic_check():
    # The two real-device circuits do this, and the manual says which and why.
    uncheckable = {
        circuits.get_circuit(cid)["name"]
        for cid in circuits.CIRCUIT_ORDER
        if not circuits.get_circuit(cid)["checks"]
    }
    assert uncheckable == {"NFET amp (SKY130)", "Op-amp (SKY130)",
                           "OTA (SKY130)", "Folded cascode (SKY130)"}
    for name in uncheckable:
        assert name in MANUAL_TEXT, name
    assert "square law" in MANUAL_LOWER


def test_manual_covers_every_bias_caution_the_server_can_send():
    triode = circuits.cs_amp_bias_note(0.01, 1.8)
    weak = circuits.cs_amp_bias_note(1.79, 1.8)
    assert "triode" in triode and "triode" in MANUAL_LOWER
    assert "barely conducting" in weak and "barely conducting" in MANUAL_LOWER
    # And the hard refusal, which is a different path from the two cautions.
    assert "not amplifying" in MANUAL_LOWER


def test_manual_states_the_pdk_load_time_the_timeout_allows():
    assert runner.PDK_TIMEOUT_S >= 60.0
    assert "10 to 30 seconds" in MANUAL_TEXT


# ---------------------------------------------------------------------------
# the contents, and the verification the panel reports
# ---------------------------------------------------------------------------


def test_every_section_is_in_the_contents():
    """A contents that misses a section sends the reader scrolling, which
    is the thing it was added to stop."""
    headings = re.findall(r'<h2 id="([^"]+)">', MANUAL)
    listed = set(re.findall(r'<a href="#([^"]+)">', MANUAL))
    assert headings, "the manual has no anchored headings"
    for anchor in headings:
        assert anchor in listed, anchor


def test_every_contents_link_points_at_a_real_section():
    headings = set(re.findall(r'<h2 id="([^"]+)">', MANUAL))
    for anchor in re.findall(r'<a href="#([^"]+)">', MANUAL):
        assert anchor in headings, anchor


def test_the_contents_is_grouped_by_how_the_tool_is_used():
    for group in ("Getting a number", "Trusting it", "Building it"):
        assert group in MANUAL_TEXT, group


def test_the_manual_explains_both_verifications_as_different_questions():
    """A rule check and a layout comparison answer different things, and a
    reader who thinks they are the same will over-trust a clean result."""
    assert "layout versus schematic" in MANUAL_LOWER
    assert "design rules" in MANUAL_LOWER
    # The claims have to track the code: the fast checker's rule count,
    # and how the real comparison matches. Never assert a stale number.
    from spice import drc
    counts = {35: "thirty-five", 36: "thirty-six",
              37: "thirty-seven", 38: "thirty-eight", 39: "thirty-nine"}
    checked = len(drc.CHECKED_RULES)
    assert counts.get(checked, str(checked)) + " rules" in MANUAL_LOWER, checked
    for said in ("by topology", "the fast loop"):
        assert said in MANUAL_LOWER, said


def test_the_manual_never_claims_sign_off():
    for boast in ("is sign-off", "fully verified", "tapeout ready",
                  "guaranteed to work"):
        assert boast not in MANUAL_LOWER, boast


def test_the_page_has_somewhere_to_report_both_verdicts():
    page = io.open("index.html", encoding="utf-8").read()
    for needed in ('id="layout-verify"', 'id="layout-verify-list"',
                   'id="layout-verify-note"'):
        assert needed in page, needed


def test_the_panel_reports_the_layout_comparison_and_not_only_the_rules():
    # The layout panel lives in its own file now.
    app = io.open("static/panel-layout.js", encoding="utf-8").read()
    assert "result.lvs" in app
    assert "Layout versus schematic" in app
    # And it says what it did not check, in the panel itself.
    assert "Still not checked" in app
    # And what stays outside the cell: ports are fine, ideal sources
    # standing in for on-chip references are named as the cheat they are.
    assert "External to this cell" in app
    assert "result.lvs.undrawn" in app
    assert "voltage source" in app
