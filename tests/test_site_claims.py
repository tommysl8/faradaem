"""Every number the site states, checked against the thing it describes.

This file exists because four pages disagreed with the code and with each
other about the same fact. The fast rule checker knew thirty-six rules; the
home page said thirty-two, the README said thirty-two in one place and
thirty-five in another, and the manual said thirty-six. Three of the four
were wrong and nothing could notice, because each was typed by hand into a
different file.

So the pages carry tokens and this scans the rendered output for the
digits and the spellings that would mean a stale number came back. A claim
that cannot be derived from the code should not be on a page; the one
exception is called out by name below.
"""

import io
import os
import re

import pytest

from spice import deployment, siteinfo
from tools import build_static

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Prose files that make claims about the code but are not built pages.
PROSE = ("README.md",)

#: The changelog is a record of what shipped, release by release. "the fast
#: checker knows all three, thirty-two to thirty-five" is a true statement
#: about what 1.10.0 changed, and rewriting it to today's number would make
#: the history wrong instead of the present. History is exempt by name.
HISTORY = ("changelog.html",)


@pytest.fixture(scope="module")
def rendered():
    """Every page as the reader receives it, both deployments."""
    pages = {}
    for mode in deployment.MODES:
        for name in build_static.PAGES + (build_static.NOT_FOUND,):
            with io.open(os.path.join(PROJECT, name), encoding="utf-8") as f:
                pages[(mode, name)] = deployment.render(f.read(), mode)
    return pages


def _visible(html):
    """The text a reader sees, with markup and script bodies removed."""
    text = re.sub(r"(?s)<script.*?</script>", " ", html)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# the counts the code owns
# ---------------------------------------------------------------------------


def test_no_page_states_a_rule_count_the_checker_disagrees_with(rendered):
    right = siteinfo.word(siteinfo.counts()["drc_rules"])
    wrong = {"thirty-two", "thirty-three", "thirty-four", "thirty-five",
             "thirty-seven", "thirty-eight"} - {right}
    for (mode, name), html in rendered.items():
        if name in HISTORY:
            continue
        text = _visible(html).lower()
        for said in wrong:
            assert said + " rules" not in text, (mode, name, said)


def test_no_prose_file_states_a_stale_rule_count():
    right = siteinfo.word(siteinfo.counts()["drc_rules"])
    for name in PROSE:
        text = io.open(os.path.join(PROJECT, name), encoding="utf-8").read()
        lowered = text.lower()
        for said in ("thirty-two", "thirty-five"):
            if said == right:
                continue
            assert said + " rules" not in lowered, (name, said)
            assert said + " design rules" not in lowered, (name, said)


def test_the_home_page_states_the_real_sky130_count(rendered):
    """It said '3 of them SKY130 amplifiers' while four circuits are on
    SKY130. Both numbers are real; they are different numbers."""
    found = siteinfo.counts()
    text = _visible(rendered[(deployment.STATIC, "index.html")])
    assert str(found["circuits"]) + " circuits, " \
        + str(found["pdk_circuits"]) + " using SKY130" in text


def test_the_topology_count_is_stated_separately_from_the_circuit_count(
        rendered):
    found = siteinfo.counts()
    text = _visible(rendered[(deployment.LOCAL, "index.html")])
    assert siteinfo.word(found["topologies"]) + " SKY130 amplifier" in text
    # And it is not presented as the number of SKY130 circuits.
    assert siteinfo.word(found["topologies"]) + " SKY130 circuits" not in text


def test_no_page_calls_four_sky130_circuits_three(rendered):
    found = siteinfo.counts()
    assert found["pdk_circuits"] == 4
    for (mode, name), html in rendered.items():
        if name in HISTORY:
            continue
        text = _visible(html).lower()
        for wrong in ("three sky130 circuits", "3 sky130 circuits",
                      "3 of them sky130", "three of them sky130"):
            assert wrong not in text, (mode, name, wrong)


def test_the_pvt_corner_count_matches_the_suite(rendered):
    found = siteinfo.counts()
    text = _visible(rendered[(deployment.LOCAL, "index.html")])
    assert str(found["pvt_corners"]) + " PVT corners" in text
    assert "Run PVT corners (" + str(found["pvt_corners"]) \
        + " simulations)" in text


def test_the_monte_carlo_count_matches_the_default(rendered):
    found = siteinfo.counts()
    text = _visible(rendered[(deployment.LOCAL, "index.html")])
    assert "Run Monte Carlo (" + str(found["mc_runs"]) + " simulations)" in text


def test_the_version_on_the_page_is_the_version_in_the_stylesheet():
    css = io.open(os.path.join(PROJECT, "static", "style.css"),
                  encoding="utf-8").read()
    assert '--app-version: "' + siteinfo.version(PROJECT) + '"' in css


# ---------------------------------------------------------------------------
# what is deliberately still hand-written
# ---------------------------------------------------------------------------


def test_the_test_count_is_the_one_hand_maintained_claim():
    """Recorded rather than fixed, on purpose.

    A number of passing tests is a fact about one run on one machine with
    particular tooling installed, not a property of the source, so
    siteinfo cannot derive it and this file cannot check it. It is left as
    the single hand-maintained number on the site, and this test exists so
    that fact is visible rather than forgotten: if the tile is ever removed
    or made generated, delete this test with it.
    """
    page = io.open(os.path.join(PROJECT, "index.html"), encoding="utf-8").read()
    found = re.search(r"<b>(\d+)</b><span>tests green</span>", page)
    assert found, "the tests-green tile changed shape; revisit this test"
    assert "{{" not in found.group(0), "if it became a token, drop this test"


# ---------------------------------------------------------------------------
# tokens, not typing
# ---------------------------------------------------------------------------


def test_the_pages_carry_tokens_rather_than_typed_numbers():
    """The source must delegate; the render must resolve."""
    page = io.open(os.path.join(PROJECT, "index.html"), encoding="utf-8").read()
    for token in ("{{circuits}}", "{{pdk_circuits}}", "{{pvt_corners}}",
                  "{{repo_url}}", "{{site_origin}}", "{{drc_rules_word}}"):
        assert token in page, token


def test_no_rendered_page_leaks_a_token(rendered):
    for (mode, name), html in rendered.items():
        assert not siteinfo.LEFTOVER.search(html), (mode, name)


def test_the_repository_url_is_written_once():
    """Seven hardcoded copies is seven places a rename can be missed."""
    for name in build_static.PAGES + (build_static.NOT_FOUND,):
        text = io.open(os.path.join(PROJECT, name), encoding="utf-8").read()
        literal = re.findall(r"https://github\.com/[A-Za-z0-9_.-]+/faradaem",
                             text)
        assert literal == [], (name, literal)
