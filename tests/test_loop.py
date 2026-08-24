"""V0.3: the loop gain measurement and the two-output run behind it.

measure_loop is proved against synthetic two-pole transfer functions whose
crossover and phase margin are exact closed forms, so the numerics are pinned
without a simulator. Only the tests at the bottom start a real ngspice.
"""

import math
import os
import tempfile

import pytest

from spice import circuits
from spice.runner import (
    NgspiceParseError,
    NgspiceRunError,
    compute_bode,
    find_ngspice,
    measure_loop,
    parse_wrdata_complex,
    run_ac_multi,
)


def two_pole_rows(t0, f1, f2, fstart, fstop, per_decade=20):
    """Sample T(s) = t0 / ((1 + s/w1)(1 + s/w2)) the way wrdata would."""
    w1 = 2.0 * math.pi * f1
    w2 = 2.0 * math.pi * f2
    decades = math.log10(fstop / fstart)
    count = int(round(decades * per_decade)) + 1

    rows = []
    for i in range(count):
        f = fstart * 10.0 ** (i * decades / (count - 1))
        w = 2.0 * math.pi * f
        h = t0 / ((1.0 + 1j * w / w1) * (1.0 + 1j * w / w2))
        rows.append((f, h.real, h.imag))
    return rows


def two_pole_expected(t0, f1, f2):
    """The exact crossover and phase margin of that loop."""
    w1 = 2.0 * math.pi * f1
    w2 = 2.0 * math.pi * f2
    a = 1.0 / (w1 * w1 * w2 * w2)
    b = 1.0 / (w1 * w1) + 1.0 / (w2 * w2)
    c = 1.0 - t0 * t0
    wc = math.sqrt((-b + math.sqrt(b * b - 4.0 * a * c)) / (2.0 * a))
    pm = 180.0 - math.degrees(math.atan(wc / w1)) - math.degrees(math.atan(wc / w2))
    return wc / (2.0 * math.pi), pm


# ---- measure_loop against closed forms --------------------------------------


@pytest.mark.parametrize(
    "t0,f1,f2",
    [
        (9090.9, 10.0, 1e5),   # the shipped default: PM near 54 degrees
        (9090.9, 10.0, 1e6),   # second pole far out: PM near 85 degrees
        (9090.9, 10.0, 1e4),   # second pole close in: PM under 20 degrees
        (100.0, 1000.0, 5e4),  # low-gain loop
    ],
)
def test_measure_loop_recovers_the_closed_form(t0, f1, f2):
    fc_exact, pm_exact = two_pole_expected(t0, f1, f2)
    bode = compute_bode(two_pole_rows(t0, f1, f2, fc_exact / 1e3, fc_exact * 10))
    measured = measure_loop(bode)

    assert measured["f_crossover"] == pytest.approx(fc_exact, rel=0.005)
    assert measured["phase_margin"] == pytest.approx(pm_exact, abs=0.2)
    assert measured["phase_at_crossover"] == pytest.approx(pm_exact - 180.0, abs=0.2)


def test_measure_loop_reports_the_dc_loop_gain():
    bode = compute_bode(two_pole_rows(1000.0, 100.0, 1e5, 0.1, 1e6))
    measured = measure_loop(bode)
    assert measured["loop_gain_db"] == pytest.approx(60.0, abs=0.01)


def test_a_loop_without_gain_is_refused_with_direction():
    bode = compute_bode(two_pole_rows(0.5, 10.0, 1e5, 1.0, 1e6))
    with pytest.raises(NgspiceParseError) as excinfo:
        measure_loop(bode)
    assert "no crossover" in str(excinfo.value)


def test_a_sweep_that_stops_above_zero_db_is_refused():
    # Plenty of gain, but the sweep ends long before the crossing.
    bode = compute_bode(two_pole_rows(9090.9, 10.0, 1e5, 0.1, 100.0))
    with pytest.raises(NgspiceParseError) as excinfo:
        measure_loop(bode)
    assert "not bracketed" in str(excinfo.value)


# ---- the two-pole circuit's own closed forms ---------------------------------


def test_twopole_crossover_matches_the_quadratic_root():
    params = circuits.defaults("twopole_amp")
    t0 = params["a0"] * circuits.twopole_beta(params)
    f1 = params["gbw"] / params["a0"]
    fc_exact, pm_exact = two_pole_expected(t0, f1, params["fp2"])

    assert circuits.twopole_crossover(params) == pytest.approx(fc_exact, rel=1e-9)
    assert circuits.twopole_phase_margin(params) == pytest.approx(pm_exact, abs=1e-9)


def test_a_loop_that_never_reaches_unity_is_a_400_class_error():
    params = dict(circuits.defaults("twopole_amp"), rin=1.0, rf=1e9, a0=100.0)
    with pytest.raises(circuits.CircuitInputError) as excinfo:
        circuits.twopole_crossover(params)
    assert "never crosses 0 dB" in str(excinfo.value)


