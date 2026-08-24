"""V0.2: the SKY130 PDK and the NFET common-source amplifier.

Two kinds of test live here.  Everything above the live-run marker is pure --
path resolution, netlist shape, bias classification -- and runs on any machine.
Everything below it drives a real ngspice against a real model library, and
skips cleanly when either is absent, the same way the V0.0 integration test
skips when no simulator is installed.
"""

import math
import os

import pytest

from spice import circuits
from spice.runner import (
    NgspiceParseError,
    PdkNotFoundError,
    find_ngspice,
    parse_op_values,
    pdk_root,
    sky130_available,
    sky130_lib_path,
    find_sky130_lib,
)

CIRCUIT_ID = "nfet_cs_amp"


# ---------------------------------------------------------------------------
# PDK path resolution
# ---------------------------------------------------------------------------


def test_pdk_root_prefers_the_environment(monkeypatch):
    monkeypatch.setenv("PDK_ROOT", r"D:\somewhere\else")
    assert pdk_root() == r"D:\somewhere\else"


def test_pdk_root_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("PDK_ROOT", raising=False)
    assert pdk_root() == r"C:\pdk"


def test_pdk_root_ignores_a_blank_environment_value(monkeypatch):
    monkeypatch.setenv("PDK_ROOT", "   ")
    assert pdk_root() == r"C:\pdk"


def test_library_path_sits_under_the_root(monkeypatch):
    monkeypatch.setenv("PDK_ROOT", r"D:\pdk")
    path = sky130_lib_path()
    assert path.startswith(r"D:\pdk")
    assert path.endswith("sky130.lib.spice")
    assert "libs.tech" in path


def test_missing_library_names_both_the_variable_and_the_path(monkeypatch):
    monkeypatch.setenv("PDK_ROOT", r"D:\definitely\not\here")
    with pytest.raises(PdkNotFoundError) as excinfo:
        find_sky130_lib()

    message = str(excinfo.value)
    assert "PDK_ROOT" in message
    assert "sky130.lib.spice" in message
    # The copy has to say what to do, not only what went wrong.
    assert "Install the SKY130 PDK" in message


def test_missing_library_says_the_fallback_was_used(monkeypatch):
    monkeypatch.delenv("PDK_ROOT", raising=False)
    monkeypatch.setattr("spice.runner.PDK_ROOT_FALLBACK", r"D:\not\here")
    with pytest.raises(PdkNotFoundError) as excinfo:
        find_sky130_lib()
    assert "is not set in this process" in str(excinfo.value)


def test_unknown_corner_is_refused():
    with pytest.raises(ValueError) as excinfo:
        find_sky130_lib("typo")
    assert "tt" in str(excinfo.value)


def test_availability_tracks_the_file(monkeypatch):
    monkeypatch.setenv("PDK_ROOT", r"D:\not\here")
    assert sky130_available() is False


# ---------------------------------------------------------------------------
# operating-point parsing
# ---------------------------------------------------------------------------


def test_op_values_are_read_by_name():
    stdout = "v(out) = 1.069323e+00\ni(vdd) = -3.65339e-05\n"
    assert parse_op_values(stdout, ("v(out)", "i(vdd)")) == {
        "v(out)": 1.069323,
        "i(vdd)": -3.65339e-05,
    }


def test_op_values_tolerate_ngspice_spacing_and_case():
    stdout = "V ( OUT )   =    1.5\n"
    assert parse_op_values(stdout, ("v(out)",)) == {"v(out)": 1.5}


def test_op_values_take_the_last_printing():
    stdout = "v(out) = 1.0\nv(out) = 2.0\n"
    assert parse_op_values(stdout, ("v(out)",)) == {"v(out)": 2.0}


def test_op_values_name_what_was_missing():
    with pytest.raises(NgspiceParseError) as excinfo:
        parse_op_values("v(out) = 1.0\n", ("v(out)", "i(vdd)"))
    assert "i(vdd)" in str(excinfo.value)


# ---------------------------------------------------------------------------
# netlist shape
# ---------------------------------------------------------------------------


