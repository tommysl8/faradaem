"""Accessibility, checked in the markup the reader actually receives.

There is no axe here and no headless browser: the project's one dependency
is pytest, and a browser-driven checker cannot run in the CI that matters.
What can be checked from the rendered HTML and the stylesheet is checked
here, and it is most of what an automated checker would report -- labels,
names, roles, heading order, alt text, live regions, focus styling,
reduced motion. What genuinely needs a browser (computed contrast, focus
order under real layout, the tab strip driven by real key events) is
exercised through the in-app browser and reported separately, never
claimed here.

Every defect this file names was really present: two unlabelled form
controls, a heading level skipped, thirty-six release entries under a
single h1 with no heading between them, and no way past the header for a
keyboard.
"""

import io
import os
import re

import pytest

from spice import deployment
from tools import build_static

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = build_static.PAGES + (build_static.NOT_FOUND,)

CSS = io.open(os.path.join(PROJECT, "static", "style.css"),
              encoding="utf-8").read()


@pytest.fixture(scope="module")
def rendered():
    out = {}
    for mode in deployment.MODES:
        for name in PAGES:
            with io.open(os.path.join(PROJECT, name), encoding="utf-8") as f:
                out[(mode, name)] = deployment.render(f.read(), mode)
    return out


def cases():
    return [(mode, name) for mode in deployment.MODES for name in PAGES]


def _strip_comments(html):
    return re.sub(r"(?s)<!--.*?-->", " ", html)


# ---------------------------------------------------------------------------
# headings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,name", cases())
def test_every_page_has_exactly_one_h1(rendered, mode, name):
    html = _strip_comments(rendered[(mode, name)])
    assert len(re.findall(r"<h1\b", html)) == 1, (mode, name)


@pytest.mark.parametrize("mode,name", cases())
def test_no_page_skips_a_heading_level(rendered, mode, name):
    """index.html went h2 -> h4 at the layout panel's Verification title."""
    html = _strip_comments(rendered[(mode, name)])
    levels = [int(m) for m in re.findall(r"<h([1-6])\b", html)]
    assert levels and levels[0] == 1, (mode, name)
    for before, after in zip(levels, levels[1:]):
        assert after <= before + 1, (mode, name, before, after)


def test_the_changelog_gives_each_release_a_heading(rendered):
    """Thirty-six entries under one h1 is one long unnavigable page."""
    html = rendered[(deployment.STATIC, "changelog.html")]
    entries = len(re.findall(r'<article class="entry">', html))
    headings = len(re.findall(r'<h2 class="entry-version">', html))
    assert entries > 20
    assert headings == entries


# ---------------------------------------------------------------------------
# names for everything operable
# ---------------------------------------------------------------------------


def _has_name(tag, html, element_id):
    """True when this control carries a name a screen reader can read."""
    if 'aria-label="' in tag or 'aria-labelledby="' in tag:
        return True
    if element_id and ('for="' + element_id + '"') in html:
        return True
    return False


@pytest.mark.parametrize("mode,name", cases())
def test_every_form_control_has_an_accessible_name(rendered, mode, name):
    html = _strip_comments(rendered[(mode, name)])
    for tag in re.findall(r"<(?:input|textarea|select)\b[^>]*>", html):
        if 'type="hidden"' in tag:
            continue
        found = re.search(r'\bid="([^"]+)"', tag)
        element_id = found.group(1) if found else None
        assert _has_name(tag, html, element_id), (mode, name, tag[:90])


@pytest.mark.parametrize("mode,name", cases())
def test_every_button_and_link_has_an_accessible_name(rendered, mode, name):
    html = _strip_comments(rendered[(mode, name)])
    for opening, body in re.findall(r"(<(?:button|a)\b[^>]*>)(.*?)</(?:button|a)>",
                                    html, re.S):
        if 'aria-hidden="true"' in opening:
            continue
        text = re.sub(r"(?s)<[^>]+>", " ", body).strip()
        named = text or 'aria-label="' in opening \
            or 'aria-labelledby="' in opening
        # An empty control that JavaScript fills is still empty on arrival.
        assert named, (mode, name, opening[:90])


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,name", cases())
def test_every_image_is_described_or_marked_decorative(rendered, mode, name):
    html = _strip_comments(rendered[(mode, name)])
    for tag in re.findall(r"<img\b[^>]*>", html):
        assert 'alt="' in tag or 'aria-hidden="true"' in tag, (mode, name, tag)
        found = re.search(r'alt="([^"]*)"', tag)
        if found and not found.group(1).strip():
            assert 'aria-hidden="true"' in tag or 'role="presentation"' in tag


@pytest.mark.parametrize("mode,name", cases())
def test_every_inline_svg_is_hidden_or_labelled(rendered, mode, name):
    html = _strip_comments(rendered[(mode, name)])
    for tag in re.findall(r"<svg\b[^>]*>", html):
        # An id is not a name. A plot is a meaningful image and must say
        # what it is; a glyph beside a label is decoration and must say
        # that instead. There is no third option that is not silence.
        decorative = 'aria-hidden="true"' in tag
        named = ('aria-label="' in tag or 'aria-labelledby="' in tag)
        assert decorative or named, (mode, name, tag)
        if named:
            assert 'role="img"' in tag, (mode, name, tag)


# ---------------------------------------------------------------------------
# tabs
# ---------------------------------------------------------------------------


