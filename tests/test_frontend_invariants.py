"""Rules app.js has to keep, now that it runs in two deployments.

The static build deletes every server-only control rather than hiding it,
which is the point: a hidden control is still focusable, still announced,
still a lie. The cost is that app.js now runs against a document where a
lot of elements legitimately do not exist, and the failure mode is a
TypeError halfway through a function that leaves the page half updated.

That is not hypothetical. Driving the published build by keyboard found
select() writing `id("blame-label").textContent` for any circuit with a
design block: the element is local-only, so on the static site pressing
End on the circuit tabs threw, the URL hash stopped updating, focus was
lost, and every subsequent arrow key did nothing. One null write, and the
tab strip was dead.

So the invariants below are enforced by reading the file. Each one is a
shape that cannot fail that way, rather than a promise to remember.
"""

import io
import os
import re

import pytest

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = io.open(os.path.join(PROJECT, "static", "app.js"),
              encoding="utf-8").read()


# ---------------------------------------------------------------------------
# nothing dereferences an element that may not be here
# ---------------------------------------------------------------------------


def test_no_text_is_written_through_a_bare_id_lookup():
    """`id("x").textContent = y` throws when x belongs to the other
    deployment. text(id("x"), y) does nothing, which is correct."""
    found = re.findall(r'id\("[a-z0-9-]+"\)\.textContent\s*=', APP)
    assert found == [], found


def test_no_handler_is_bound_through_a_bare_id_lookup():
    found = re.findall(r'id\("[a-z0-9-]+"\)\.addEventListener\(', APP)
    assert found == [], found


def test_the_null_safe_helpers_are_actually_null_safe():
    for name in ("function show(", "function clear(", "function text(",
                 "function on("):
        body = APP.split(name)[1][:340]
        assert "if (" in body and ("!node" in body or "!element" in body
                                   or "element)" in body or "node)" in body), name


def test_show_tolerates_a_missing_element():
    body = APP.split("function show(element, visible)")[1][:200]
    assert "if (element)" in body


def test_text_tolerates_a_missing_element():
    body = APP.split("function text(element, value)")[1][:200]
    assert "if (element)" in body


# ---------------------------------------------------------------------------
# the static deployment asks no server anything
# ---------------------------------------------------------------------------


def test_every_server_request_goes_through_one_door():
    """Sixteen call sites with sixteen guards is sixteen chances to add a
    seventeenth without one."""
    direct = re.findall(r'fetch\("(/api/[^"]*)"', APP)
    assert direct == [], direct


def test_that_door_refuses_in_the_static_deployment():
    body = APP.split("function api(path, options)")[1][:520]
    assert "if (isStatic)" in body
    assert "Promise.reject" in body
    # And the refusal comes before any fetch.
    assert body.index("if (isStatic)") < body.index("return fetch(")


def test_the_only_plain_fetch_is_the_published_catalogue():
    plain = set(re.findall(r'fetch\("([^"]+)"', APP))
    assert plain == {"catalogue.json"}, plain


def test_the_deployment_is_read_from_the_document_not_requested():
    """It used to be inferred from a 404, which is what produced both the
    doomed request and the flash."""
    assert 'document.documentElement.getAttribute("data-deployment")' in APP
    assert 'var isStatic = MODE === "static"' in APP
    # The mode must be known before anything renders: it is read above the
    # first element lookup rather than inside start().
    assert APP.index('getAttribute("data-deployment")') < APP.index(
        "async function start()")


def test_nothing_sets_the_mode_at_runtime():
    """A mode that can change after load is a mode that can flash."""
    assignments = re.findall(r"isStatic\s*=", APP)
    assert len(assignments) == 1, assignments


# ---------------------------------------------------------------------------
# import and persistence are wired to the shared modules
# ---------------------------------------------------------------------------


def _code_only(text):
    """Drop comments, so prose about a document is not read as touching one."""
    text = re.sub(r"(?s)/\*.*?\*/", " ", text)
    return re.sub(r"(?m)^\s*//.*$", " ", text)


def test_the_validator_is_the_shared_pure_module():
    assert "window.FaradaemImport" in APP
    validator = _code_only(
        io.open(os.path.join(PROJECT, "static", "import-validate.js"),
                encoding="utf-8").read())
    # Pure: no DOM, no network, nothing that needs a browser. This is what
    # lets the whole rejection table be exercised by a node harness.
    for forbidden in ("document.", "window.location", "fetch(",
                      "localStorage", "getComputedStyle"):
        assert forbidden not in validator, forbidden


def test_the_store_is_namespaced_and_versioned():
    assert 'var STORE_KEY = "faradaem.designs.v1"' in APP
    body = APP.split("function readStore()")[1][:900]
    # Every read is wrapped: a private window, blocked site data or a full
    # quota must mean "no memory", never a broken page.
    assert "try {" in body and "catch" in body
    assert "parsed.version !== 1" in body


def test_only_a_complete_in_range_sizing_is_remembered():
    body = APP.split("function remember()")[1][:900]
    assert "isFinite(value)" in body
    assert "value < spec.min" in body and "value > spec.max" in body
    assert "if (!ok)" in body


def test_reset_forgets_after_the_redraw_not_before():
    """onEdit persists; forgetting first would be undone immediately."""
    body = APP.split('on("design-reset", "click"')[1][:600]
    assert body.index("onEdit()") < body.index("forget(current.id)")


@pytest.mark.parametrize("hook", ["onEdit", "renderPresets",
                                  "applyImportedDesign"])
def test_every_way_values_change_updates_what_is_remembered(hook):
    body = APP.split("function " + hook + "(")[1][:2000]
    assert "remember()" in body, hook
