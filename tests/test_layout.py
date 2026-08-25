"""V1.5: the floorplan, and what the interconnect it implies costs.

Everything a schematic leaves out: that devices occupy silicon and that the
wires between them are capacitors. The dimensions and the capacitances come
out of the PDK's own technology file, so these tests check the reading of it
as much as the arithmetic on top.

What is deliberately not tested here, because it is deliberately not done:
design rule checking, layout versus schematic, and real extraction. Those
need Magic and Netgen. A test at the bottom makes sure the code never claims
otherwise.
"""

import pytest

from spice import circuits, layout
from spice.runner import find_ngspice, sky130_available


def fake_tech():
    """The shape tech_constants returns, with round numbers to check against."""
    return {
        "poly_width": 0.15,
        "diff_overhang": 0.25,
        "diff_spacing": 0.27,
        "contact_width": 0.17,
        "metal1_width": 0.14,
        "metal1_area": 25.78e-18,
        "metal1_edge": 44.0e-18,
        "li_area": 36.99e-18,
        "li_edge": 25.5e-18,
        "poly_area": 106.13e-18,
    }


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def test_a_device_is_its_gate_plus_the_diffusion_around_it():
    """Along the channel: L plus the overhang on both sides, from poly.7."""
    cell = layout.device_footprint(10e-6, 0.5e-6, fake_tech())
    assert cell["along"] == pytest.approx(0.5 + 2 * 0.25)
    assert cell["across"] == pytest.approx(10.0)


def test_a_device_is_never_narrower_than_a_contact():
    """A device thinner than the contact that has to land on it would be a
    drawing, not a layout."""
    cell = layout.device_footprint(0.05e-6, 0.15e-6, fake_tech())
    assert cell["across"] == pytest.approx(0.17)


def test_devices_sit_a_diffusion_spacing_apart():
    plan = layout.floorplan(
        [("A", 10e-6, 0.5e-6), ("B", 4e-6, 0.5e-6)], fake_tech()
    )
    first, second = plan["devices"]
    gap = second["x"] - (first["x"] + first["width"])
    assert gap == pytest.approx(0.27)


def test_the_row_is_as_tall_as_its_widest_device():
    plan = layout.floorplan(
        [("A", 10e-6, 0.5e-6), ("B", 40e-6, 0.5e-6)], fake_tech()
    )
    assert plan["height_um"] == pytest.approx(40.0)


def test_area_is_the_bounding_box_and_says_so():
    """Two devices of one micron each, a 0.27 gap, forty microns tall."""
    plan = layout.floorplan(
        [("A", 40e-6, 0.5e-6), ("B", 40e-6, 0.5e-6)], fake_tech()
    )
    assert plan["width_um"] == pytest.approx(1.0 + 0.27 + 1.0)
    assert plan["area_um2"] == pytest.approx(plan["width_um"] * 40.0)
    # The active area is the devices themselves, which is less.
    assert plan["active_area_um2"] < plan["area_um2"]


def test_a_floorplan_of_nothing_is_refused():
    with pytest.raises(layout.LayoutDataError):
        layout.floorplan([], fake_tech())


# ---------------------------------------------------------------------------
# what the wires cost
# ---------------------------------------------------------------------------


def test_wire_capacitance_is_plate_plus_two_edges():
    tech = fake_tech()
    expected = 10.0 * 0.14 * tech["metal1_area"] + 2 * 10.0 * tech["metal1_edge"]
    assert layout.wire_capacitance(10.0, tech) == pytest.approx(expected)


def test_a_longer_wire_costs_proportionally_more():
    tech = fake_tech()
    assert layout.wire_capacitance(20.0, tech) == pytest.approx(
        2 * layout.wire_capacitance(10.0, tech)
    )


def test_a_net_reaching_one_device_has_no_run():
    """A net that goes nowhere is not a wire."""
    plan = layout.floorplan([("A", 10e-6, 0.5e-6)], fake_tech())
    assert layout.net_parasitics(plan, {"solo": ["A"]}, fake_tech()) == {}


def test_a_nets_run_spans_the_devices_it_joins():
    tech = fake_tech()
    plan = layout.floorplan(
        [("A", 10e-6, 0.5e-6), ("B", 10e-6, 0.5e-6), ("C", 10e-6, 0.5e-6)], tech
    )
    near = layout.net_parasitics(plan, {"n": ["A", "B"]}, tech)["n"]
    far = layout.net_parasitics(plan, {"n": ["A", "C"]}, tech)["n"]
    assert far["length_um"] > near["length_um"]
    assert far["capacitance_f"] > near["capacitance_f"]


# ---------------------------------------------------------------------------
# putting it back in the netlist
# ---------------------------------------------------------------------------


def test_parasitics_are_added_before_the_control_block():
    transform = layout.parasitic_transform(
        {"out": {"capacitance_f": 4e-15, "length_um": 40.0, "devices": []}}
    )
    result = transform("* deck\nR1 a b 1k\n.control\nop\n.endc\n.end\n")
    assert "Cpar_out out 0 4e-15" in result
    assert result.index("Cpar_out") < result.index(".control")


def test_a_netlist_with_nowhere_to_put_them_is_refused():
    transform = layout.parasitic_transform(
        {"out": {"capacitance_f": 4e-15, "length_um": 40.0, "devices": []}}
    )
    with pytest.raises(layout.LayoutDataError):
        transform("* a deck with no control block\n.end\n")


