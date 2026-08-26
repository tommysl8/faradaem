"""The rule checker, and the proof that it is capable of saying no.

A checker that never fails is worse than no checker, because it turns
unchecked geometry into geometry someone believes. So every rule here is
tested twice: once against a shape that satisfies it, and once against a
shape built specifically to break it.

What is checked is ten rules. What is not checked is the rest of the deck,
which needs Magic or KLayout. The last test makes sure the result says so
in its own words.
"""

import pytest

from spice import circuits, drc, layout

LAYERS = {"DIFF": (65, 20), "POLY": (66, 20), "NWELL": (64, 20),
          "CONT": (66, 44), "LI": (67, 20)}

TECH = {
    "poly_width": 0.15,
    "diff_width": 0.15,
    "diff_spacing": 0.27,
    "diff_overhang": 0.25,
    "poly_endcap": 0.13,
    "nwell_width": 0.84,
    "nwell_spacing": 1.27,
    "nwell_surround": 0.18,
    "contact_width": 0.17,
    "contact_spacing": 0.17,
    "contact_surround": 0.04,
    "contact_to_gate": 0.055,
    "li_width": 0.17,
    "li_spacing": 0.17,
    "li_surround": 0.08,
    "poly_contact_surround": 0.05,
    "poly_contact_to_diff": 0.19,
    "via_width": 0.17,
    "via_spacing": 0.19,
    "metal1_via_surround": 0.03,
    "metal2_width": 0.14,
    "metal2_spacing": 0.14,
    "via1_width": 0.15,
    "via1_spacing": 0.17,
    "via1_surround": 0.055,
}

LAYERS_WITH_WELL = dict(LAYERS)


def contact(x1, y1, size=0.17):
    return (66, 44, x1, y1, x1 + size, y1 + size)


def li(x1, y1, x2, y2):
    return (67, 20, x1, y1, x2, y2)


def contacted(x=0.0, width=1.03, height=10.0, gate=0.5, at=None, reach=0.08):
    """A device with one contact in its source, and the li over it.

    Defaults are drawn legally, so a test can move exactly one thing and
    watch the rule that thing breaks.
    """
    shapes = device(x=x, width=width, height=height, gate=gate)
    left = x + 0.04 if at is None else at
    low = height / 2.0 - 0.085
    shapes.append(contact(left, low))
    shapes.append(li(left, low - reach, left + 0.17, low + 0.17 + reach))
    return shapes


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
# the contacts, and the local interconnect they land on
# ---------------------------------------------------------------------------


def test_a_properly_contacted_device_passes():
    """The whole stack drawn legally. If this fails, every test below is
    measuring the wrong thing."""
    result = drc.check(contacted(), LAYERS, TECH)
    assert result["clean"], result["violations"]


def test_a_contact_narrower_than_the_minimum_is_caught():
    shapes = device(width=1.03) + [contact(0.04, 5.0, size=0.10),
                                   li(0.04, 4.92, 0.21, 5.18)]
    assert "licon.1" in tags(drc.check(shapes, LAYERS, TECH))


def test_two_contacts_too_close_together_are_caught():
    shapes = contacted()
    shapes.append(contact(0.04, 5.0))
    shapes.append(contact(0.04, 5.22))          # a 0.05 gap above it
    shapes.append(li(0.04, 4.92, 0.21, 5.47))
    assert "licon.2" in tags(drc.check(shapes, LAYERS, TECH))


def test_a_contact_the_diffusion_does_not_surround_is_caught():
    """A contact at the very edge of its diffusion reaches past the silicon
    it was meant to touch."""
    result = drc.check(contacted(at=0.01), LAYERS, TECH)
    broken = [v for v in result["violations"] if v["tag"] == "licon.5a"]
    assert broken
    assert broken[0]["measured_um"] == pytest.approx(0.01)


def test_a_contact_outside_every_diffusion_is_caught():
    shapes = [contact(50.0, 50.0), li(50.0, 49.92, 50.17, 50.25)]
    assert "licon.5a" in tags(drc.check(shapes, LAYERS, TECH))


def test_a_contact_too_close_to_the_gate_is_caught():
    """Closer than licon.11 and the contact shorts the channel to the
    source it was supposed to connect."""
    # The gate of a 1.03-wide cell with a 0.5 gate starts at 0.265.
    result = drc.check(contacted(at=0.23), LAYERS, TECH)
    assert "licon.11" in tags(result)


def test_a_contact_that_lands_on_the_gate_is_caught():
    """A contact that sits half on the gate and half off it is enclosed by
    neither the diffusion nor the poly, so it reaches nothing."""
    result = drc.check(contacted(at=0.30), LAYERS, TECH)
    assert not result["clean"]
    assert {"licon.5a", "licon.11", "licon.8"} & set(tags(result))


def test_a_contact_properly_on_poly_is_judged_by_the_poly_rules():
    """A gate contact is a poly contact. Judging it by licon.5a, which is
    about diffusion, reports a correct gate as a broken source."""
    poly = (66, 20, 5.0, 5.0, 6.0, 6.0)
    shapes = [poly, contact(5.4, 5.4), li(5.4, 5.32, 5.57, 5.65)]
    result = drc.check(shapes, LAYERS, TECH)
    assert result["clean"], result["violations"]


