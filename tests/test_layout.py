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
        "poly_endcap": 0.13,
        "diff_width": 0.15,
        "contact_spacing": 0.17,
        "contact_surround": 0.04,
        "contact_to_gate": 0.055,
        "li_width": 0.17,
        "li_spacing": 0.17,
        "li_surround": 0.08,
        "implant_surround": 0.185,
        "nwell_width": 0.84,
        "nwell_spacing": 1.27,
        "nwell_surround": 0.18,
        "poly_contact_surround": 0.05,
        "poly_contact_to_diff": 0.19,
        "npc_surround": 0.1,
        "via_width": 0.17,
        "via_spacing": 0.19,
        "metal1_via_surround": 0.03,
        "metal2_width": 0.14,
        "metal2_spacing": 0.14,
        "via1_width": 0.15,
        "via1_spacing": 0.17,
        "via1_surround": 0.055,
        "metal1_spacing": 0.14,
        "ndiff_to_nwell": 0.34,
        "ptap_to_nwell": 0.13,
        "nwell_tap_surround": 0.18,
    }


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


def test_a_device_is_its_gate_plus_the_diffusion_around_it():
    """Along the channel: L plus the overhang the source and drain need.

    poly.7 asks for 0.25, but that diffusion has to be contacted, and the
    contact needs to clear the gate (licon.11 = 0.055), be itself wide
    (licon.1 = 0.17) and be surrounded by diffusion (licon.5a = 0.04).
    Those come to 0.265, so poly.7 alone would draw a device nothing could
    connect to.
    """
    cell = layout.device_footprint(10e-6, 0.5e-6, fake_tech())
    # And even that is not what binds here. The transistor wants 1.03, but
    # source, gate and drain each need a via pad and the pads have to keep
    # met1.2 apart, which comes to 1.05. A device drawn to the transistor
    # rules alone is one its own three wires do not fit on.
    assert 0.5 + 2 * 0.265 == pytest.approx(1.03)
    assert layout.terminal_pitch_minimum(fake_tech()) == pytest.approx(1.05)
    assert cell["along"] == pytest.approx(1.05)
    assert cell["across"] == pytest.approx(10.0)


def test_the_overhang_is_the_larger_of_the_two_rules():
    """Whichever rule binds, the footprint takes it. Here poly.7 is made
    large enough to win, and the answer follows it instead."""
    tech = fake_tech()
    tech["diff_overhang"] = 0.9
    assert layout.source_drain_overhang(tech) == pytest.approx(0.9)


def test_a_device_is_never_narrower_than_a_contact():
    """A device thinner than the contact that has to land on it, plus the
    diffusion that has to surround that contact, would be a drawing rather
    than a layout."""
    cell = layout.device_footprint(0.05e-6, 0.15e-6, fake_tech())
    assert cell["across"] == pytest.approx(0.17 + 2 * 0.04)


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
    """Two devices of 1.05 microns each, a 0.27 gap, forty microns tall.

    1.05 rather than 1.0: 0.03 of it is the diffusion each source and
    drain needs to be contactable at all, and the rest is the room the
    three via pads need to sit side by side without shorting.
    """
    plan = layout.floorplan(
        [("A", 40e-6, 0.5e-6), ("B", 40e-6, 0.5e-6)], fake_tech()
    )
    assert plan["width_um"] == pytest.approx(1.05 + 0.27 + 1.05)
    assert plan["area_um2"] == pytest.approx(plan["width_um"] * 40.0)
    # The active area is the devices themselves, which is less.
    assert plan["active_area_um2"] < plan["area_um2"]


def test_a_floorplan_of_nothing_is_refused():
    with pytest.raises(layout.LayoutDataError):
        layout.floorplan([], fake_tech())


# ---------------------------------------------------------------------------
# the device stack
# ---------------------------------------------------------------------------


