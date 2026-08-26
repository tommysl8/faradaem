"""Matching, and the exact property it is supposed to deliver.

A differential pair drawn side by side samples the process gradient at two
different points, and the difference turns up at the output as an offset
the schematic never predicted. Common centroid fixes it by construction,
which means the fix can be checked by construction: compute both centroids
and require them equal. That is the test worth having, and most of this
file is it.
"""

import pytest

from spice import circuits, drc, klvs, layout, signoff

requires_tech = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed for the real dimensions",
)

LAID_OUT = [item["id"] for item in circuits.catalog()
            if circuits.has_floorplan(item["id"])]


def arrays_of(circuit_id):
    params = circuits.defaults(circuit_id)
    block = circuits.get_circuit(circuit_id)["floorplan"]
    ordered, arrays = layout.matched_layout(block["devices"](params),
                                            block.get("matched"))
    return params, ordered, arrays


# ---------------------------------------------------------------------------
# the ordering
# ---------------------------------------------------------------------------


def test_a_matched_pair_is_ordered_a_b_b_a():
    """The sequence has to read the same forwards and backwards. That is
    what puts both centroids in the middle of the array."""
    order = layout.common_centroid_order(["M1", "M2"])
    assert order == ["M1", "M2", "M2", "M1"]
    assert order == list(reversed(order))


def test_splitting_a_device_divides_its_width_and_not_its_length():
    """A folded device is N devices of a share of the width in parallel.
    Dividing the length would be a different transistor."""
    fingers = layout.fingered(("M1", 1e-5, 5e-7, "nfet"), fingers=2)
    assert len(fingers) == 2
    assert sum(item[1] for item in fingers) == pytest.approx(1e-5)
    for item in fingers:
        assert item[2] == 5e-7
        assert item[3] == "nfet"


def test_a_finger_knows_which_device_it_belongs_to():
    assert layout.device_of("M1@2") == "M1"
    assert layout.device_of("M5") == "M5"
    assert layout.device_of("DUMMY_M1_L1") is None
    assert layout.is_dummy("DUMMY_M1_L1")
    assert not layout.is_dummy("M1@1")


def test_every_matched_group_gets_a_dummy_at_each_end():
    """The outermost real finger would otherwise see open field on one side
    and a neighbour on the other, and etch differently for it."""
    _, _, arrays = arrays_of("opamp_two_stage")
    assert arrays
    for array in arrays:
        assert layout.is_dummy(array["names"][0])
        assert layout.is_dummy(array["names"][-1])
        inner = array["names"][1:-1]
        assert not any(layout.is_dummy(name) for name in inner)


def test_devices_outside_a_matched_group_keep_their_place():
    _, ordered, _ = arrays_of("opamp_two_stage")
    names = [entry[0] for entry in ordered]
    for name in ("M8", "M5", "M7", "M6"):
        assert name in names


def test_a_circuit_with_no_matched_groups_is_left_alone():
    devices = [("M1", 1e-5, 5e-7, "nfet"), ("M2", 1e-5, 5e-7, "nfet")]
    ordered, arrays = layout.matched_layout(devices, None)
    assert ordered == devices
    assert arrays == []


# ---------------------------------------------------------------------------
# the property itself
# ---------------------------------------------------------------------------


@requires_tech
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_every_matched_pair_shares_a_centroid(circuit_id):
    """The whole technique, checked exactly rather than approximately."""
    _, ordered, arrays = arrays_of(circuit_id)
    plan = layout.floorplan(ordered, layout.tech_constants())
    placed = {item["name"]: item for item in plan["devices"]}

    assert arrays, circuit_id
    for array in arrays:
        centroids = []
        for member in array["group"]:
            fingers = [placed[name] for name in array["names"]
                       if layout.device_of(name) == member]
            assert len(fingers) == array["fingers"], (member, len(fingers))
            centroids.append(layout.centroid_of(fingers))

        first, second = centroids
        assert first[0] == pytest.approx(second[0], abs=1e-9), array["group"]
        assert first[1] == pytest.approx(second[1], abs=1e-9), array["group"]


@requires_tech
def test_the_pairs_that_matter_are_the_ones_declared_matched():
    """A differential pair and a current mirror are where two devices being
    the same is what makes the circuit work."""
    for circuit_id in LAID_OUT:
        matched = circuits.get_circuit(circuit_id)["floorplan"].get("matched")
        assert matched, circuit_id
        assert ["M1", "M2"] in matched, circuit_id


@requires_tech
def test_a_dummy_is_tied_off_and_never_left_floating():
    """An unconnected gate is a reliability problem, and an extractor reads
    it as a transistor wired to nothing."""
    params = circuits.defaults("ota_5t")
    block = circuits.get_circuit("ota_5t")["floorplan"]
    ordered, _ = layout.matched_layout(block["devices"](params),
                                       block["matched"])
    plan = layout.floorplan(ordered, layout.tech_constants())
    routed = layout.route(plan, circuits.circuit_nets("ota_5t", params),
                          layout.tech_constants())

    tied = {name for item in routed.values()
            for name, _ in item["pins"] if layout.is_dummy(name)}
    drawn = {item["name"] for item in plan["devices"]
             if layout.is_dummy(item["name"])}
    assert drawn
    assert tied == drawn, drawn - tied


# ---------------------------------------------------------------------------
# and it still passes everything it passed before
# ---------------------------------------------------------------------------


