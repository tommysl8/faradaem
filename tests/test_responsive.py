"""Narrow screens, checked in the stylesheet.

The full responsive check is driven through a real browser at 390x844,
768x1024 and 1440x900, and its results are reported with the audit rather
than asserted here: this project's one dependency is pytest, and a
headless browser is not one it is going to grow.

What a stylesheet can be asked, it is asked here. That turns out to be the
part that mattered. The bug this file exists for was pure CSS: at 900px
and below, `.sim-figure { order: -1 }` put the schematic above the
controls, which on a 375px screen pushed the first input to 1726 px and
the sentence explaining what the demo can do to 1493 px. A visitor
scrolled past a drawing to reach both. The hero made exactly the same
mistake at exactly the same breakpoint one release earlier, which is what
makes it worth a test rather than a fix.
"""

import io
import os
import re

import pytest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = io.open(os.path.join(PROJECT, "static", "style.css"),
              encoding="utf-8").read()

#: The narrow breakpoints the stylesheet declares.
NARROW = [block for block in re.findall(
    r"@media \(max-width:\s*\d+px\)\s*\{", CSS)]


def _blocks(condition, css=None):
    """Every rule body inside media queries matching this condition."""
    css = CSS if css is None else css
    out = []
    for match in re.finditer(re.escape(condition) + r"\s*\{", css):
        depth = 0
        start = match.end() - 1
        for index in range(start, len(css)):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if depth == 0:
                    out.append(css[start + 1:index])
                    break
    return out


def _figure_ordered_first(condition, css):
    """The check the test below applies, over any stylesheet."""
    for body in _blocks(condition, css):
        for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
            order = re.search(r"order:\s*(-?\d+)", declarations)
            if order and int(order.group(1)) < 0 and "figure" in selector:
                return selector.strip()
    return None


def test_the_narrow_breakpoints_exist():
    assert NARROW, "no max-width media queries; the page is not responsive"


def test_no_narrow_rule_orders_a_figure_above_the_controls():
    """The reader came for the controls. A drawing above them on a phone
    is a drawing between them and everything the page does."""
    for condition in ("@media (max-width: 900px)", "@media (max-width: 560px)"):
        found = _figure_ordered_first(condition, CSS)
        assert found is None, (condition, found)


def test_that_check_catches_the_rule_it_was_written_for():
    """The exact rule that shipped, so this is a test and not a hope."""
    guilty = """@media (max-width: 900px) {
  .sim-figure {
    position: static;
  }
  .sim-figure {
    order: -1;
  }
}
"""
    assert _figure_ordered_first("@media (max-width: 900px)",
                                 guilty) == ".sim-figure"
    innocent = guilty.replace("order: -1;", "order: 0;")
    assert _figure_ordered_first("@media (max-width: 900px)",
                                 innocent) is None


def test_the_hero_figure_stays_below_the_headline_on_a_phone():
    """It was ordered above once, so a phone opened on a picture of
    silicon with no words saying what the tool was."""
    for body in _blocks("@media (max-width: 900px)"):
        assert "hero-figure" not in body or "order: -1" not in body


def test_the_columns_collapse_to_one():
    found = "\n".join(_blocks("@media (max-width: 900px)"))
    assert "grid-template-columns: 1fr" in found


def test_wide_content_scrolls_inside_itself():
    """A wide drawing or table must scroll in its own box rather than
    pushing the page sideways."""
    for selector in (".figure-scroll", ".sheet-table-wrap"):
        assert selector in CSS, selector
        block = CSS.split(selector + " {")[1].split("}")[0]
        assert "overflow-x: auto" in block or "overflow: auto" in block, selector


def test_grid_children_may_shrink_below_their_content():
    """Without this a wide drawing pushes its whole column off screen
    instead of scrolling: grid items default to min-width:auto."""
    block = CSS.split(".sim > * {")[1].split("}")[0]
    assert "min-width: 0" in block


def test_the_page_never_sets_a_width_wider_than_a_phone():
    """Any fixed width above 360px is a horizontal scrollbar waiting for
    a narrow screen, unless it is inside a scroll container."""
    for match in re.finditer(r"(?<!max-)(?<!min-)width:\s*(\d+)px", CSS):
        assert int(match.group(1)) <= 360, CSS[max(0, match.start() - 90):
                                               match.end()]


def test_images_never_exceed_their_box():
    assert re.search(r"img\s*\{[^}]*max-width:\s*100%", CSS, re.S) \
        or "max-width: 100%" in CSS


def test_the_viewport_is_declared_on_every_page():
    from tools import build_static

    for name in build_static.PAGES + (build_static.NOT_FOUND,):
        page = io.open(os.path.join(PROJECT, name), encoding="utf-8").read()
        assert ('<meta name="viewport" content="width=device-width, '
                'initial-scale=1">') in page, name


@pytest.mark.parametrize("selector", [".modes", ".presets", ".hero-actions",
                                      ".design-actions", ".foot-links"])
def test_button_rows_wrap_rather_than_overflow(selector):
    """Ten circuit tabs on a 375px screen have to go somewhere.

    A wrapping row, a grid, or a column all satisfy this: a column cannot
    overflow horizontally in the first place.
    """
    assert selector in CSS, selector
    block = CSS.split(selector + " {")[1].split("}")[0]
    safe = ("flex-wrap: wrap" in block
            or "display: grid" in block
            or "flex-direction: column" in block)
    assert safe, (selector, block.strip())