def test_a_contact_column_fills_the_diffusion():
    """One contact carries limited current, so the column takes as many as
    the spacing rule allows rather than a number someone picked."""
    column = layout.contact_column(0.0, 0.0, 1.0, fake_tech())
    # 1.0 tall, less 0.04 of surround at each end, leaves 0.92. Contacts are
    # 0.17 and sit 0.17 apart, so (2n - 1) * 0.17 <= 0.92 gives three.
    assert len(column) == 3
    assert column[1][1] - column[0][3] == pytest.approx(0.17)


def test_a_contact_column_stays_inside_its_diffusion():
    column = layout.contact_column(0.0, 0.0, 1.0, fake_tech())
    assert column[0][1] >= 0.04 - 1e-12
    assert column[-1][3] <= 1.0 - 0.04 + 1e-12


def test_a_diffusion_too_short_to_contact_gets_no_contacts():
    """Rather than draw one that breaks the surround rule."""
    assert layout.contact_column(0.0, 0.0, 0.2, fake_tech()) == []


def test_a_taller_device_takes_more_contacts():
    short = layout.contact_column(0.0, 0.0, 1.0, fake_tech())
    tall = layout.contact_column(0.0, 0.0, 10.0, fake_tech())
    assert len(tall) > len(short)


def test_the_implants_say_which_diffusion_is_which():
    plan = layout.floorplan(
        [("N1", 4e-6, 0.15e-6, "nfet"), ("P1", 4e-6, 0.15e-6, "pfet")],
        fake_tech()
    )
    layers = {implant["layer"] for implant in plan["implants"]}
    assert layers == {"NSDM", "PSDM"}


def test_the_two_implants_never_overlap():
    """A diffusion cannot be both n-type and p-type, so where the groups
    meet the implants share a boundary instead of overlapping."""
    plan = layout.floorplan(
        [("N1", 4e-6, 0.15e-6, "nfet"), ("P1", 4e-6, 0.15e-6, "pfet")],
        fake_tech()
    )
    n = [i for i in plan["implants"] if i["layer"] == "NSDM"][0]
    p = [i for i in plan["implants"] if i["layer"] == "PSDM"][0]
    assert n["x2"] <= p["x1"] + 1e-12


def test_an_implant_covers_the_devices_it_is_for():
    plan = layout.floorplan([("N1", 4e-6, 0.15e-6, "nfet")], fake_tech())
    implant = plan["implants"][0]
    device = plan["devices"][0]
    assert implant["x1"] <= device["x"]
    assert implant["y1"] <= device["y"]
    assert implant["x2"] >= device["x"] + device["width"]
    assert implant["y2"] >= device["y"] + device["height"]
    assert implant["holds"] == ["N1"]


def test_one_type_of_device_gets_one_implant():
    plan = layout.floorplan(
        [("N1", 4e-6, 0.15e-6), ("N2", 4e-6, 0.15e-6)], fake_tech()
    )
    assert len(plan["implants"]) == 1
    assert plan["implants"][0]["layer"] == "NSDM"


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
    # NMOS first, then the PMOS group, so the wells can merge into one.
    assert [entry[0] for entry in devices] == [
        "M8", "M1", "M2", "M5", "M7", "M3", "M4", "M6"
    ]
    assert [entry[3] for entry in devices] == (
        ["nfet"] * 5 + ["pfet"] * 3
    )
    widths = {entry[0]: entry[1] for entry in devices}
    assert widths["M6"] == params["w6"]
    assert widths["M7"] == params["w7"]
    assert widths["M1"] == params["wpair"]

    assert len(circuits.ota_devices(circuits.defaults("ota_5t"))) == 6


