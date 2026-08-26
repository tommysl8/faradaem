"""The folded cascode, and the reason a third topology was worth adding.

The reason is not the circuit. It is the question a third one answers:
whether the machinery built for the first two generalises, or whether each
new topology costs another round of special cases. Every test below is
really that question asked about one piece of it.

The simulator-backed ones skip cleanly without the PDK, the same as the
rest of the suite.
"""

import pytest

from spice import circuits, drc, layout, lvs
from spice.runner import find_ngspice, sky130_available

CIRCUIT = "folded_cascode"


requires_pdk = pytest.mark.skipif(
    not sky130_available() or not find_ngspice(),
    reason="ngspice and the SKY130 PDK are needed to measure a real circuit",
)

requires_tech = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed for the real dimensions",
)


# ---------------------------------------------------------------------------
# it is in the registry, not bolted on beside it
# ---------------------------------------------------------------------------


def test_it_is_in_the_catalogue():
    listing = {item["id"]: item for item in circuits.catalog()}
    assert CIRCUIT in listing
    assert listing[CIRCUIT]["name"] == "Folded cascode (SKY130)"


def test_it_arrived_through_the_registry_and_not_a_special_case():
    """A circuit added any other way is a circuit the rest of the tool does
    not know about."""
    from spice import registry, topologies
    catalogue = open(registry.__file__, encoding="utf-8").read()
    assert '"folded_cascode": {' in catalogue
    assert '"folded_cascode",' in catalogue        # in CIRCUIT_ORDER
    # And its devices are described where every topology is described.
    assert "_folded_cascode_core" in open(
        topologies.__file__, encoding="utf-8").read()
    # And the server has no branch naming it.
    server = open("server.py", encoding="utf-8").read()
    assert "folded_cascode" not in server


def test_every_preset_sets_every_parameter():
    circuit = circuits.get_circuit(CIRCUIT)
    names = {item["key"] for item in circuit["params"]}
    for item in circuit["presets"]:
        assert set(item["params"]) == names, item["label"]


def test_it_has_fourteen_transistors_grouped_by_type():
    devices = circuits.folded_cascode_devices(circuits.defaults(CIRCUIT))
    assert len(devices) == 14
    kinds = [entry[3] for entry in devices]
    # n-channel first, then p-channel, so the p-channel devices share one
    # well rather than needing several a rule apart.
    assert kinds == ["nfet"] * 9 + ["pfet"] * 5


def test_the_widths_the_form_sets_reach_the_devices():
    params = dict(circuits.defaults(CIRCUIT))
    params["wpair"] = 3.3e-5
    params["wcasc"] = 4.4e-5
    widths = {entry[0]: entry[1]
              for entry in circuits.folded_cascode_devices(params)}
    assert widths["M1"] == params["wpair"]
    assert widths["M2"] == params["wpair"]
    assert widths["M6"] == params["wcasc"]
    assert widths["M7"] == params["wcasc"]


# ---------------------------------------------------------------------------
# the connectivity, read off the netlist rather than restated
# ---------------------------------------------------------------------------


def test_the_connectivity_comes_from_the_netlist():
    devices = circuits.circuit_devices(CIRCUIT, circuits.defaults(CIRCUIT))
    assert len(devices) == 14
    # The topology, stated where it can be checked: the pair folds into the
    # sources, and the output is the p-cascode's drain.
    assert devices["M1"]["terminals"]["drain"] == "fold1"
    assert devices["M2"]["terminals"]["drain"] == "fold2"
    assert devices["M3"]["terminals"]["drain"] == "fold1"
    assert devices["M7"]["terminals"]["source"] == "fold2"
    assert devices["M7"]["terminals"]["drain"] == "out"


def test_the_inverting_input_is_the_folded_side():
    """M2's drain reaches the output through one inversion, so M2's gate is
    where the servo has to feed back. On the other gate the loop would be
    positive and the bias would rail."""
    devices = circuits.circuit_devices(CIRCUIT, circuits.defaults(CIRCUIT))
    assert devices["M2"]["terminals"]["gate"] == "inn"
    assert devices["M1"]["terminals"]["gate"] == "inp"


