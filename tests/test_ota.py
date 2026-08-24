"""V0.8: the 5T OTA, the second amplifier topology."""

import pytest

from spice import circuits
from spice.runner import find_ngspice, sky130_available

CIRCUIT_ID = "ota_5t"

requires_pdk = pytest.mark.skipif(
    not sky130_available(),
    reason="the SKY130 model library is not installed",
)


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_live_pdk = pytest.mark.skipif(
    ngspice_missing() or not sky130_available(),
    reason="a real ngspice and the SKY130 model library are both needed",
)


def build_lines(**overrides):
    params = dict(circuits.defaults(CIRCUIT_ID), **overrides)
    circuit = circuits.get_circuit(CIRCUIT_ID)
    fstart, fstop = circuits.sweep_range(circuit["centre"](params), circuit["decades"])
    return circuits.build_ota_5t(params, fstart, fstop, ["C:/temp/loop.data"]).splitlines()


@requires_pdk
def test_five_transistors_and_the_bias_diode():
    # The five-transistor core plus the bias diode M8.
    devices = [line for line in build_lines() if line.startswith("XM")]
    assert len(devices) == 6


@requires_pdk
def test_single_stage_feedback_goes_to_the_non_diode_gate():
    """One inversion in the loop, so M2's gate is the inverting input."""
    lines = build_lines()
    m1 = [line for line in lines if line.startswith("XM1 ")][0]
    m2 = [line for line in lines if line.startswith("XM2 ")][0]
    assert " inp " in m1        # non-inverting: the diode side
    assert " inn " in m2        # inverting: takes the servo feedback
    assert "Lfb out inn 1e9" in lines
    assert "Cin ac inn 1e9" in lines
    assert "Einv lg 0 0 out 1" in lines


@requires_pdk
def test_the_output_is_the_second_drain():
    lines = build_lines()
    m2 = [line for line in lines if line.startswith("XM2 ")][0]
    m4 = [line for line in lines if line.startswith("XM4 ")][0]
    assert m2.startswith("XM2 out ")
    assert m4.startswith("XM4 out ")


@requires_pdk
def test_ota_netlist_is_ascii():
    "\n".join(build_lines()).encode("ascii")


def test_a_railed_output_is_a_sizing_problem():
    stdout = "v(out) = 0.100000e+00\ni(vdd) = -1.0e-05\n"
    with pytest.raises(circuits.CircuitInputError) as excinfo:
        circuits.measure_ota_5t([None], {}, stdout)
    assert "does not bias" in str(excinfo.value)


def test_presets_and_design_block_are_consistent():
    circuit = circuits.get_circuit(CIRCUIT_ID)
    specs = {spec["key"]: spec for spec in circuit["params"]}
    assert len(circuit["presets"]) == 6
    for item in circuit["presets"]:
        assert set(item["params"]) == set(specs), item["label"]
        for key, value in item["params"].items():
            assert specs[key]["min"] <= value <= specs[key]["max"], (item["label"], key)

    block = circuit["design"]
    assert set(block["tunable"]) <= set(specs)
    shown = {circuit["readout"]["headline"]["key"]}
    shown.update(stat["key"] for stat in circuit["readout"]["stats"])
    for item in block["goals"]:
        assert item["key"] in shown


def test_the_seed_scales_bias_with_the_budget():
    from spice import design
    seeded, targets = design.seed_params(
        CIRCUIT_ID, {"power": 5e-5}, circuits.defaults(CIRCUIT_ID)
    )
    assert seeded["ibias"] == pytest.approx(1e-5)
    # A high gain target reaches for the longer channel.
    high, _ = design.seed_params(
        CIRCUIT_ID, {"loop_gain_db": 42.0}, circuits.defaults(CIRCUIT_ID)
    )
    assert high["l"] == pytest.approx(1e-6)


@pytest.fixture(scope="module")
def live():
    return circuits.simulate(CIRCUIT_ID, circuits.defaults(CIRCUIT_ID))


@requires_live_pdk
def test_the_base_sizing_meets_its_own_default_goals(live):
    assert live["loop_gain_db"] >= 35.0
    assert live["f_crossover"] >= 5e6
    assert live["phase_margin"] >= 60.0
    assert live["power"] <= 1e-4


@requires_live_pdk
def test_the_single_stage_carries_far_more_margin_than_the_two_stage(live):
    """The topology trade the strategist selects on, measured."""
    two_stage = circuits.simulate(
        "opamp_two_stage", circuits.defaults("opamp_two_stage")
    )
    assert live["phase_margin"] > two_stage["phase_margin"] + 10.0
    assert two_stage["loop_gain_db"] > live["loop_gain_db"] + 20.0


@requires_live_pdk
def test_the_servo_holds_the_output_near_mid_supply(live):
    assert live["out_dc"] == pytest.approx(0.9, abs=0.05)