def test_every_net_names_devices_and_pins_that_exist():
    """The connectivity is read off the netlist now, so this checks the
    reading rather than a list someone kept in step by hand. A pin is a
    transistor terminal or one end of a drawn passive, and nothing else."""
    for circuit_id, lister in (("opamp_two_stage", circuits.opamp_devices),
                               ("ota_5t", circuits.ota_devices)):
        params = circuits.defaults(circuit_id)
        placed = {entry[0] for entry in lister(params)}
        placed |= {item["name"]
                   for item in circuits.drawable_passives(circuit_id, params)}
        for net, pins in circuits.circuit_nets(circuit_id, params).items():
            for name, terminal in pins:
                assert name in placed, (circuit_id, net, name)
                assert terminal in circuits.TERMINAL_ORDER + ("p1", "p2")


def test_the_netlist_is_the_only_statement_of_the_connectivity():
    """A second list beside the netlist is a second thing to keep right."""
    source = open(circuits.__file__, encoding="utf-8").read()
    for gone in ("OPAMP_NETS", "OTA_NETS"):
        assert gone not in source, gone


def test_the_devices_read_out_match_the_devices_placed():
    params = circuits.defaults("opamp_two_stage")
    read = circuits.circuit_devices("opamp_two_stage", params)
    placed = {entry[0]: entry[3] for entry in circuits.opamp_devices(params)}
    assert set(read) == set(placed)
    # And they agree about which are p-channel.
    for name, device in read.items():
        assert device["kind"] == placed[name], name


def test_the_catalogue_advertises_it():
    listing = {item["id"]: item for item in circuits.catalog()}
    assert listing["divider"]["floorplan"] is None
    assert "floorplan, not a layout" in listing["opamp_two_stage"]["floorplan"]["caption"]


def test_the_code_never_claims_to_have_checked_anything():
    """The one claim that would be a lie.

    This test is meant to change as the tool grows, and it has: it used to
    require the module to disclaim a router, and there is a router now. What
    it must still disclaim is layout versus schematic, which nothing here
    does, and the taps, without which the geometry is not manufacturable
    however many rules it passes.
    """
    source = open(layout.__file__, encoding="utf-8").read()
    lowered = source.lower()
    # It has to name each thing it is still not doing, in its own docs.
    for disclaimed in ("layout versus schematic", "taps"):
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
    # Eight schematic devices, drawn matched: the two declared pairs split
    # into two fingers each and gain a dummy at both ends of each array,
    # so eight becomes sixteen placed transistors.
    assert len(plan["devices"]) == 16
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


# ---------------------------------------------------------------------------
# routing: wires that are drawn, and therefore measurable
# ---------------------------------------------------------------------------


def routing_tech():
    tech = fake_tech()
    tech["metal1_spacing"] = 0.14
    return tech


def test_a_net_gets_a_track_and_a_stub_for_each_pin():
    tech = routing_tech()
    plan = layout.floorplan(
        [("A", 10e-6, 0.5e-6), ("B", 10e-6, 0.5e-6)], tech
    )
    routed = layout.route(
        plan, {"n": [("A", "drain"), ("B", "source")]}, tech
    )["n"]
    assert len(routed["stubs"]) == 2
    assert routed["devices"] == ["A", "B"]
    # One via down to the interconnect and one up to metal2, per pin.
    assert len(routed["contacts"]) == 2
    assert len(routed["vias"]) == 2


def test_a_net_reaches_pins_not_devices():
    """Two pins of one device can be on different nets, and one net can
    reach two pins of the same device. Routing to a device cannot express
    either."""
    tech = routing_tech()
    plan = layout.floorplan([("A", 10e-6, 0.5e-6)], tech)
    routed = layout.route(
        plan, {"diode": [("A", "drain"), ("A", "gate")]}, tech
    )
    assert "diode" in routed
    assert routed["diode"]["pins"] == [("A", "drain"), ("A", "gate")]


