"""The wordmark, the mark, and the page metadata.

Nothing else pins the brand, so a regression here would pass silently:
that is exactly what this file exists to prevent. The rules under test:

- Header logotype: FARADAEM set with the AE ligature and a trademark
  symbol, accent AE, no whitespace between the M and the symbol.
- The trademark appears in the logotype and nowhere else.
- Prose uses plain ASCII "Faradaem"; the ligature never leaks out of
  the logotype span.
- The mark (a white AE on the ink ground) ships as real routed files
  in every size the platforms ask for.
"""

import re
import struct
from pathlib import Path

import pytest

import server

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["index.html", "manual.html", "about.html", "changelog.html"]

#: The one true logotype: ligature, accent span, trademark span, and
#: critically no whitespace between the M and the symbol.
WORDMARK = (
    '<span class="wordmark-name">FARAD<span class="wordmark-ae">&AElig;</span>'
    'M<span class="wordmark-tm">&trade;</span></span>'
)


def page(name):
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", PAGES)
def test_header_renders_the_logotype(name):
    body = page(name)
    assert body.count(WORDMARK) == 1
    assert "PROJECT" not in body


@pytest.mark.parametrize("name", PAGES)
def test_trademark_appears_only_in_the_logotype(name):
    body = page(name)
    assert body.count("&trade;") == 1
    assert "\u2122" not in body


@pytest.mark.parametrize("name", PAGES)
def test_ligature_never_leaks_into_prose(name):
    rest = page(name).replace(WORDMARK, "")
    for form in ("&AElig;", "&aelig;", "\u00c6", "\u00e6"):
        assert form not in rest


@pytest.mark.parametrize("name", PAGES)
def test_page_metadata(name):
    body = page(name)
    title = re.search(r"<title>(.+?)</title>", body).group(1)
    if name == "index.html":
        assert title.startswith("Faradaem: ")
    else:
        assert re.fullmatch(r"[A-Z][a-z]+ - Faradaem", title)
    assert '<meta property="og:title" content="%s">' % title in body
    assert '<meta name="application-name" content="Faradaem">' in body
    assert '<meta name="twitter:card" content="summary_large_image">' in body
    assert '<meta property="og:image" content="/static/og.png">' in body
    assert '<link rel="icon" href="/static/icon.svg"' in body
    assert '<link rel="apple-touch-icon"' in body


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


def test_the_mark_ships_in_every_size():
    svg = (ROOT / "static/icon.svg").read_text(encoding="ascii")
    assert svg.startswith("<svg")
    assert "<path d=" in svg and 'fill="#FFFFFF"' in svg
    assert png_size(ROOT / "static/icon-32.png") == (32, 32)
    assert png_size(ROOT / "static/apple-touch-icon.png") == (180, 180)
    assert png_size(ROOT / "static/og.png") == (1200, 630)
    same = (ROOT / "static/favicon.svg").read_bytes()
    assert same == (ROOT / "static/icon.svg").read_bytes()


def test_every_asset_is_routed():
    for route in ("/static/icon.svg", "/static/icon-32.png",
                  "/static/apple-touch-icon.png", "/static/og.png",
                  "/favicon.ico", "/favicon.svg"):
        assert route in server.ROUTES, route
    assert server.ROUTES["/favicon.ico"][1] == "image/png"


def test_prose_files_use_plain_ascii_faradaem():
    """Python, JS, and markdown never carry the ligature."""
    for pattern in ("*.py", "spice/*.py", "tests/*.py", "static/*.js",
                    "*.md"):
        for path in ROOT.glob(pattern):
            if path.name.startswith("faradaem-v"):
                continue  # historical briefs stay as written
            text = path.read_text(encoding="utf-8")
            assert "\u00c6" not in text and "\u00e6" not in text, path
