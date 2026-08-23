"""V0.1: RC low-pass netlist, wrdata parsing, Bode math, and a live AC sweep.

The Bode maths is proved against a synthetic H = 1/(1 + j f/fc) generated here,
so the numerical behaviour is pinned without needing a simulator. Only the
final test actually runs ngspice.
"""

import cmath
import math

import pytest

from spice.runner import (
    NgspiceParseError,
    build_rc_lowpass_netlist,
    compute_bode,
    find_ngspice,
    measure_lowpass,
    parse_wrdata_complex,
    simulate_rc_lowpass,
    unwrap_degrees,
)

FC = 1000.0


def synthetic_points(fc=FC, fstart=1.0, fstop=1e6, per_decade=20):
    """Rows shaped like wrdata output for an ideal one-pole low-pass."""
    decades = math.log10(fstop / fstart)
    count = int(round(decades * per_decade)) + 1
    points = []
    for index in range(count):
        frequency = fstart * 10.0 ** (index * decades / (count - 1))
        response = 1.0 / (1.0 + 1j * frequency / fc)
        points.append((frequency, response.real, response.imag))
    return points


# ---- netlist builder ----------------------------------------------------


def netlist_lines(**kwargs):
    defaults = {
        "r": 1000,
        "c": 1.59e-7,
        "fstart": 1.0,
        "fstop": 1e6,
        "points_per_decade": 20,
        "out_path": r"C:\Users\tommy\AppData\Local\Temp\faradaem_ac.data",
    }
    defaults.update(kwargs)
    return build_rc_lowpass_netlist(**defaults).splitlines()


def test_title_line_is_a_comment():
    assert netlist_lines()[0].startswith("*")


def test_source_is_a_unit_ac_source():
    assert "V1 in 0 AC 1" in netlist_lines()


def test_series_resistor_and_shunt_capacitor():
    lines = netlist_lines(r=1000, c=1.59e-7)
    assert "R1 in out 1000" in lines
    assert "C1 out 0 1.59e-07" in lines


def test_ac_sweep_command_carries_the_derived_range():
    assert "ac dec 20 1 1000000" in netlist_lines(fstart=1.0, fstop=1e6)


def test_sweep_command_formats_fractional_bounds():
    assert "ac dec 10 0.5 2500.5" in netlist_lines(
        fstart=0.5, fstop=2500.5, points_per_decade=10
    )


def test_wrdata_path_uses_forward_slashes():
    # Backslashes are escape-prone inside an ngspice control block.
    line = [ln for ln in netlist_lines() if ln.startswith("wrdata")][0]
    assert "\\" not in line
    assert line.endswith(" v(out)")
    assert "C:/Users/tommy/AppData/Local/Temp/faradaem_ac.data" in line


def test_control_block_is_ordered_and_ends_with_end():
    lines = netlist_lines()
    order = [".control", "ac dec 20 1 1000000", "quit", ".endc", ".end"]
    positions = [lines.index(item) for item in order]
    assert positions == sorted(positions)
    assert lines[-1] == ".end"
    assert any(line.startswith("wrdata") for line in lines)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_component_values_are_rejected(bad):
    with pytest.raises(ValueError):
        netlist_lines(r=bad)


# ---- wrdata parsing -----------------------------------------------------

CANNED = (
    " 1.00000000e+00  9.99999002e-01 -9.99025467e-04 \n"
    " 1.00000000e+03  7.07451471e-01 -7.06763871e-01 \n"
    " 1.00000000e+06  1.00194892e-06 -1.00097348e-03 \n"
)


def test_canned_file_parses_to_three_rows():
    points = parse_wrdata_complex(CANNED)
    assert len(points) == 3
    assert points[0][0] == pytest.approx(1.0)
    assert points[1][1] == pytest.approx(0.707451471)
    assert points[2][2] == pytest.approx(-1.00097348e-03)


def test_blank_lines_and_trailing_whitespace_are_tolerated():
    noisy = "\n\n" + CANNED.replace("\n", "   \n") + "\n   \n"
    assert len(parse_wrdata_complex(noisy)) == 3