def build_lines(**overrides):
    params = dict(circuits.defaults(CIRCUIT_ID), **overrides)
    fstart, fstop = circuits.sweep_range(circuits.cs_amp_pole(params))
    text = circuits.build_nfet_cs_amp(params, fstart, fstop, "C:/temp/out.data")
    return text.splitlines()


requires_pdk = pytest.mark.skipif(
    not sky130_available(),
    reason="the SKY130 model library is not installed, so PDK netlists "
           "cannot be built or run on this machine",
)


@requires_pdk
def test_device_is_instantiated_as_a_subcircuit():
    # SKY130 devices are subcircuits: a plain M line will not resolve.
    device = [line for line in build_lines() if line.startswith("XM1 ")][0]
    assert circuits.NFET_MODEL in device
    assert device.startswith("XM1 out g 0 0 ")


@requires_pdk
def test_lib_line_uses_forward_slashes_and_a_corner():
    lib = [line for line in build_lines() if line.startswith(".lib ")][0]
    assert "\\" not in lib
    assert lib.endswith(" tt")
    assert lib.endswith("sky130.lib.spice tt")


@requires_pdk
def test_width_and_length_are_emitted_in_microns():
    # The catalogue carries lengths in metres; SKY130 subcircuits want microns.
    device = [line for line in build_lines(w=1e-6, l=1.5e-7) if line.startswith("XM1 ")][0]
    assert "W=1 " in device
    assert device.endswith("L=0.15")


@requires_pdk
def test_micron_conversion_does_not_leak_float_noise():
    device = [line for line in build_lines(w=4.2e-7, l=2.5e-7) if line.startswith("XM1 ")][0]
    assert "W=0.42 " in device
    assert device.endswith("L=0.25")


@requires_pdk
def test_gate_source_carries_both_bias_and_excitation():
    gate = [line for line in build_lines(vgs=0.9) if line.startswith("Vg ")][0]
    assert gate == "Vg g 0 DC 0.9 AC 1"


@requires_pdk
def test_control_block_runs_the_operating_point_before_the_sweep():
    lines = build_lines()
    assert lines.index("op") < [
        index for index, line in enumerate(lines) if line.startswith("ac dec ")
    ][0]
    assert "print v(out) i(vdd)" in lines
    assert lines[-1] == ".end"


# ---------------------------------------------------------------------------
# the control block stays backward compatible for circuits without an op
# ---------------------------------------------------------------------------


def test_control_block_omits_the_operating_point_by_default():
    block = circuits.ac_control_block(10.0, 1000.0, "C:/temp/x.data")
    assert "op" not in block
    assert block[1].startswith("ac dec ")


def test_control_block_adds_the_operating_point_on_request():
    block = circuits.ac_control_block(
        10.0, 1000.0, "C:/temp/x.data", op_prints=["v(out)"]
    )
    assert block[1] == "op"
    assert block[2] == "print v(out)"


# ---------------------------------------------------------------------------
# bias classification
# ---------------------------------------------------------------------------


def test_a_healthy_bias_carries_no_caution():
    assert circuits.cs_amp_bias_note(1.07, 1.8) is None


def test_a_bottomed_out_drain_says_which_way_to_move():
    note = circuits.cs_amp_bias_note(0.03, 1.8)
    assert "triode" in note
    assert "Lower Vgs or RD" in note


def test_a_drain_stuck_at_the_rail_says_which_way_to_move():
    note = circuits.cs_amp_bias_note(1.79, 1.8)
    assert "barely conducting" in note
    assert "Raise Vgs" in note


# ---------------------------------------------------------------------------
# catalogue wiring
# ---------------------------------------------------------------------------


def test_the_circuit_is_registered_and_last_in_order():
    assert circuits.CIRCUIT_ORDER[-1] == CIRCUIT_ID
    assert circuits.get_circuit(CIRCUIT_ID)["analysis"] == "ac"


def test_the_circuit_ships_no_analytic_check_on_purpose():
    # Square law does not describe a 150 nm device. Shipping a check that is
    # permanently tens of percent out would read as a fault in the simulator,
    # so the operating point is reported instead.
    circuit = circuits.get_circuit(CIRCUIT_ID)
    assert circuit["checks"] == []
    assert circuit["readout"]["headline"]["check"] is None
    assert all(stat["check"] is None for stat in circuit["readout"]["stats"])


