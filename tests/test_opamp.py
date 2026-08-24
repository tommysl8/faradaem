"""V0.4: the SKY130 two-stage op-amp and its open-loop measurement.

The netlist-shape tests need the PDK on disk only to resolve the library path;
the live tests at the bottom need a real ngspice as well. Both skip cleanly,
the same way as every other PDK test.
"""

import pytest

from spice import circuits
from spice.runner import find_ngspice, sky130_available

CIRCUIT_ID = "opamp_two_stage"

requires_pdk = pytest.mark.skipif(
    not sky130_available(),
    reason="the SKY130 model library is not installed, so op-amp netlists "
           "cannot be built or run on this machine",
)


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_live_pdk = pytest.mark.skipif(
    ngspice_missing() or not sky130_available(),
    reason="a real ngspice and the SKY130 model library are both needed to "
           "measure the op-amp",
)


def build_lines(**overrides):
    params = dict(circuits.defaults(CIRCUIT_ID), **overrides)
    circuit = circuits.get_circuit(CIRCUIT_ID)
    fstart, fstop = circuits.sweep_range(
        circuit["centre"](params), circuit["decades"]
    )
    return circuits.build_opamp_two_stage(
        params, fstart, fstop, ["C:/temp/loop.data"]
    ).splitlines()


# ---- netlist shape ---------------------------------------------------------


@requires_pdk
def test_all_eight_devices_are_subcircuits():
    lines = build_lines()
    devices = [line for line in lines if line.startswith("XM")]
    assert len(devices) == 8
    assert all(("nfet_01v8" in line) or ("pfet_01v8" in line) for line in devices)


@requires_pdk
def test_the_feedback_goes_to_the_diode_side_gate():
    """M1 carries the mirror diode, so its gate is the inverting input."""
    lines = build_lines()
    m1 = [line for line in lines if line.startswith("XM1 ")][0]
    m2 = [line for line in lines if line.startswith("XM2 ")][0]
    assert " inn " in m1
    assert " inp " in m2
    assert "Lfb out inn 1e9" in lines


@requires_pdk
def test_the_ac_drive_is_isolated_behind_the_servo_capacitor():
    lines = build_lines()
    assert "Vs ac 0 DC 0 AC 1" in lines
    assert "Cin ac inn 1e9" in lines
    # The written vector is the sign-corrected open-loop response.
    assert "Einv lg 0 0 out 1" in lines
    wrdata = [line for line in lines if line.startswith("wrdata ")]
    assert len(wrdata) == 1
    assert wrdata[0].endswith(" v(lg)")


@requires_pdk
def test_the_operating_point_is_taken_in_the_same_run():
    lines = build_lines()
    assert "print v(out) i(vdd)" in lines
    assert any(line.startswith(".nodeset ") for line in lines)


@requires_pdk
def test_compensation_network_follows_the_parameters():
    lines = build_lines(cc=3e-12, rz=1500.0)
    assert "Rz d2 zx 1500" in lines
    assert "Cc zx out 3e-12" in lines


@requires_pdk
def test_the_sweep_spans_the_wider_declared_window():
    lines = build_lines()
    sweep = [line for line in lines if line.startswith("ac dec ")][0]
    fields = sweep.split()
    fstart, fstop = float(fields[3]), float(fields[4])
    assert fstart < 10.0
    assert fstop > 1e8


@requires_pdk
def test_opamp_netlist_is_ascii():
    "\n".join(build_lines()).encode("ascii")


# ---- the rail check needs no simulator -------------------------------------


def test_a_railed_output_is_reported_as_a_sizing_problem():
    stdout = "v(out) = 1.750000e+00\ni(vdd) = -1.0e-04\n"
    with pytest.raises(circuits.CircuitInputError) as excinfo:
        circuits.measure_opamp_two_stage([None], {}, stdout)
    message = str(excinfo.value)
    assert "1.750" in message
    assert "does not bias" in message
    assert "W6" in message and "W7" in message


def test_presets_stay_inside_the_declared_ranges():
    circuit = circuits.get_circuit(CIRCUIT_ID)
    specs = {spec["key"]: spec for spec in circuit["params"]}
    assert len(circuit["presets"]) == 6
    for item in circuit["presets"]:
        assert set(item["params"]) == set(specs), item["label"]
        for key, value in item["params"].items():
            assert specs[key]["min"] <= value <= specs[key]["max"], (item["label"], key)


def test_the_design_block_is_consistent_with_the_circuit():
    circuit = circuits.get_circuit(CIRCUIT_ID)
    block = circuit["design"]
    param_keys = {spec["key"] for spec in circuit["params"]}
    assert set(block["tunable"]) <= param_keys

    shown = {circuit["readout"]["headline"]["key"]}
    shown.update(stat["key"] for stat in circuit["readout"]["stats"])
    for item in block["goals"]:
        # Every goal is a number the readout actually shows the user.
        assert item["key"] in shown, item["key"]
        assert item["op"] in (">=", "<=")
        assert item["default"] > 0


def test_the_circuit_claims_a_pdk_sized_timeout():
    assert circuits.get_circuit(CIRCUIT_ID)["timeout_s"] >= 60.0


# ---- live ------------------------------------------------------------------


@pytest.fixture(scope="module")
def live():
    return circuits.simulate(CIRCUIT_ID, circuits.defaults(CIRCUIT_ID))


@requires_live_pdk
def test_the_hand_design_meets_its_own_default_spec(live):
    """The V0.4 deliverable: the shipped sizing is a working op-amp."""
    assert live["loop_gain_db"] >= 60.0
    assert live["f_crossover"] >= 5e6
    assert live["phase_margin"] >= 60.0
    assert live["power"] <= 2e-4


@requires_live_pdk
def test_the_servo_holds_the_output_at_mid_supply(live):
    assert live["out_dc"] == pytest.approx(0.9, abs=0.05)


@requires_live_pdk
def test_the_open_loop_curve_is_what_is_plotted(live):
    # The plot starts at the flat open-loop gain and ends far below 0 dB.
    assert live["mag_db"][0] == pytest.approx(live["loop_gain_db"], abs=0.2)
    assert live["mag_db"][-1] < -20.0
    assert live["analytic"] == {}


@requires_live_pdk
def test_removing_the_nulling_resistor_costs_phase_margin(live):
    params = dict(circuits.defaults(CIRCUIT_ID), rz=1e-3)
    bare = circuits.simulate(CIRCUIT_ID, params)
    # The right-half-plane zero is real physics: taking Rz away must hurt.
    assert bare["phase_margin"] < live["phase_margin"] - 10.0
