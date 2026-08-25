"""The published build: complete, self-consistent, and honest.

The site that goes to faradaem.com has no simulator behind it. These
tests pin the two things that could quietly go wrong: a page or asset
missing from the build, and a catalogue that has drifted from the one
the running server serves.
"""

import json

import pytest

from spice import circuits
from tools import build_static


@pytest.fixture(name="site")
def build_the_site(tmp_path):
    out = tmp_path / "dist"
    written = build_static.build(str(out))
    return out, written


def test_every_page_and_asset_is_published(site):
    out, written = site
    for page in build_static.PAGES:
        assert (out / page).is_file(), page
    for asset in build_static.ASSETS:
        assert (out / "static" / asset).is_file(), asset
    assert len(written) == len(build_static.PAGES) + len(build_static.ASSETS) + 1


def test_the_pages_reference_nothing_that_was_left_out(site):
    """Every /static/ reference in a page must exist in the build."""
    out, _ = site
    published = {"/static/" + name for name in build_static.ASSETS}
    for page in build_static.PAGES:
        text = (out / page).read_text(encoding="utf-8")
        for marker in ('href="/static/', 'src="/static/'):
            start = 0
            while True:
                at = text.find(marker, start)
                if at == -1:
                    break
                begin = at + len(marker) - len("/static/")
                end = text.index('"', begin)
                assert text[begin:end] in published, text[begin:end]
                start = end


def test_the_catalogue_matches_the_live_one(site):
    """The static page reads this file where it would read the server."""
    out, _ = site
    published = json.loads((out / "catalogue.json").read_text(encoding="utf-8"))
    assert published == {"circuits": circuits.catalog()}
    assert len(published["circuits"]) == len(circuits.catalog())


def test_rebuilding_replaces_the_previous_output(tmp_path):
    """A stale file from an earlier build must not survive into a deploy."""
    out = tmp_path / "dist"
    build_static.build(str(out))
    stale = out / "leftover.html"
    stale.write_text("old", encoding="utf-8")
    build_static.build(str(out))
    assert not stale.exists()


def test_the_page_falls_back_to_the_published_catalogue():
    """Without this the static site would show only an error."""
    app = (build_static.__file__.rsplit("tools", 1)[0] + "static/app.js")
    text = open(app, encoding="utf-8").read()
    assert 'fetch("catalogue.json")' in text
    assert "applyStaticMode" in text
    # Static mode must put away everything that needs a measured number:
    # the strategist, the netlist reader, and every analysis. The four
    # analyses live behind one section now, so hiding them is one call.
    body = text.split("function applyStaticMode")[1][:800]
    for element in ('id("advise")', "netlistToggle", "renderAnalysis()"):
        assert element in body, element

    # And that call must actually be able to hide them all.
    strip = text.split("function renderAnalysis")[1][:600]
    assert "isStatic ? []" in strip
    assert "show(analysisSection" in strip


def test_the_host_configuration_builds_the_same_directory():
    root = build_static.__file__.rsplit("tools", 1)[0]
    config = json.loads(open(root + "vercel.json", encoding="utf-8").read())
    assert config["outputDirectory"] == "dist"
    assert config["buildCommand"].endswith("--out dist")
    assert config["cleanUrls"] is True
