"""The foundry's own deck, and what it is for.

These tests exist to hold one line: the thirty-six rules Faradaem checks
while drawing are a fast loop, and the SKY130 runset is the answer. The
most important test here is the one that runs both and requires them to
agree, because the moment they stop agreeing the fast loop has become a
thing that reassures without checking.

Everything that needs KLayout skips cleanly without it, the same as the
PDK-dependent tests do.
"""

import pytest

from spice import circuits, drc, layout, signoff

requires_klayout = pytest.mark.skipif(
    not signoff.available(),
    reason="KLayout and the SKY130 runset are needed to run the real deck",
)

requires_tech = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed for the real dimensions",
)

LAID_OUT = [item["id"] for item in circuits.catalog()
            if circuits.has_floorplan(item["id"])]


def drawn(circuit_id):
    """Build a circuit's geometry the way the tool does.

    That means the matched ordering -- fingers, dummies, guard rings --
    and the drawn passives, because that is the file that ships. Testing
    any other construction checks a layout nobody emits.
    """
    tech = layout.tech_constants()
    layers = layout.gds_layers()
    params = circuits.defaults(circuit_id)
    block = circuits.get_circuit(circuit_id)["floorplan"]
    ordered, _ = layout.matched_layout(block["devices"](params),
                                       block.get("matched"))
    plan = layout.floorplan(
        ordered, tech,
        passives=circuits.drawable_passives(circuit_id, params))
    routed = layout.route(plan, circuits.circuit_nets(circuit_id, params),
                          tech)
    shapes = (layout.floorplan_shapes(plan, layers, tech)
              + layout.routing_shapes(routed, layers))
    return plan, shapes, layers, tech


# ---------------------------------------------------------------------------
# it refuses rather than pretending
# ---------------------------------------------------------------------------


def test_a_missing_tool_is_an_error_and_never_a_pass():
    """The one failure mode that would matter: reporting clean for a check
    that did not run."""
    source = open(signoff.__file__, encoding="utf-8").read()
    assert "class KlayoutNotFoundError" in source
    # Nothing may construct a clean result without a report behind it.
    assert '"clean": total == 0' in source
    assert source.count('"clean":') == 1


def test_it_says_which_deck_and_which_sections_it_ran():
    """A clean result means nothing without knowing what was looked at."""
    source = open(signoff.__file__, encoding="utf-8").read()
    for key in ('"deck"', '"sections"', '"tool"', '"coverage"'):
        assert key in source, key


def test_the_module_reimplements_no_check():
    """The whole point is to stop writing EDA. If a rule value appears in
    this module, something is being checked here that should not be."""
    source = open(signoff.__file__, encoding="utf-8").read()
    for smell in ("0.15", "0.17", "micron", "def check_"):
        assert smell not in source, smell


def test_the_deck_lives_in_the_pdk_not_in_the_repo():
    """A runset copied into the project is a runset that drifts from the
    models it is supposed to match."""
    deck = signoff.drc_deck()
    if deck is None:
        pytest.skip("the PDK is not installed")
    assert "pdk" in deck.lower()
    assert "faradaem" not in deck.lower()


# ---------------------------------------------------------------------------
# and when it is there, it runs
# ---------------------------------------------------------------------------


@requires_klayout
@requires_tech
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_the_emitted_geometry_passes_the_foundrys_own_deck(circuit_id):
    """Sixteen hundred lines of rules, written by the foundry, run by a
    tool with no stake in the answer."""
    _, shapes, _, _ = drawn(circuit_id)
    result = signoff.run_drc(shapes, circuit_id)
    assert result["clean"], result["violations"]
    assert result["shapes_checked"] == len(shapes)


@requires_klayout
@requires_tech
def test_the_fast_checker_and_the_real_deck_agree():
    """The fast loop earns its place only while it agrees with the deck.

    It did not, once: it passed geometry the runset failed on forty
    counts, because it had implemented the all-round half of three
    directional rules and skipped the rest. That is why this test exists.
    """
    plan, shapes, layers, tech = drawn("opamp_two_stage")
    pmos = [(item["x"], item["y"], item["x"] + item["width"],
             item["y"] + item["height"])
            for item in plan["devices"] if item["kind"] == "pfet"]

    fast = drc.check(shapes, layers, tech, pmos=pmos)
    real = signoff.run_drc(shapes, "opamp_two_stage")
    assert fast["clean"] == real["clean"], {
        "fast": [v["tag"] for v in fast["violations"]][:5],
        "real": real["violations"],
    }


@requires_klayout
@requires_tech
def test_the_deck_actually_says_no_to_geometry_that_is_wrong():
    """A deck that passes everything proves nothing. Break one rule on
    purpose and require the runset to catch it."""
    _, shapes, layers, _ = drawn("ota_5t")
    metal2 = layers["MET2"]

    # Shrink every metal2 track to a third of its width. That breaks the
    # minimum width and the enclosure of every via it carries.
    broken = []
    for shape in shapes:
        if shape[:2] == metal2:
            height = (shape[5] - shape[3]) / 3.0
            broken.append((shape[0], shape[1], shape[2], shape[3],
                           shape[4], shape[3] + height))
        else:
            broken.append(shape)

    result = signoff.run_drc(broken, "ota_5t")
    assert not result["clean"]
    assert result["total"] > 0


# ---------------------------------------------------------------------------
# the fast checker knows the rules the deck taught it
# ---------------------------------------------------------------------------


@requires_tech
def test_the_directional_rules_are_checked_now():
    result = drc.check([], layout.gds_layers(), layout.tech_constants())
    tags = {item["tag"] for item in result["rules_checked"]}
    for rule in ("met1.5", "via.5a", "met2.5"):
        assert rule in tags, rule


@requires_tech
def test_a_via_with_only_all_round_enclosure_is_caught():
    """The exact shape that passed the fast rules and failed the deck:
    metal that clears the via on every side by the all-round rule, and by
    no more than that on either axis."""
    tech = layout.tech_constants()
    layers = layout.gds_layers()

    cut = tech["via1_width"]
    room = tech["via1_surround"]
    via = (layers["VIA1"][0], layers["VIA1"][1], 1.0, 1.0, 1.0 + cut, 1.0 + cut)
    square = (1.0 - room, 1.0 - room, 1.0 + cut + room, 1.0 + cut + room)
    metals = [
        (layers["MET1"][0], layers["MET1"][1]) + square,
        (layers["MET2"][0], layers["MET2"][1]) + square,
    ]

    found = drc.check([via] + metals, layers, tech)
    tags = {item["tag"] for item in found["violations"]}
    assert "via.5a" in tags or "met2.5" in tags, found["violations"]


def test_the_fast_checker_no_longer_calls_itself_the_answer():
    source = open(drc.__file__, encoding="utf-8").read().lower()
    assert "fast loop" in source
    assert "run the real deck before believing it" in source