def test_empty_file_raises():
    with pytest.raises(NgspiceParseError):
        parse_wrdata_complex("   \n\n")


@pytest.mark.parametrize(
    "malformed",
    [
        "1.0 2.0\n",
        "1.0 2.0 3.0 4.0\n",
        "1.0 abc 3.0\n",
        "not a row at all\n",
        "1.0 2.0 nan-ish\n",
    ],
)
def test_malformed_row_raises(malformed):
    with pytest.raises(NgspiceParseError):
        parse_wrdata_complex(malformed)


def test_error_names_the_offending_row():
    with pytest.raises(NgspiceParseError) as excinfo:
        parse_wrdata_complex(CANNED + "1.0 2.0\n")
    assert "Row 4" in str(excinfo.value)


# ---- phase unwrap -------------------------------------------------------


def test_unwrap_turns_a_wrapped_sequence_into_a_monotonic_descent():
    wrapped = [0.0, -80.0, -170.0, 175.0, 100.0, 20.0]
    unwrapped = unwrap_degrees(wrapped)
    assert unwrapped[:3] == [0.0, -80.0, -170.0]
    assert unwrapped[3] == pytest.approx(-185.0)
    assert unwrapped[4] == pytest.approx(-260.0)
    assert unwrapped[5] == pytest.approx(-340.0)
    assert all(b <= a for a, b in zip(unwrapped, unwrapped[1:]))


def test_unwrap_leaves_a_continuous_sequence_alone():
    smooth = [0.0, -10.0, -45.0, -80.0, -90.0]
    assert unwrap_degrees(smooth) == smooth


def test_unwrap_handles_empty_and_single_values():
    assert unwrap_degrees([]) == []
    assert unwrap_degrees([-45.0]) == [-45.0]


# ---- Bode maths on synthetic data --------------------------------------


def test_compute_bode_returns_parallel_lists():
    bode = compute_bode(synthetic_points())
    assert len(bode["freq"]) == len(bode["mag_db"]) == len(bode["phase_deg"])


def test_synthetic_dc_gain_is_zero_db():
    bode = compute_bode(synthetic_points())
    assert bode["mag_db"][0] == pytest.approx(0.0, abs=0.05)


def test_synthetic_phase_descends_from_zero_toward_minus_ninety():
    bode = compute_bode(synthetic_points())
    assert bode["phase_deg"][0] == pytest.approx(0.0, abs=0.1)
    assert bode["phase_deg"][-1] == pytest.approx(-90.0, abs=0.1)
    phases = bode["phase_deg"]
    assert all(b <= a + 1e-9 for a, b in zip(phases, phases[1:]))


def test_synthetic_rolloff_is_twenty_db_per_decade():
    bode = compute_bode(synthetic_points())
    freq, mag = bode["freq"], bode["mag_db"]
    high = [i for i, f in enumerate(freq) if f >= 100 * FC]
    decade = mag[high[0]] - mag[high[-1]]
    decades = math.log10(freq[high[-1]] / freq[high[0]])
    assert decade / decades == pytest.approx(20.0, abs=0.2)


def test_measure_lowpass_recovers_the_corner_from_synthetic_data():
    measured = measure_lowpass(compute_bode(synthetic_points()))
    assert measured["f3db"] == pytest.approx(FC, rel=0.005)
    assert measured["dc_gain_db"] == pytest.approx(0.0, abs=0.05)
    assert measured["phase_at_f3db"] == pytest.approx(-45.0, abs=1.0)


@pytest.mark.parametrize("fc", [10.0, 1000.0, 47_000.0])
def test_measure_lowpass_across_several_corners(fc):
    points = synthetic_points(fc=fc, fstart=fc / 1000.0, fstop=fc * 1000.0)
    measured = measure_lowpass(compute_bode(points))
    assert measured["f3db"] == pytest.approx(fc, rel=0.005)
    assert measured["phase_at_f3db"] == pytest.approx(-45.0, abs=1.0)


def test_measure_lowpass_raises_when_the_crossing_is_above_the_sweep():
    # Stop a decade below the corner: the response never falls 3 dB.
    points = synthetic_points(fc=FC, fstart=1.0, fstop=100.0)
    with pytest.raises(NgspiceParseError) as excinfo:
        measure_lowpass(compute_bode(points))
    assert "not bracketed" in str(excinfo.value)


