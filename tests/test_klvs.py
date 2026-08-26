"""KLayout's LVS engine over Faradaem's geometry, and the proof it can
refuse.

The declaration of what the layers mean is the trust boundary, so most of
these tests attack it: a missing via, a wrong netlist, a wrong value.
An engine that matched any of those would be reassurance, not
verification. Everything here skips cleanly when the engine is absent.
"""

import pytest

from spice import circuits, klvs, layout

requires_engine = pytest.mark.skipif(
    not klvs.available() or not layout.tech_available(),
    reason="KLayout's Python package and the PDK are needed",
)

LAID_OUT = [item["id"] for item in circuits.catalog()
            if circuits.has_floorplan(item["id"])]


# ---------------------------------------------------------------------------
# it refuses rather than pretending
# ---------------------------------------------------------------------------


def test_a_missing_engine_is_an_error_and_never_a_pass():
    source = open(klvs.__file__, encoding="utf-8").read()
    assert "class KlvsError" in source
    assert "refusal, not a pass" in source


def test_the_result_states_the_trust_boundary():
    source = open(klvs.__file__, encoding="utf-8").read()
    assert "trust boundary" in source
    # And it never claims to be the PDK's own runset.
    assert "not the PDK's own LVS runset" in source


def test_the_comparison_netlist_writes_metre_suffixes():
    """A plain M line is read in metres. Bare microns from the X line
    would become ten-metre transistors and match nothing."""
    if not layout.tech_available():
        pytest.skip("the PDK is needed to build the netlist")
    text = klvs.cell_netlist("ota_5t", circuits.defaults("ota_5t"))
    assert "W=10U" in text
    assert "L=0.5U" in text
    # External elements are not devices of the cell.
    assert "Ib" not in text
    assert "CL" not in text


# ---------------------------------------------------------------------------
# and when the engine is there, it verifies
# ---------------------------------------------------------------------------


@requires_engine
@pytest.mark.parametrize("circuit_id", LAID_OUT)
def test_the_extracted_netlist_matches_the_circuit(circuit_id):
    """Devices recognised from geometry, sizes measured from it, matched
    by topology. The whole point of installing the engine."""
    result = klvs.compare(circuit_id, circuits.defaults(circuit_id))
    assert result["match"], result["log"][:8]


@requires_engine
def test_the_engine_extracts_the_passives_with_their_values():
    """The drawn resistor and capacitor come back as devices, valued off
    the geometry: sheet resistance times squares, plate constant times
    area. Nothing tells the engine the values the netlist wanted."""
    result = klvs.compare("opamp_two_stage",
                          circuits.defaults("opamp_two_stage"))
    assert result["match"]
    assert "device res" in result["extracted"]
    assert "device cap" in result["extracted"]
    assert "R=2000.3" in result["extracted"]


@requires_engine
def test_the_wrong_netlist_is_refused():
    """The op-amp's geometry against the OTA's netlist. Same process,
    same layers, different circuit."""
    shapes = circuits.layout_shapes("opamp_two_stage",
                                    circuits.defaults("opamp_two_stage"))
    result = klvs.compare("ota_5t", circuits.defaults("ota_5t"),
                          shapes=shapes)
    assert not result["match"]
    assert result["log"]


@requires_engine
def test_a_missing_via_is_refused():
    """One cut removed from eight hundred shapes: the geometry looks the
    same and is a different circuit."""
    layers = layout.gds_layers()
    shapes = list(circuits.layout_shapes("ota_5t",
                                         circuits.defaults("ota_5t")))
    for index, shape in enumerate(shapes):
        if shape[:2] == layers["VIA1"]:
            del shapes[index]
            break
    result = klvs.compare("ota_5t", circuits.defaults("ota_5t"),
                          shapes=shapes)
    assert not result["match"]


@requires_engine
def test_a_wrong_width_is_refused():
    """The engine measures widths off the gates. Draw at one width and
    claim another, and the value comparison says no."""
    shapes = circuits.layout_shapes("ota_5t", circuits.defaults("ota_5t"))
    lied = dict(circuits.defaults("ota_5t"), wpair=2e-5)   # drawn at 1e-5
    result = klvs.compare("ota_5t", lied, shapes=shapes)
    assert not result["match"]


# ---------------------------------------------------------------------------
# the wires, priced in ohms by the same engine
# ---------------------------------------------------------------------------


@requires_engine
def test_the_routing_resistance_is_measured_and_itemised():
    tech = layout.tech_constants()
    params = circuits.defaults("ota_5t")
    plan = layout.floorplan(circuits.ota_devices(params), tech)
    routed = layout.route(plan, circuits.circuit_nets("ota_5t", params),
                          tech)
    priced = klvs.routing_resistance(routed, tech)

    assert priced
    for net, entry in priced.items():
        # Two cuts down and two up on any pin-to-pin path.
        assert entry["vias_ohms"] == pytest.approx(
            2.0 * (tech["mcon_res"] + tech["via1_res"]))
        assert entry["worst_ohms"] > entry["vias_ohms"]
        assert entry["stub_squares"] > 0.0


@requires_engine
def test_a_longer_wire_costs_more_ohms():
    """The squares come off the drawn rectangles, so a net that spans the
    row must beat one that joins neighbours."""
    tech = layout.tech_constants()
    params = circuits.defaults("opamp_two_stage")
    plan = layout.floorplan(
        circuits.opamp_devices(params), tech,
        passives=circuits.drawable_passives("opamp_two_stage", params))
    routed = layout.route(
        plan, circuits.circuit_nets("opamp_two_stage", params), tech)
    priced = klvs.routing_resistance(routed, tech)

    # zx reaches the far tab of a capacitor hundreds of microns long.
    assert priced["zx"]["worst_ohms"] > 2.0 * priced["tail"]["worst_ohms"]


@requires_engine
def test_run_layout_reports_the_engine_and_the_ohms():
    result = circuits.run_layout("ota_5t", circuits.defaults("ota_5t"))
    assert result["klvs"]["match"] is True
    assert result["resistance"]
    worst = max(entry["worst_ohms"]
                for entry in result["resistance"].values())
    assert 1.0 < worst < 1e4