def test_a_poly_contact_the_poly_does_not_surround_is_caught():
    poly = (66, 20, 5.0, 5.0, 6.0, 6.0)
    # Inside the poly, but only 0.02 in from its left edge.
    shapes = [poly, contact(5.02, 5.4), li(5.02, 5.32, 5.19, 5.65)]
    assert "licon.8" in tags(drc.check(shapes, LAYERS, TECH))


def test_a_poly_contact_too_close_to_a_diffusion_is_caught():
    """licon.14. A gate contact has to clear every diffusion, not just its
    own device's."""
    poly = (66, 20, 5.0, 5.0, 6.0, 6.0)
    diff = (65, 20, 5.0, 4.0, 6.0, 4.95)          # 0.05 below the contact
    shapes = [poly, diff, contact(5.4, 5.05), li(5.4, 4.97, 5.57, 5.30)]
    assert "licon.14" in tags(drc.check(shapes, LAYERS, TECH))


def test_local_interconnect_narrower_than_the_minimum_is_caught():
    shapes = device(width=1.03) + [li(0.04, 4.9, 0.14, 5.3)]
    assert "li.1" in tags(drc.check(shapes, LAYERS, TECH))


def test_two_pieces_of_local_interconnect_too_close_are_caught():
    shapes = device(width=1.03) + [li(0.04, 4.9, 0.21, 5.3),
                                   li(0.04, 5.35, 0.21, 5.8)]
    assert "li.3" in tags(drc.check(shapes, LAYERS, TECH))


def test_a_contact_with_no_local_interconnect_on_it_is_caught():
    shapes = device(width=1.03) + [contact(0.04, 5.0)]
    result = drc.check(shapes, LAYERS, TECH)
    broken = [v for v in result["violations"] if v["tag"] == "li.5"]
    assert broken
    assert broken[0]["measured_um"] == 0.0


def test_local_interconnect_that_barely_covers_its_contact_is_caught():
    """li.5 asks for the overlap in one direction. None in either is a
    violation even though the contact is covered."""
    result = drc.check(contacted(reach=0.02), LAYERS, TECH)
    assert "li.5" in tags(result)


def test_the_overlap_is_owed_in_one_direction_only():
    """The rule is directional, and the geometry depends on that: taking
    the overlap on all four sides would push a source and a drain together
    until they broke li.3 at minimum gate length."""
    assert drc.check(contacted(reach=0.08), LAYERS, TECH)["clean"]


# ---------------------------------------------------------------------------
# what it does not check
# ---------------------------------------------------------------------------


def test_overlapping_shapes_are_not_a_spacing_violation():
    """Two touching diffusions are one piece of geometry, not two too close."""
    shapes = [(65, 20, 0.0, 0.0, 1.0, 10.0), (65, 20, 0.5, 0.0, 1.5, 10.0)]
    assert drc.check(shapes, LAYERS, TECH)["clean"]


def test_the_result_states_its_own_coverage():
    result = drc.check(device(), LAYERS, TECH)
    assert len(result["rules_checked"]) == 36
    assert {item["tag"] for item in result["rules_checked"]} == {
        "poly.1a", "diff/tap.1", "diff/tap.3", "poly.7", "poly.8",
        "nwell.1", "nwell.2a", "nwell.5", "met1.1", "met1.2",
        "licon.1", "licon.2", "licon.5a", "licon.8", "licon.11", "licon.14",
        "li.1", "li.3", "li.5",
        "mcon.1", "mcon.2", "met1.4", "met2.1", "met2.2",
        "via.1a", "via.2", "via.4a",
        "diff/tap.9", "diff/tap.10", "diff/tap.11", "nwell.4", "licon.16",
        # The three the foundry's own deck caught and this had not.
        "met1.5", "via.5a", "met2.5",
        # Poly spacing, which began to matter when a resistor was drawn.
        "poly.2",
    }
    coverage = result["coverage"].lower()
    # It is the fast loop and has to say so: the answer is the runset.
    assert "fast loop" in coverage
    assert "sign-off deck" in coverage
    assert "klayout" in coverage


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


#: Every circuit with a floorplan, from the registry, so a topology added
#: there is checked here without this file being touched.
LAID_OUT = [item["id"] for item in circuits.catalog()
            if circuits.has_floorplan(item["id"])]


@requires_pdk
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_the_emitted_geometry_satisfies_the_rules_it_was_drawn_for(circuit_id):
    """The layout is built from these rules, so it had better meet them.
    If this ever fails, the generator and the checker have drifted apart."""
    tech = layout.tech_constants()
    layers = layout.gds_layers()
    params = circuits.defaults(circuit_id)
    devices = circuits.get_circuit(circuit_id)["floorplan"]["devices"](params)
    plan = layout.floorplan(devices, tech)
    shapes = (layout.floorplan_shapes(plan, layers, tech)
              + layout.routing_shapes(
                  layout.route(plan, circuits.circuit_nets(circuit_id, params),
                               tech), layers))
    pmos = [(item["x"], item["y"], item["x"] + item["width"],
             item["y"] + item["height"])
            for item in plan["devices"] if item["kind"] == "pfet"]

    result = drc.check(shapes, layers, tech, pmos=pmos)
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