def test_corner_is_measured_relative_to_the_first_swept_point():
    """dc_gain_db is defined as the first point, not as the true DC gain.

    A sweep that starts above the corner is already rolling off, so the
    measurement is 3 dB below that starting point and is NOT the real fc. This
    pins that behaviour so it stays a deliberate choice: simulate_rc_lowpass
    always starts three decades below the corner, where the two coincide.
    """
    measured = measure_lowpass(compute_bode(synthetic_points(fc=FC, fstart=1e5, fstop=1e7)))
    assert measured["dc_gain_db"] == pytest.approx(-40.0, abs=0.1)
    assert measured["f3db"] == pytest.approx(1.41e5, rel=0.02)
    assert measured["f3db"] != pytest.approx(FC, rel=0.5)


def test_measure_lowpass_needs_at_least_two_points():
    with pytest.raises(NgspiceParseError):
        measure_lowpass({"freq": [1.0], "mag_db": [0.0], "phase_deg": [0.0]})


def test_compute_bode_rejects_a_zero_magnitude_point():
    with pytest.raises(NgspiceParseError):
        compute_bode([(1.0, 0.0, 0.0), (2.0, 0.5, -0.5)])


def test_synthetic_matches_the_closed_form_everywhere():
    points = synthetic_points()
    bode = compute_bode(points)
    for frequency, mag_db, phase in zip(bode["freq"], bode["mag_db"], bode["phase_deg"]):
        expected = 1.0 / (1.0 + 1j * frequency / FC)
        assert mag_db == pytest.approx(20.0 * math.log10(abs(expected)), abs=1e-9)
        assert phase == pytest.approx(math.degrees(cmath.phase(expected)), abs=1e-9)


# ---- live ngspice -------------------------------------------------------


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(),
    reason="ngspice is not available on this machine, so the AC sweep cannot run",
)


@pytest.fixture(scope="module")
def swept():
    return simulate_rc_lowpass(1000.0, 1.59e-7)


@requires_ngspice
def test_live_sweep_finds_the_corner(swept):
    assert swept["f3db"] == pytest.approx(1000.9, rel=0.02)


@requires_ngspice
def test_live_sweep_agrees_with_the_analytic_corner(swept):
    assert swept["fc_analytic"] == pytest.approx(1000.9745, rel=1e-4)
    assert swept["f3db"] == pytest.approx(swept["fc_analytic"], rel=0.02)


@requires_ngspice
def test_live_first_point_pins_the_wrdata_column_order(swept):
    """Unity magnitude and zero phase at the bottom of the sweep.

    This only holds if the wrdata columns really are (frequency, real,
    imaginary). Reading them in any other order breaks this assertion, which is
    what makes the column interpretation tested rather than assumed.
    """
    assert swept["mag_db"][0] == pytest.approx(0.0, abs=0.09)
    assert swept["phase_deg"][0] == pytest.approx(0.0, abs=2.0)


@requires_ngspice
def test_live_sweep_shape(swept):
    assert len(swept["freq"]) == len(swept["mag_db"]) == len(swept["phase_deg"])
    assert len(swept["freq"]) > 100
    assert swept["freq"][0] < swept["fc_analytic"] < swept["freq"][-1]
    assert swept["dc_gain_db"] == pytest.approx(0.0, abs=0.09)
    assert swept["phase_at_f3db"] == pytest.approx(-45.0, abs=2.0)


@requires_ngspice
def test_live_phase_descends_toward_minus_ninety(swept):
    phases = swept["phase_deg"]
    assert phases[-1] == pytest.approx(-90.0, abs=1.0)
    assert all(b <= a + 1e-6 for a, b in zip(phases, phases[1:]))


@requires_ngspice
def test_live_sweep_leaves_no_temp_files_behind():
    import glob
    import tempfile

    pattern = tempfile.gettempdir() + "\\faradaem_*"
    before = set(glob.glob(pattern))
    simulate_rc_lowpass(2200.0, 1e-8)
    assert set(glob.glob(pattern)) == before