def test_the_cascode_gates_are_biased_and_the_bodies_are_tied():
    devices = circuits.circuit_devices(CIRCUIT, circuits.defaults(CIRCUIT))
    assert devices["M6"]["terminals"]["gate"] == "pcasc"
    assert devices["M9"]["terminals"]["gate"] == "ncasc"
    for name in ("M3", "M4", "M6", "M7", "M13"):
        assert devices[name]["terminals"]["bulk"] == "vdd", name
    for name in ("M1", "M2", "M5", "M8"):
        assert devices[name]["terminals"]["bulk"] == "0", name


# ---------------------------------------------------------------------------
# what it measures, against the topology it is meant to beat
# ---------------------------------------------------------------------------


@requires_pdk
def test_it_biases_and_measures():
    result = circuits.simulate(CIRCUIT, circuits.defaults(CIRCUIT))
    assert 0.2 < result["out_dc"] < 1.6
    assert result["loop_gain_db"] > 40.0
    assert result["f_crossover"] > 5e6
    assert result["phase_margin"] > 45.0


@requires_pdk
def test_it_earns_its_place_against_the_five_transistor_ota():
    """A third topology is only worth its code if it does something the
    other two cannot. This one trades power for gain and bandwidth, which
    is what a folded cascode is for."""
    folded = circuits.simulate(CIRCUIT, circuits.defaults(CIRCUIT))
    ota = circuits.simulate("ota_5t", circuits.defaults("ota_5t"))

    assert folded["loop_gain_db"] > ota["loop_gain_db"] + 10.0
    assert folded["f_crossover"] > ota["f_crossover"]
    # And it is honest about the cost.
    assert folded["power"] > ota["power"]


# ---------------------------------------------------------------------------
# and the layout machinery, which had never seen fourteen devices
# ---------------------------------------------------------------------------


@requires_tech
def test_the_layout_machinery_generalised_without_being_told_about_it():
    """Nothing in the placer, the router, the rule check or the comparison
    knows this topology exists. If any of them had a two-circuit assumption
    in it, this is where it would show."""
    tech = layout.tech_constants()
    layers = layout.gds_layers()
    params = circuits.defaults(CIRCUIT)

    block = circuits.get_circuit(CIRCUIT)["floorplan"]
    ordered, _ = layout.matched_layout(block["devices"](params),
                                      block.get("matched"))
    plan = layout.floorplan(ordered, tech)
    routed = layout.route(plan, circuits.circuit_nets(CIRCUIT, params), tech)
    shapes = (layout.floorplan_shapes(plan, layers, tech)
              + layout.routing_shapes(routed, layers))

    # Fourteen schematic devices; five declared pairs finger into twenty,
    # four stay whole, and each array gains two dummies.
    assert len(plan["devices"]) == len(ordered) == 34
    assert len(plan["taps"]) == 2
    assert len(routed) >= 10

    pmos = [(item["x"], item["y"], item["x"] + item["width"],
             item["y"] + item["height"])
            for item in plan["devices"] if item["kind"] == "pfet"]
    assert drc.check(shapes, layers, tech, pmos=pmos)["clean"]

    declared, order = circuits.drawn_devices(CIRCUIT, params, plan)
    compared = lvs.compare(shapes, layers, declared, order)
    assert compared["match"], compared["problems"][:5]


@requires_tech
def test_it_costs_more_silicon_than_the_simpler_topologies():
    """Fourteen devices in a row, and the widest of them is the folding
    source. A reader should not have to guess that it is bigger."""
    tech = layout.tech_constants()
    folded = layout.floorplan(
        circuits.folded_cascode_devices(circuits.defaults(CIRCUIT)), tech)
    ota = layout.floorplan(
        circuits.ota_devices(circuits.defaults("ota_5t")), tech)
    assert folded["area_um2"] > ota["area_um2"]
