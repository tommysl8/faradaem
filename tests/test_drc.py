"""The rule checker, and the proof that it is capable of saying no.

A checker that never fails is worse than no checker, because it turns
unchecked geometry into geometry someone believes. So every rule here is
tested twice: once against a shape that satisfies it, and once against a
shape built specifically to break it.

What is checked is eight rules. What is not checked is the rest of the deck,
which needs Magic or KLayout. The last test makes sure the result says so
in its own words.
"""

import pytest

from spice import circuits, drc, layout

LAYERS = {"DIFF": (65, 20), "POLY": (66, 20), "NWELL": (64, 20)}

TECH = {
    "poly_width": 0.15,
    "diff_width": 0.15,
    "diff_spacing": 0.27,
    "diff_overhang": 0.25,
    "poly_endcap": 0.13,
    "nwell_width": 0.84,
    "nwell_spacing": 1.27,
    "nwell_surround": 0.18,
}

LAYERS_WITH_WELL = {"DIFF": (65, 20), "POLY": (66, 20), "NWELL": (64, 20)}


def well(x1, y1, x2, y2):
    return (64, 20, x1, y1, x2, y2)


def device(x=0.0, width=1.0, height=10.0, gate=0.5, endcap=0.13, overhang=None):
    """A transistor as two rectangles, with every dimension adjustable so a
    test can break exactly one of them."""
    diff = (65, 20, x, 0.0, x + width, height)
    left = x + (overhang if overhang is not None else (width - gate) / 2.0)
    poly = (66, 20, left, -endcap, left + gate, height + endcap)
    return [diff, poly]


def tags(result):
    return sorted({item["tag"] for item in result["violations"]})


# ---------------------------------------------------------------------------
# a device that is drawn correctly
# ---------------------------------------------------------------------------


def test_a_correct_device_passes():
    result = drc.check(device(), LAYERS, TECH)
    assert result["clean"]
    assert result["violations"] == []
    assert result["shapes_checked"] == 2


def test_two_correctly_spaced_devices_pass():
    shapes = device(x=0.0) + device(x=1.0 + 0.27)
    assert drc.check(shapes, LAYERS, TECH)["clean"]


def test_exactly_the_minimum_spacing_is_allowed():
    """A rule is a minimum, so meeting it exactly is legal. Float noise in a
    placement done in microns must not turn that into a violation."""
    shapes = device(x=0.0) + device(x=1.0 + TECH["diff_spacing"])
    assert drc.check(shapes, LAYERS, TECH)["clean"]


# ---------------------------------------------------------------------------
# and the ways it can be drawn wrong
# ---------------------------------------------------------------------------


def test_a_gate_thinner_than_the_minimum_is_caught():
    result = drc.check(device(gate=0.10), LAYERS, TECH)
    assert not result["clean"]
    assert "poly.1a" in tags(result)


def test_a_diffusion_thinner_than_the_minimum_is_caught():
    result = drc.check(device(height=0.10), LAYERS, TECH)
    assert not result["clean"]
    assert "diff/tap.1" in tags(result)


def test_devices_drawn_too_close_are_caught():
    shapes = device(x=0.0) + device(x=1.10)          # a 0.10 gap
    result = drc.check(shapes, LAYERS, TECH)
    assert "diff/tap.3" in tags(result)
    broken = [v for v in result["violations"] if v["tag"] == "diff/tap.3"][0]
    assert broken["measured_um"] == pytest.approx(0.10)
    assert broken["required_um"] == pytest.approx(0.27)


def test_a_gate_that_stops_inside_the_diffusion_is_caught():
    """Poly that ends on the diffusion leaves the channel shorted around it."""
    result = drc.check(device(endcap=-0.05), LAYERS, TECH)
    assert "poly.8" in tags(result)


def test_too_little_diffusion_for_a_source_is_caught():
    result = drc.check(device(width=0.6, gate=0.5, overhang=0.05),
                       LAYERS, TECH)
    assert "poly.7" in tags(result)


def test_a_violation_says_what_it_measured_and_what_was_needed():
    """A number without its limit is not a finding anyone can act on."""
    broken = drc.check(device(gate=0.10), LAYERS, TECH)["violations"][0]
    assert broken["measured_um"] < broken["required_um"]
    assert broken["tag"]
    assert broken["what"]
    assert broken["where"]


# ---------------------------------------------------------------------------
# what it does not check
# ---------------------------------------------------------------------------


def test_overlapping_shapes_are_not_a_spacing_violation():
    """Two touching diffusions are one piece of geometry, not two too close."""
    shapes = [(65, 20, 0.0, 0.0, 1.0, 10.0), (65, 20, 0.5, 0.0, 1.5, 10.0)]
    assert drc.check(shapes, LAYERS, TECH)["clean"]


