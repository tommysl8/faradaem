"""Layout versus schematic, and the proof that it can say no.

A comparison that always matches is worse than no comparison, because it
turns an unverified drawing into one someone believes. So each test here
breaks the layout in one specific way -- an open, a short, a wire on the
wrong pin -- and checks that the comparison notices.

The last group runs it against the geometry Faradaem actually emits.
"""

import pytest

from spice import circuits, drc, layout, lvs

LAYERS = {
    "DIFF": (65, 20), "POLY": (66, 20), "NWELL": (64, 20), "LI": (67, 20),
    "MET1": (68, 20), "MET2": (69, 20), "CONT": (66, 44), "MCON": (67, 44),
    "VIA1": (68, 44), "NSDM": (93, 44), "PSDM": (94, 20), "TAP": (65, 44),
    "NPC": (95, 20),
}


def shape(layer, box):
    number, datatype = LAYERS[layer]
    return (number, datatype, box[0], box[1], box[2], box[3])


def transistor(x, name="M", width=1.0, height=2.0, gate=0.3):
    """One device: a diffusion with a gate crossing it, and li on each of
    the three terminals so a wire has somewhere to land."""
    left = x + (width - gate) / 2.0
    return [
        shape("DIFF", (x, 0.0, x + width, height)),
        shape("POLY", (left, -0.2, left + gate, height + 0.6)),
        shape("CONT", (x + 0.1, 0.8, x + 0.27, 0.97)),
        shape("LI", (x + 0.1, 0.7, x + 0.27, 1.1)),
        shape("CONT", (x + width - 0.27, 0.8, x + width - 0.1, 0.97)),
        shape("LI", (x + width - 0.27, 0.7, x + width - 0.1, 1.1)),
        shape("CONT", (left, height + 0.2, left + 0.17, height + 0.37)),
        shape("LI", (left, height + 0.1, left + 0.17, height + 0.5)),
    ]


def wire(x1, y1, x2, y2, layer="MET1"):
    return shape(layer, (x1, y1, x2, y2))


def join(box, layer="MCON"):
    return shape(layer, box)


# ---------------------------------------------------------------------------
# what it finds in a drawing, before comparing anything
# ---------------------------------------------------------------------------


def test_a_gate_crossing_a_diffusion_is_a_transistor():
    found = lvs.extract(transistor(0.0), LAYERS)
    assert len(found["devices"]) == 1


def test_the_gate_splits_the_diffusion_into_a_source_and_a_drain():
    """The channel is not a conductor until the gate says so. An extractor
    that misses this reports every device as a short across itself."""
    found = lvs.extract(transistor(0.0), LAYERS)
    device = found["devices"][0]
    union = found["union"]
    assert union.find(device["source"]) != union.find(device["drain"])


def test_a_diffusion_with_no_gate_is_one_conductor():
    regions, gates = lvs.split_diffusion((0.0, 0.0, 1.0, 2.0), [])
    assert len(regions) == 1
    assert gates == []


def test_two_gates_on_one_diffusion_make_three_regions():
    diff = (0.0, 0.0, 3.0, 2.0)
    gates = [(1.0, -0.2, 1.2, 2.2), (2.0, -0.2, 2.2, 2.2)]
    regions, crossing = lvs.split_diffusion(diff, gates)
    assert len(regions) == 3
    assert len(crossing) == 2


def test_metal_on_its_own_joins_nothing_to_the_silicon():
    """Metal over local interconnect is not a connection. Only a via is."""
    shapes = transistor(0.0) + [wire(0.1, 0.7, 0.27, 5.3)]
    found = lvs.extract(shapes, LAYERS)
    union = found["union"]
    device = found["devices"][0]
    metal = [key for key in union.parent if key[0] == "MET1"][0]
    assert union.find(metal) != union.find(device["source"])


def test_a_via_is_what_makes_the_connection():
    shapes = transistor(0.0) + [
        wire(0.1, 0.7, 0.27, 5.3),
        join((0.1, 0.9, 0.27, 1.07)),
    ]
    found = lvs.extract(shapes, LAYERS)
    union = found["union"]
    device = found["devices"][0]
    metal = [key for key in union.parent if key[0] == "MET1"][0]
    assert union.find(metal) == union.find(device["source"])