def test_a_bulk_pin_is_carried_by_a_tap_and_not_by_a_stub():
    """A bulk connects through its well or substrate tap. The net is real
    and gets wired, but nothing aims a stub at the device's body."""
    tech = routing_tech()
    plan = layout.floorplan([("A", 10e-6, 0.5e-6)], tech)
    routed = layout.route(plan, {"gnd": [("A", "bulk"), ("A", "source")]},
                          tech)["gnd"]
    aimed = {(stub["device"], stub["terminal"]) for stub in routed["stubs"]}
    assert ("A", "bulk") not in aimed
    assert ("A", "source") in aimed
    assert ("ptap", "tap") in aimed


def test_a_net_with_only_a_bulk_pin_ties_the_ring_and_nothing_else():
    """Before guard rings, a bulk-only net drew nothing: the tap alone was
    one landing, and one landing is not a wire. The ring changed that --
    an untied guard ring is a doped strip that does nothing -- so the rail
    is now drawn. What must still be true is that it lands only on taps
    and rings, never on the transistor's own terminals."""
    tech = routing_tech()
    plan = layout.floorplan([("A", 10e-6, 0.5e-6)], tech)
    routed = layout.route(plan, {"gnd": [("A", "bulk")]}, tech)
    assert "gnd" in routed
    for name, terminal in routed["gnd"]["pins"]:
        assert terminal == "tap", (name, terminal)


def test_every_net_gets_its_own_track():
    """Two nets sharing a track would be one net."""
    tech = routing_tech()
    plan = layout.floorplan(
        [("A", 10e-6, 0.5e-6), ("B", 10e-6, 0.5e-6), ("C", 10e-6, 0.5e-6)],
        tech
    )
    routed = layout.route(plan, {
        "x": [("A", "drain"), ("B", "source")],
        "y": [("B", "drain"), ("C", "source")],
    }, tech)
    assert routed["x"]["track"] != routed["y"]["track"]
    gap = abs(routed["x"]["track"] - routed["y"]["track"])
    assert gap >= tech["metal2_width"] + tech["metal2_spacing"] - 1e-9


def test_a_net_reaching_one_pin_is_not_routed():
    tech = routing_tech()
    plan = layout.floorplan([("A", 10e-6, 0.5e-6)], tech)
    assert layout.route(plan, {"solo": [("A", "drain")]}, tech) == {}


def test_the_length_is_the_metal_that_was_drawn():
    """Not a bounding box: the run plus every stub that climbs to it."""
    tech = routing_tech()
    plan = layout.floorplan(
        [("A", 10e-6, 0.5e-6), ("B", 40e-6, 0.5e-6)], tech
    )
    routed = layout.route(
        plan, {"n": [("A", "drain"), ("B", "source")]}, tech
    )["n"]
    drawn = routed["span"]["length_um"] + sum(
        stub["length_um"] for stub in routed["stubs"])
    assert routed["length_um"] == pytest.approx(drawn)
    assert routed["length_um"] > plan["width_um"]


def test_drawn_routing_costs_more_than_the_bounding_box_estimate():
    """The estimate this replaced counted the run across the row and not
    the climb down onto devices tens of microns tall."""
    tech = routing_tech()
    devices = [("A", 10e-6, 0.5e-6), ("B", 40e-6, 0.5e-6), ("C", 10e-6, 0.5e-6)]
    plan = layout.floorplan(devices, tech)

    estimated = layout.net_parasitics(
        plan, {"n": ["A", "C"]}, tech)["n"]["capacitance_f"]
    drawn = layout.routed_parasitics(
        layout.route(plan, {"n": [("A", "drain"), ("C", "source")]}, tech),
        tech
    )["n"]["capacitance_f"]
    assert drawn > estimated