# ---- the netlist: two networks, two files, one sweep -------------------------


def build_lines():
    params = circuits.defaults("twopole_amp")
    fstart, fstop = circuits.sweep_range(circuits.twopole_frame(params))
    return circuits.build_twopole_amp(
        params, fstart, fstop, ["C:/temp/closed.data", "C:/temp/loop.data"]
    ).splitlines()


def test_twopole_netlist_writes_both_responses_from_one_sweep():
    lines = build_lines()
    sweeps = [line for line in lines if line.startswith("ac dec ")]
    wrdata = [line for line in lines if line.startswith("wrdata ")]
    assert len(sweeps) == 1
    assert len(wrdata) == 2
    assert wrdata[0].endswith(" v(out)")
    assert wrdata[1].endswith(" v(lg)")


def test_twopole_netlist_carries_two_separate_networks():
    lines = build_lines()
    # The closed loop and its drive.
    assert "V1 in 0 AC 1" in lines
    assert any(line.startswith("Rf vm out ") for line in lines)
    # The broken loop, its injection, and the return-ratio source.
    assert "Vinj lx 0 AC 1" in lines
    assert "Eloop lg 0 0 lr 1" in lines
    # No node is shared between the two networks except ground.
    closed_nodes = {"in", "vm", "out", "pa", "pb", "pc", "pd"}
    loop_nodes = {"lx", "lo", "lr", "lg", "qa", "qb", "qc", "qd"}
    assert not closed_nodes & loop_nodes


def test_twopole_netlist_is_ascii():
    params = circuits.defaults("twopole_amp")
    fstart, fstop = circuits.sweep_range(circuits.twopole_frame(params))
    circuits.build_twopole_amp(
        params, fstart, fstop, ["C:/t/a.data", "C:/t/b.data"]
    ).encode("ascii")


# ---- live: run_ac_multi against a real ngspice --------------------------------


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(),
    reason="ngspice is not available on this machine, so the live loop "
           "measurement cannot run",
)


def reserve():
    handle, path = tempfile.mkstemp(suffix=".data", prefix="faradaem_")
    os.close(handle)
    os.unlink(path)
    return path


@requires_ngspice
def test_run_ac_multi_returns_every_file_in_order():
    paths = [reserve(), reserve()]
    netlist = "\n".join([
        "* two files from one sweep",
        "V1 in 0 AC 1",
        "R1 in out 1000",
        "C1 out 0 1.59e-7",
        ".control",
        "ac dec 10 10 100000",
        "wrdata " + paths[0].replace("\\", "/") + " v(out)",
        "wrdata " + paths[1].replace("\\", "/") + " v(in)",
        "quit",
        ".endc",
        ".end",
    ]) + "\n"

    texts = run_ac_multi(netlist, paths)
    out_points = parse_wrdata_complex(texts[0])
    in_points = parse_wrdata_complex(texts[1])

    assert len(out_points) == len(in_points)
    # v(in) is the source: unity everywhere. v(out) rolls off. That ordering
    # only holds if the files came back in the order they were asked for.
    assert abs(in_points[-1][1]) == pytest.approx(1.0, abs=1e-6)
    assert math.hypot(out_points[-1][1], out_points[-1][2]) < 0.1
    # And both temp files are gone.
    assert not os.path.exists(paths[0])
    assert not os.path.exists(paths[1])


@requires_ngspice
def test_run_ac_multi_names_the_missing_file():
    present = reserve()
    never_written = reserve()
    netlist = "\n".join([
        "* only one wrdata",
        "V1 in 0 AC 1",
        "R1 in out 1000",
        "C1 out 0 1.59e-7",
        ".control",
        "ac dec 5 10 1000",
        "wrdata " + present.replace("\\", "/") + " v(out)",
        "quit",
        ".endc",
        ".end",
    ]) + "\n"

    with pytest.raises(NgspiceRunError) as excinfo:
        run_ac_multi(netlist, [present, never_written])
    assert never_written in str(excinfo.value)
    assert not os.path.exists(present)


@requires_ngspice
def test_live_twopole_agrees_with_its_own_closed_forms():
    params = circuits.defaults("twopole_amp")
    result = circuits.simulate("twopole_amp", params)
    analytic = result["analytic"]

    assert result["phase_margin"] == pytest.approx(analytic["pm"], abs=0.1)
    assert result["f_crossover"] == pytest.approx(analytic["fc"], rel=0.005)
    assert result["loop_gain_db"] == pytest.approx(analytic["loop_dc"], abs=0.1)
    assert result["midband_db"] == pytest.approx(analytic["midband"], abs=0.05)
