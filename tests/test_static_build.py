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
    assert (out / build_static.NOT_FOUND).is_file()
    for asset in build_static.ASSETS:
        assert (out / "static" / asset).is_file(), asset
    # Pages, the not-found page, the assets, and three generated files:
    # the catalogue, robots.txt and the sitemap.
    assert len(written) == (len(build_static.PAGES) + 1
                            + len(build_static.ASSETS) + 3)


def test_no_local_only_asset_reaches_the_published_build(site):
    """Code whose only controls the build deletes is dead weight."""
    out, written = site
    for asset in build_static.LOCAL_ONLY_ASSETS:
        assert not (out / "static" / asset).exists(), asset
        assert "static/" + asset not in written, asset


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


def test_the_page_reads_the_published_catalogue_without_asking_a_server():
    """This test used to pin the opposite behaviour, and was right to at
    the time: the page discovered it was static by requesting /api/circuits,
    being refused, and falling back. That discovery is what produced a
    request that could only fail and a visible flash of controls being
    taken away, so the mechanism was replaced rather than patched. What is
    pinned now is that the published site reads the file beside it, and
    that the decision is read from the document rather than asked for."""
    app = (build_static.__file__.rsplit("tools", 1)[0] + "static/app.js")
    text = open(app, encoding="utf-8").read()

    assert 'fetch("catalogue.json")' in text
    assert 'getAttribute("data-deployment")' in text
    # The mechanism that caused the flash must be gone, not merely unused.
    assert "applyStaticMode" not in text

    body = text.split("async function loadCatalogue")[1][:600]
    assert "if (isStatic)" in body
    assert 'fetch("catalogue.json")' in body
    # The /api call must sit on the far side of that branch.
    assert body.index("if (isStatic)") < body.index('api("/api/circuits")')


def test_the_host_configuration_builds_the_same_directory():
    root = build_static.__file__.rsplit("tools", 1)[0]
    config = json.loads(open(root + "vercel.json", encoding="utf-8").read())
    assert config["outputDirectory"] == "dist"
    assert config["buildCommand"].endswith("--out dist")
    assert config["cleanUrls"] is True