def test_routing_becomes_shapes_on_both_metals():
    """Metal1 runs vertically and metal2 horizontally, joined only by a
    via. On one layer every stub would cross every track beneath it."""
    tech = routing_tech()
    plan = layout.floorplan(
        [("A", 10e-6, 0.5e-6), ("B", 10e-6, 0.5e-6)], tech
    )
    routed = layout.route(
        plan, {"n": [("A", "drain"), ("B", "source")]}, tech
    )
    layers = {"MET1": (68, 20), "MET2": (69, 20),
              "VIA1": (68, 44), "MCON": (67, 44)}
    shapes = layout.routing_shapes(routed, layers)
    drawn = {shape[:2] for shape in shapes}
    assert (69, 20) in drawn                    # the track
    assert (68, 20) in drawn                    # the stubs
    assert (68, 44) in drawn                    # metal1 to metal2
    assert (67, 44) in drawn                    # metal1 down to li


def test_the_track_clears_everything_already_drawn():
    """A track over the gate stack would short every gate it crossed."""
    tech = routing_tech()
    plan = layout.floorplan([("A", 10e-6, 0.5e-6)], tech)
    floor = layout.routing_floor(plan, tech)
    assert floor > plan["height_um"] + layout.gate_stack_height(tech)


# ---------------------------------------------------------------------------
# the passives: the compensation network, drawn
# ---------------------------------------------------------------------------


requires_tech_for_passives = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed for the sheet and plate "
           "constants",
)


@requires_tech_for_passives
def test_the_resistor_is_sized_off_the_sheet_the_pdk_states():
    """Ohms in, microns out, and the value recomputed from the drawn
    marker so nothing is taken on trust."""
    tech = layout.tech_constants()
    cell = layout.resistor_footprint(2000.0, tech)
    assert cell["drawn_ohms"] == pytest.approx(2000.0, rel=0.005)
    # The marker length is the squares, and it sits on the grid.
    grid = tech["grid"]
    assert cell["marker"] == pytest.approx(
        round(cell["marker"] / grid) * grid)


@requires_tech_for_passives
def test_the_capacitor_is_sized_off_the_plate_the_pdk_states():
    tech = layout.tech_constants()
    cell = layout.capacitor_footprint(2e-12, tech, 40.0)
    assert cell["drawn_farads"] == pytest.approx(2e-12, rel=0.005)
    # Two picofarads of parallel plate is thousands of square microns.
    # That is why real processes put MiM capacitors on upper metals, and
    # the honest cost of building one from the two layers this stack has.
    assert cell["area_um2"] > 10000.0


@requires_tech_for_passives
def test_the_passives_are_placed_past_the_tap_and_the_well():
    """The first draft placed them off the device span alone, and the
    layout-versus-schematic caught the resistor's wire parked on top of
    the tap's wire as a supply short. The clearance is the regression."""
    tech = layout.tech_constants()
    params = circuits.defaults("opamp_two_stage")
    plan = layout.floorplan(
        circuits.opamp_devices(params), tech,
        passives=circuits.drawable_passives("opamp_two_stage", params))

    edge = 0.0
    for tap in plan["taps"]:
        edge = max(edge, tap["x2"])
    for well in plan["wells"]:
        edge = max(edge, well["x2"])
    for item in plan["passives"]:
        assert item["x"] > edge


@requires_tech_for_passives
def test_a_circuit_without_internal_passives_draws_none():
    params = circuits.defaults("ota_5t")
    plan = layout.floorplan(
        circuits.ota_devices(params), tech=layout.tech_constants(),
        passives=circuits.drawable_passives("ota_5t", params))
    assert plan["passives"] == []


def test_the_boundary_rule_separates_cell_from_bench():
    """A passive across two internal nets is the circuit; one touching a
    rail or ground is a bias or a load. The op-amp's compensation network
    is exactly the former, and the loads are exactly the latter."""
    params = circuits.defaults("opamp_two_stage")
    drawn = {item["name"]
             for item in circuits.drawable_passives("opamp_two_stage", params)}
    outside = {item["name"]
               for item in circuits.external_elements("opamp_two_stage",
                                                      params)}
    assert drawn == {"Rz", "Cc"}
    assert outside == {"Ib", "CL"}