def test_no_parasitics_leaves_the_netlist_alone():
    netlist = "* deck\n.control\nop\n.endc\n.end\n"
    assert layout.parasitic_transform({})(netlist) == netlist


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------


def test_only_the_amplifiers_have_a_floorplan():
    assert circuits.has_floorplan("opamp_two_stage")
    assert circuits.has_floorplan("ota_5t")
    for circuit_id in ("divider", "rc_lowpass", "nfet_cs_amp"):
        assert not circuits.has_floorplan(circuit_id)


def test_a_circuit_without_one_says_so_clearly():
    with pytest.raises(circuits.NoFloorplanError) as caught:
        circuits.run_layout("divider", circuits.defaults("divider"))
    assert "SKY130 amplifiers" in str(caught.value)


def test_the_device_list_matches_the_schematic():
    """Eight transistors in the op-amp, six in the OTA, and every width
    that the form can change has to reach the floorplan."""
    params = circuits.defaults("opamp_two_stage")
    devices = circuits.opamp_devices(params)
    assert [name for name, _, _ in devices] == [
        "M8", "M1", "M2", "M3", "M4", "M5", "M6", "M7"
    ]
    widths = {name: width for name, width, _ in devices}
    assert widths["M6"] == params["w6"]
    assert widths["M7"] == params["w7"]
    assert widths["M1"] == params["wpair"]

    assert len(circuits.ota_devices(circuits.defaults("ota_5t"))) == 6


def test_every_net_names_devices_that_exist():
    names = {name for name, _, _ in
             circuits.opamp_devices(circuits.defaults("opamp_two_stage"))}
    for net, members in circuits.OPAMP_NETS.items():
        for member in members:
            assert member in names, (net, member)

    ota_names = {name for name, _, _ in
                 circuits.ota_devices(circuits.defaults("ota_5t"))}
    for net, members in circuits.OTA_NETS.items():
        for member in members:
            assert member in ota_names, (net, member)


def test_the_catalogue_advertises_it():
    listing = {item["id"]: item for item in circuits.catalog()}
    assert listing["divider"]["floorplan"] is None
    assert "floorplan, not a layout" in listing["opamp_two_stage"]["floorplan"]["caption"]


def test_the_code_never_claims_to_have_checked_anything():
    """The one claim that would be a lie. If a design rule check or an LVS
    ever does run here, this test should be the thing that changes."""
    source = open(layout.__file__, encoding="utf-8").read()
    lowered = source.lower()
    # It has to name each thing it is not doing, in its own documentation.
    for disclaimed in ("no router", "design rule check", "layout versus schematic"):
        assert disclaimed in lowered, disclaimed
    # And it must not claim any of them were passed.
    for boast in ("drc clean", "lvs clean", "design rule clean",
                  "extracted layout of", "verified layout"):
        assert boast not in lowered, boast


# ---------------------------------------------------------------------------
# live: reading the real technology file
# ---------------------------------------------------------------------------


requires_pdk = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed to read real dimensions",
)


@requires_pdk
def test_the_real_rules_match_the_published_minimums():
    """Four independent rules, each with a published SKY130 value. They are
    what pin the unit of the DRC section to the nanometre."""
    tech = layout.tech_constants()
    assert tech["poly_width"] == pytest.approx(0.15)
    assert tech["contact_width"] == pytest.approx(0.17)
    assert tech["diff_overhang"] == pytest.approx(0.25)
    assert tech["diff_spacing"] == pytest.approx(0.27)
    assert tech["metal1_width"] == pytest.approx(0.14)


@requires_pdk
def test_the_real_capacitances_are_the_right_order():
    """Metal over field is tens of attofarads per square micron. A value a
    thousand times off would mean the unit was misread."""
    tech = layout.tech_constants()
    assert 1e-18 < tech["metal1_area"] < 1e-15
    assert 1e-18 < tech["metal1_edge"] < 1e-15
    # Poly over active is the thickest oxide capacitance of the three.
    assert tech["poly_area"] > tech["metal1_area"]


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_live_pdk = pytest.mark.skipif(
    ngspice_missing() or not sky130_available() or not layout.tech_available(),
    reason="a real ngspice and the full SKY130 PDK are needed",
)


@pytest.fixture(scope="module")
def live_layout():
    return circuits.run_layout(
        "opamp_two_stage", circuits.defaults("opamp_two_stage")
    )


@requires_live_pdk
def test_live_floorplan_is_the_size_of_its_devices(live_layout):
    plan = live_layout["floorplan"]
    assert len(plan["devices"]) == 8
    # The output driver is the widest device, so it sets the height.
    assert plan["height_um"] == pytest.approx(40.0)
    assert plan["area_um2"] > plan["active_area_um2"] > 0


@requires_live_pdk
def test_live_interconnect_is_femtofarads_not_picofarads(live_layout):
    """Tens of microns of minimum-width metal1. Picofarads would mean the
    geometry or the constants were read wrong."""
    total = live_layout["total_parasitic_f"]
    assert 1e-15 < total < 1e-12


@requires_live_pdk
def test_live_wiring_costs_margin_and_the_cost_is_measured(live_layout):
    """The point of the exercise: the same specs, run again with the wires
    loading them. Phase margin should fall, and not by much."""
    by_key = {item["key"]: item for item in live_layout["comparison"]}
    assert "phase_margin" in by_key
    margin = by_key["phase_margin"]
    assert margin["change"] < 0
    assert abs(margin["change"]) < 5.0
    assert margin["after"] == pytest.approx(
        margin["before"] + margin["change"], abs=1e-9
    )