@requires_tech
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_the_matched_layout_is_still_rule_clean(circuit_id):
    params = circuits.defaults(circuit_id)
    shapes = circuits.layout_shapes(circuit_id, params)
    block = circuits.get_circuit(circuit_id)["floorplan"]
    ordered, _ = layout.matched_layout(block["devices"](params),
                                       block.get("matched"))
    plan = layout.floorplan(
        ordered, layout.tech_constants(),
        passives=circuits.drawable_passives(circuit_id, params))
    pmos = [(item["x"], item["y"], item["x"] + item["width"],
             item["y"] + item["height"])
            for item in plan["devices"] if item["kind"] == "pfet"]

    found = drc.check(shapes, layout.gds_layers(), layout.tech_constants(),
                      pmos=pmos)
    assert found["clean"], found["violations"][:3]


@pytest.mark.skipif(not klvs.available() or not layout.tech_available(),
                    reason="KLayout's engine and the PDK are needed")
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_the_fingers_recombine_into_the_circuit(circuit_id):
    """A device drawn as two fingers is two devices in parallel, which is
    what it physically is. Both sides of the comparison are combined the
    same way, so what is compared is two circuits and not two different
    simplifications."""
    found = klvs.compare(circuit_id, circuits.defaults(circuit_id))
    assert found["match"], found["log"][:6]


@pytest.mark.skipif(not signoff.available() or not layout.tech_available(),
                    reason="KLayout and the SKY130 runset are needed")
def test_the_matched_layout_passes_the_foundrys_deck():
    shapes = circuits.layout_shapes("ota_5t", circuits.defaults("ota_5t"))
    found = signoff.run_drc(shapes, "ota_5t")
    assert found["clean"], found["violations"]


# ---------------------------------------------------------------------------
# guard rings
# ---------------------------------------------------------------------------


@requires_tech
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_each_group_gets_a_closed_ring(circuit_id):
    """Three drawn segments plus the tap strip above them. A ring open on
    one side holds the substrate at one edge instead of all round."""
    params, ordered, _ = arrays_of(circuit_id)
    plan = layout.floorplan(ordered, layout.tech_constants())

    for kind in ("ptap", "ntap"):
        sides = {item["side"] for item in plan["guards"]
                 if item["kind"] == kind}
        assert sides == {"bottom", "left", "right"}, (circuit_id, kind)
        assert any(tap["kind"] == kind for tap in plan["taps"])


@requires_tech
def test_the_ring_segments_abut_so_the_ring_is_continuous():
    """The uprights start at the bottom strip's own bottom edge, so the
    doped regions are one region. Only their contacts keep clear."""
    params, ordered, _ = arrays_of("ota_5t")
    plan = layout.floorplan(ordered, layout.tech_constants())

    for kind in ("ptap", "ntap"):
        segments = {item["side"]: item for item in plan["guards"]
                    if item["kind"] == kind}
        bottom, left = segments["bottom"], segments["left"]
        assert left["y1"] == pytest.approx(bottom["y1"])
        assert left["x1"] == pytest.approx(bottom["x1"])


@requires_tech
def test_the_uprights_keep_their_contacts_out_of_the_corner():
    params, ordered, _ = arrays_of("ota_5t")
    plan = layout.floorplan(ordered, layout.tech_constants())
    upright = [item for item in plan["guards"]
               if item["side"] == "left"][0]
    assert upright["contact_from"] > 0.0

    cuts = layout.tap_contacts(upright, layout.tech_constants())
    assert cuts
    assert cuts[0][1] > upright["y1"]


@requires_tech
def test_the_well_holds_its_own_guard_ring():
    """An n-tap outside the n-well is not a well tap."""
    params, ordered, _ = arrays_of("ota_5t")
    plan = layout.floorplan(ordered, layout.tech_constants())
    well = plan["wells"][0]
    for item in plan["guards"]:
        if item["kind"] != "ntap":
            continue
        assert well["x1"] <= item["x1"] and item["x2"] <= well["x2"]
        assert well["y1"] <= item["y1"] and item["y2"] <= well["y2"]


@requires_tech
def test_the_two_rings_do_not_meet():
    """The substrate ring and the well ring touching is a short from the
    substrate to the well, which is why the groups sit further apart now."""
    params, ordered, _ = arrays_of("ota_5t")
    plan = layout.floorplan(ordered, layout.tech_constants())
    p_right = max(item["x2"] for item in plan["guards"]
                  if item["kind"] == "ptap")
    n_left = min(item["x1"] for item in plan["guards"]
                 if item["kind"] == "ntap")
    assert n_left > p_right


@requires_tech
def test_the_ring_is_tied_and_not_left_floating():
    """An unconnected guard ring is a doped strip that does nothing."""
    params = circuits.defaults("ota_5t")
    block = circuits.get_circuit("ota_5t")["floorplan"]
    ordered, _ = layout.matched_layout(block["devices"](params),
                                       block["matched"])
    tech = layout.tech_constants()
    plan = layout.floorplan(ordered, tech)
    routed = layout.route(plan, circuits.circuit_nets("ota_5t", params), tech)

    landed = {name for item in routed.values()
              for name, terminal in item["pins"] if terminal == "tap"}
    assert "ptap" in landed and "ntap" in landed


@requires_tech
def test_the_group_gap_makes_room_for_both_rings():
    tech = layout.tech_constants()
    rings = 2.0 * (layout.tap_height(tech) + tech["diff_spacing"])
    assert layout.group_gap(tech) >= rings