def test_the_circuit_claims_a_pdk_sized_timeout():
    # The first library load costs 10 to 30 s before any solving starts.
    assert circuits.get_circuit(CIRCUIT_ID)["timeout_s"] >= 60.0


def test_presets_stay_inside_the_declared_parameter_ranges():
    circuit = circuits.get_circuit(CIRCUIT_ID)
    specs = {spec["key"]: spec for spec in circuit["params"]}
    for item in circuit["presets"]:
        assert set(item["params"]) == set(specs), item["label"]
        for key, value in item["params"].items():
            assert specs[key]["min"] <= value <= specs[key]["max"], (item["label"], key)


def test_sweep_framing_brackets_the_rd_cl_pole():
    params = circuits.defaults(CIRCUIT_ID)
    centre = circuits.cs_amp_pole(params)
    assert centre == pytest.approx(
        1.0 / (2.0 * math.pi * params["rd"] * params["cl"]), rel=1e-9
    )
    fstart, fstop = circuits.sweep_range(centre)
    assert fstart < centre < fstop


# ---------------------------------------------------------------------------
# live runs: real ngspice against the real model library
# ---------------------------------------------------------------------------


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_live_pdk = pytest.mark.skipif(
    ngspice_missing() or not sky130_available(),
    reason="a real ngspice and the SKY130 model library are both needed to run "
           "the device simulation",
)


@pytest.fixture(scope="module")
def live():
    return circuits.simulate(CIRCUIT_ID, circuits.defaults(CIRCUIT_ID))


@requires_live_pdk
def test_the_device_actually_amplifies(live):
    # Roughly 4.5 V/V at the shipped defaults. Wide bounds: this is a real
    # device model, and the point is that it amplifies, not that it hits a
    # number the test author chose.
    assert 8.0 < live["midband_db"] < 18.0


@requires_live_pdk
def test_the_operating_point_sits_in_saturation(live):
    assert 0.3 < live["drain_voltage"] < 1.6
    assert 1e-6 < live["drain_current"] < 1e-3
    assert live["power"] == pytest.approx(1.8 * live["drain_current"], rel=1e-9)


@requires_live_pdk
def test_the_bandwidth_is_set_by_the_device_not_by_rd_cl(live):
    # The whole reason this circuit has no analytic check: the real pole is
    # RD in parallel with the device output resistance, which lands it well
    # above 1/(2*pi*RD*CL).
    ideal = circuits.cs_amp_pole(circuits.defaults(CIRCUIT_ID))
    assert live["f3db"] > ideal * 1.1


@requires_live_pdk
def test_a_healthy_default_bias_reports_no_caution(live):
    assert "note" not in live


@requires_live_pdk
def test_the_sweep_returns_a_full_curve(live):
    assert len(live["freq"]) == len(live["mag_db"]) == len(live["phase_deg"])
    assert len(live["freq"]) > 100
    assert live["analytic"] == {}


@requires_live_pdk
def test_a_device_that_is_off_reports_a_bias_problem():
    params = dict(circuits.defaults(CIRCUIT_ID), vgs=0.3)
    with pytest.raises(circuits.BiasError) as excinfo:
        circuits.simulate(CIRCUIT_ID, params)

    message = str(excinfo.value)
    assert "not amplifying" in message
    assert "Raise Vgs" in message
    # The measured bias has to be in the message, or there is nothing to act on.
    assert "Vds =" in message


@requires_live_pdk
def test_a_bottomed_out_stage_still_measures_but_warns():
    params = dict(circuits.defaults(CIRCUIT_ID), vgs=1.2, w=4e-6)
    result = circuits.simulate(CIRCUIT_ID, params)
    assert result["drain_voltage"] < circuits.CS_TRIODE_VDS
    assert "triode" in result["note"]


@requires_live_pdk
def test_live_pdk_runs_leave_no_temp_files_behind():
    import glob
    import tempfile

    circuits.simulate(CIRCUIT_ID, circuits.defaults(CIRCUIT_ID))
    leftovers = glob.glob(os.path.join(tempfile.gettempdir(), "faradaem_*"))
    assert leftovers == []