def test_the_result_states_its_own_coverage():
    result = drc.check(device(), LAYERS, TECH)
    assert len(result["rules_checked"]) == 8
    assert {item["tag"] for item in result["rules_checked"]} == {
        "poly.1a", "diff/tap.1", "diff/tap.3", "poly.7", "poly.8",
        "nwell.1", "nwell.2a", "nwell.5"
    }
    coverage = result["coverage"].lower()
    assert "not the sign-off deck" in coverage
    assert "magic" in coverage or "klayout" in coverage


def test_the_module_never_calls_itself_sign_off():
    source = open(drc.__file__, encoding="utf-8").read().lower()
    assert "this is not sign-off" in source
    for boast in ("drc clean", "fully checked", "passes drc"):
        assert boast not in source, boast


# ---------------------------------------------------------------------------
# live: the geometry Faradaem actually emits
# ---------------------------------------------------------------------------


requires_pdk = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed for the real rule values",
)


@requires_pdk
@pytest.mark.parametrize("circuit_id", ["opamp_two_stage", "ota_5t"])
def test_the_emitted_geometry_satisfies_the_rules_it_was_drawn_for(circuit_id):
    """The floorplan is built from these rules, so it had better meet them.
    If this ever fails, the generator and the checker have drifted apart."""
    tech = layout.tech_constants()
    layers = layout.gds_layers()
    devices = (circuits.opamp_devices if circuit_id == "opamp_two_stage"
               else circuits.ota_devices)(circuits.defaults(circuit_id))
    plan = layout.floorplan(devices, tech)
    shapes = layout.floorplan_shapes(plan, layers, tech)

    result = drc.check(shapes, layers, tech)
    assert result["clean"], result["violations"][:3]


@requires_pdk
def test_a_minimum_length_device_still_passes():
    """The tightest legal sizing the form allows is the one most likely to
    break a rule."""
    tech = layout.tech_constants()
    layers = layout.gds_layers()
    plan = layout.floorplan([("M1", 0.42e-6, 0.15e-6)], tech)
    shapes = layout.floorplan_shapes(plan, layers, tech)
    assert drc.check(shapes, layers, tech)["clean"]


# ---------------------------------------------------------------------------
# the well the p-channel devices sit in
# ---------------------------------------------------------------------------


def test_a_pmos_in_a_proper_well_passes():
    diff = (65, 20, 1.0, 1.0, 2.0, 11.0)
    shapes = [diff, (66, 20, 1.25, 0.87, 1.75, 11.13),
              well(0.82, 0.82, 2.18, 11.18)]
    result = drc.check(shapes, LAYERS_WITH_WELL, TECH,
                       pmos=[(1.0, 1.0, 2.0, 11.0)])
    assert result["clean"], result["violations"]


def test_a_pmos_outside_any_well_is_caught():
    """A p-channel device drawn in the substrate is not a device."""
    shapes = device(x=1.0)
    result = drc.check(shapes, LAYERS_WITH_WELL, TECH,
                       pmos=[(1.0, 0.0, 2.0, 10.0)])
    assert "nwell.5" in tags(result)


def test_a_well_that_does_not_reach_far_enough_is_caught():
    diff = (65, 20, 1.0, 1.0, 2.0, 11.0)
    shapes = [diff, well(0.95, 0.82, 2.18, 11.18)]     # only 0.05 on the left
    result = drc.check(shapes, LAYERS_WITH_WELL, TECH,
                       pmos=[(1.0, 1.0, 2.0, 11.0)])
    broken = [v for v in result["violations"] if v["tag"] == "nwell.5"]
    assert broken
    assert broken[0]["measured_um"] == pytest.approx(0.05)


def test_a_well_narrower_than_the_minimum_is_caught():
    shapes = [well(0.0, 0.0, 0.5, 10.0)]               # 0.5 against 0.84
    assert "nwell.1" in tags(drc.check(shapes, LAYERS_WITH_WELL, TECH))


def test_two_wells_too_close_are_caught():
    shapes = [well(0.0, 0.0, 1.0, 10.0), well(1.5, 0.0, 2.5, 10.0)]
    result = drc.check(shapes, LAYERS_WITH_WELL, TECH)
    assert "nwell.2a" in tags(result)


def test_one_merged_well_is_not_two_wells_too_close():
    """Overlapping wells are the same well, which is why the PMOS are
    placed together in the first place."""
    shapes = [well(0.0, 0.0, 1.0, 10.0), well(0.9, 0.0, 2.0, 10.0)]
    result = drc.check(shapes, LAYERS_WITH_WELL, TECH)
    assert "nwell.2a" not in tags(result)