# ---------------------------------------------------------------------------
# comparing it against the netlist
# ---------------------------------------------------------------------------


def two_devices():
    """Two transistors, with M1's drain wired to M2's source."""
    shapes = transistor(0.0, width=1.0) + transistor(2.0, width=1.0)
    shapes += [
        # The stub has to reach into the track, or the via between them
        # lands on nothing, which is exactly the failure below.
        wire(0.73, 0.7, 0.9, 5.3),
        join((0.73, 0.9, 0.9, 1.07)),
        wire(2.1, 0.7, 2.27, 5.3),
        join((2.1, 0.9, 2.27, 1.07)),
        wire(0.6, 5.0, 2.4, 5.3, "MET2"),
        shape("VIA1", (0.75, 5.05, 0.9, 5.2)),
        shape("VIA1", (2.1, 5.05, 2.25, 5.2)),
    ]
    return shapes


SCHEMATIC = {
    "M1": {"name": "M1", "kind": "nfet",
           "terminals": {"drain": "mid", "gate": "g1",
                         "source": "s1", "bulk": "sub"}},
    "M2": {"name": "M2", "kind": "nfet",
           "terminals": {"drain": "d2", "gate": "g2",
                         "source": "mid", "bulk": "sub"}},
}


def test_a_layout_that_matches_its_schematic_is_reported_as_matching():
    result = lvs.compare(two_devices(), LAYERS, SCHEMATIC, ["M1", "M2"])
    assert result["match"], result["problems"]
    assert result["devices_compared"] == 2


def test_a_missing_wire_is_caught():
    """The commonest real failure: a connection the schematic has and the
    drawing does not."""
    shapes = [s for s in two_devices() if s[:2] != LAYERS["MET2"]]
    result = lvs.compare(shapes, LAYERS, SCHEMATIC, ["M1", "M2"])
    assert not result["match"]
    assert any(item["kind"] == "not_connected" for item in result["problems"])


def test_a_missing_via_is_caught():
    """Metal drawn right over the pin, and never joined to it, looks
    connected and is not."""
    shapes = [s for s in two_devices() if s[:2] != LAYERS["MCON"]]
    result = lvs.compare(shapes, LAYERS, SCHEMATIC, ["M1", "M2"])
    assert not result["match"]
    assert any(item["kind"] == "not_connected" for item in result["problems"])


def test_a_short_between_two_nets_is_caught():
    """Two nets the schematic keeps apart, joined by a piece of metal."""
    shapes = two_devices() + [
        wire(0.1, 0.7, 0.27, 5.3),                  # onto M1's source
        join((0.1, 0.9, 0.27, 1.07)),
        # A stray piece of track reaching back over M1's source, touching
        # the real one, with a via down onto the stub.
        shape("MET2", (0.05, 5.0, 0.7, 5.3)),
        shape("VIA1", (0.12, 5.05, 0.27, 5.2)),
    ]
    result = lvs.compare(shapes, LAYERS, SCHEMATIC, ["M1", "M2"])
    assert not result["match"]
    assert any(item["kind"] == "shorted" for item in result["problems"])


def test_a_wire_on_the_wrong_pin_is_caught():
    """The failure no rule check can see: legal geometry, wrong circuit."""
    wrong = dict(SCHEMATIC)
    wrong["M2"] = {"name": "M2", "kind": "nfet",
                   "terminals": {"drain": "d2", "gate": "mid",
                                 "source": "s2", "bulk": "sub"}}
    result = lvs.compare(two_devices(), LAYERS, wrong, ["M1", "M2"])
    assert not result["match"]


def test_a_device_count_mismatch_is_caught():
    result = lvs.compare(transistor(0.0), LAYERS, SCHEMATIC, ["M1", "M2"])
    assert not result["match"]
    assert any(item["kind"] == "device_count" for item in result["problems"])


def test_source_and_drain_are_interchangeable():
    """A MOSFET is symmetric, so which diffusion is called the source is a
    naming choice. Reporting a swap as a mismatch would be noise."""
    swapped = {
        "M1": {"name": "M1", "kind": "nfet",
               "terminals": {"drain": "s1", "gate": "g1",
                             "source": "mid", "bulk": "sub"}},
        "M2": SCHEMATIC["M2"],
    }
    result = lvs.compare(two_devices(), LAYERS, swapped, ["M1", "M2"])
    assert result["match"], result["problems"]