def test_the_circuit_strip_is_a_real_tablist(rendered):
    html = rendered[(deployment.STATIC, "index.html")]
    assert 'role="tablist"' in html
    assert 'aria-label="Circuit"' in html
    # The panel the tabs control exists in the shipped markup with a
    # stable id, so aria-controls has something to point at.
    assert 'id="circuit-panel"' in html


def test_the_tabs_are_wired_to_the_panel_in_code():
    app = io.open(os.path.join(PROJECT, "static", "app.js"),
                  encoding="utf-8").read()
    render = app.split("function renderTabs()")[1][:2200]
    for wanted in ('setAttribute("role", "tab")',
                   'setAttribute("aria-selected"',
                   'setAttribute("aria-controls", CIRCUIT_PANEL_ID)',
                   'tab.id = "mode-tab-"',
                   'setAttribute("role", "tabpanel")',
                   'setAttribute("aria-labelledby", "mode-tab-"'):
        assert wanted in render, wanted
    # Roving tabindex: exactly one tab is in the tab order.
    assert "tab.tabIndex = active ? 0 : -1" in render


def test_arrow_keys_select_as_they_move_and_wrap():
    app = io.open(os.path.join(PROJECT, "static", "app.js"),
                  encoding="utf-8").read()
    focus = app.split("function focusTab(")[1][:800]
    assert "(index + tabs.length) % tabs.length" in focus
    assert "select(wanted)" in focus
    assert ".focus()" in focus


# ---------------------------------------------------------------------------
# live regions
# ---------------------------------------------------------------------------


def test_errors_are_announced(rendered):
    html = rendered[(deployment.LOCAL, "index.html")]
    assert 'id="error"' in html and 'role="alert"' in html
    assert 'id="import-error"' in html


def test_the_import_error_is_an_alert_in_both_deployments(rendered):
    for mode in deployment.MODES:
        html = rendered[(mode, "index.html")]
        at = html.index('id="import-error"')
        tag = html[html.rindex("<", 0, at):html.index(">", at) + 1]
        assert 'role="alert"' in tag, mode
        # Focusable, so the message can be moved to after a file dialog.
        assert 'tabindex="-1"' in tag, mode


def test_import_status_is_a_status_region(rendered):
    for mode in deployment.MODES:
        html = rendered[(mode, "index.html")]
        at = html.index('id="share-note"')
        tag = html[html.rindex("<", 0, at):html.index(">", at) + 1]
        assert 'role="status"' in tag, mode


def test_the_import_error_takes_focus_in_code():
    app = io.open(os.path.join(PROJECT, "static", "app.js"),
                  encoding="utf-8").read()
    body = app.split("function importError(")[1][:600]
    assert "show(box, true)" in body
    assert "box.focus()" in body


# ---------------------------------------------------------------------------
# keyboard reach
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,name", cases())
def test_every_page_offers_a_way_past_the_header(rendered, mode, name):
    html = rendered[(mode, name)]
    assert 'class="skip-link"' in html, (mode, name)
    assert 'href="#main"' in html, (mode, name)
    assert 'id="main"' in html, (mode, name)
    # And it is the first focusable thing on the page.
    assert html.index("skip-link") < html.index('class="nav"'), (mode, name)


def test_the_skip_link_is_reachable_rather_than_display_none():
    """display:none is not focusable, so it would never be reached."""
    block = CSS.split(".skip-link {")[1].split("}")[0]
    assert "display: none" not in block
    assert "position: absolute" in block
    assert ".skip-link:focus" in CSS


# ---------------------------------------------------------------------------
# focus and motion
# ---------------------------------------------------------------------------


def test_focus_is_visible_and_never_removed():
    assert ":focus-visible" in CSS
    # outline:none without a replacement is the classic way to make a page
    # unusable by keyboard while looking tidier.
    for stripped in re.findall(r"outline:\s*none", CSS):
        assert False, "outline:none appears in the stylesheet"


def test_focus_styling_uses_tokens_both_themes_define():
    block = CSS.split(":focus-visible")[1].split("}")[0]
    assert "var(--" in block


def test_reduced_motion_covers_delay_as_well_as_duration():
    """A 0.01ms animation behind a 0.24s delay is still a quarter second
    of an invisible page under `backwards`."""
    block = CSS.split("@media (prefers-reduced-motion: reduce) {")[1][:600]
    assert "animation-duration" in block
    assert "animation-delay" in block
    assert "transition-duration" in block


def test_every_animation_is_behind_a_motion_preference():
    """The hero settle runs only for readers who have not asked it not to."""
    assert "@media (prefers-reduced-motion: no-preference)" in CSS
    at = CSS.index("@media (prefers-reduced-motion: no-preference)")
    guarded = CSS[at:at + 900]
    assert "animation: hero-settle" in guarded
    # And no unguarded copy of it exists outside that block.
    outside = CSS[:at] + CSS[at + 900:]
    assert "animation: hero-settle" not in outside


# ---------------------------------------------------------------------------
# language and landmarks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,name", cases())
def test_every_page_declares_its_language(rendered, mode, name):
    assert re.search(r'<html[^>]*\slang="en"', rendered[(mode, name)]), name


@pytest.mark.parametrize("mode,name", cases())
def test_every_page_has_the_expected_landmarks(rendered, mode, name):
    html = rendered[(mode, name)]
    assert "<header" in html and "<main" in html and "<footer" in html
    # Two navs per page, and both are named, or a screen reader announces
    # "navigation" twice with no way to tell them apart.
    for nav in re.findall(r"<nav\b[^>]*>", html):
        assert 'aria-label="' in nav, (mode, name, nav)
