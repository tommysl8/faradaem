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
    assert uncheckable == {"NFET amp (SKY130)", "Op-amp (SKY130)", "OTA (SKY130)"}
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