def test_the_result_states_what_it_did_not_check():
    result = lvs.compare(two_devices(), LAYERS, SCHEMATIC, ["M1", "M2"])
    coverage = result["coverage"].lower()
    assert "connectivity only" in coverage
    assert "widths and" in coverage        # sizes are not compared
    # And the part that is easiest to overstate: only transistors were
    # compared, because only transistors are drawn.
    assert "only transistors are compared" in coverage
    assert "undrawn" in coverage


def test_the_module_never_calls_itself_sign_off():
    source = open(lvs.__file__, encoding="utf-8").read().lower()
    assert "this is not" in source or "is not a full" in source
    for boast in ("lvs clean", "fully verified", "passes lvs"):
        assert boast not in source, boast


def test_nothing_to_compare_is_refused_rather_than_passed():
    with pytest.raises(lvs.LvsError):
        lvs.run([], LAYERS, SCHEMATIC, ["M1"])


# ---------------------------------------------------------------------------
# live: the geometry Faradaem actually emits
# ---------------------------------------------------------------------------


requires_pdk = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed for the real dimensions",
)

#: Every circuit that has a floorplan, read from the registry, so a new
#: topology is checked here the moment it is added.
LAID_OUT = [item["id"] for item in circuits.catalog()
            if circuits.has_floorplan(item["id"])]


def drawn(circuit_id):
    """Build a circuit's geometry the way the tool does.

    The device list comes off the registry rather than a branch here, so a
    topology added to the registry is covered by these tests without any of
    them being edited. That is the point of a registry.
    """
    tech = layout.tech_constants()
    layers = layout.gds_layers()
    params = circuits.defaults(circuit_id)
    lister = circuits.get_circuit(circuit_id)["floorplan"]["devices"]
    plan = layout.floorplan(lister(params), tech)
    routed = layout.route(plan, circuits.circuit_nets(circuit_id, params),
                          tech)
    shapes = (layout.floorplan_shapes(plan, layers, tech)
              + layout.routing_shapes(routed, layers))
    return plan, shapes, layers, tech, params


@requires_pdk
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_the_emitted_layout_is_the_circuit_that_was_simulated(circuit_id):
    """The whole point. If this fails, the drawing and the numbers are of
    two different circuits."""
    plan, shapes, layers, _, params = drawn(circuit_id)
    result = lvs.compare(
        shapes, layers, circuits.circuit_devices(circuit_id, params),
        [item["name"] for item in plan["devices"]]
    )
    assert result["match"], result["problems"][:5]
    assert result["nets_drawn"] == result["nets_expected"]


@requires_pdk
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_every_transistor_in_the_netlist_is_in_the_drawing(circuit_id):
    plan, shapes, layers, _, params = drawn(circuit_id)
    found = lvs.extract(shapes, layers)
    assert len(found["devices"]) == len(plan["devices"])
    assert len(found["devices"]) == len(
        circuits.circuit_devices(circuit_id, params))


@requires_pdk
def test_cutting_one_via_out_of_the_real_layout_is_caught():
    """A single missing via in eight hundred shapes is exactly the kind of
    thing a person does not see and this has to."""
    plan, shapes, layers, _, params = drawn("opamp_two_stage")
    number = layers["MCON"]
    without = list(shapes)
    for index, item in enumerate(without):
        if item[:2] == number:
            del without[index]
            break

    result = lvs.compare(
        without, layers, circuits.circuit_devices("opamp_two_stage", params),
        [item["name"] for item in plan["devices"]]
    )
    assert not result["match"]


@requires_pdk
def test_the_real_layout_is_both_legal_and_correct():
    """Two different questions. Geometry can be legal and wrong, and this
    is the pair of answers that says it is neither."""
    plan, shapes, layers, tech, params = drawn("opamp_two_stage")
    pmos = [(item["x"], item["y"], item["x"] + item["width"],
             item["y"] + item["height"])
            for item in plan["devices"] if item["kind"] == "pfet"]

    assert drc.check(shapes, layers, tech, pmos=pmos)["clean"]
    assert lvs.compare(
        shapes, layers, circuits.circuit_devices("opamp_two_stage", params),
        [item["name"] for item in plan["devices"]]
    )["match"]
