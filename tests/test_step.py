"""V1.2: the step response, and what it takes to measure one honestly.

A sweep measures an amplifier held to small signals. A step asks how fast
it can actually move, which is a different question with different
answers, and the first analysis in this project that is not a sweep.

Everything above the live-run marker is pure: synthetic waveforms with a
slope nobody has to guess at, so the measurement can be checked against
arithmetic. Below the marker, a real transient runs against the real
model library and skips cleanly when either is absent.
"""

import pytest

from spice import circuits
from spice.runner import (
    NgspiceParseError,
    find_ngspice,
    measure_step,
    parse_wrdata_real,
    sky130_available,
)

RISE_AT = 250e-9
FALL_AT = 500e-9
WINDOW = 250e-9


def ramp(rate_v_per_us, low=0.75, high=1.05, points=4000, span=750e-9):
    """A two-edge waveform that slews at exactly the given rate.

    Flat, then a straight ramp up to the far value, flat, then a straight
    ramp back. Nothing curved: the point is that the answer is arithmetic.
    """
    rate = rate_v_per_us * 1e6
    series = []
    for index in range(points):
        time = span * index / (points - 1)
        if time < RISE_AT:
            value = low
        elif time < FALL_AT:
            value = min(high, low + rate * (time - RISE_AT))
        else:
            value = max(low, high - rate * (time - FALL_AT))
        series.append((time, value))
    return series


# ---------------------------------------------------------------------------
# reading a transient table
# ---------------------------------------------------------------------------


def test_real_wrdata_is_read_as_time_and_value():
    text = "0.000000e+00\t7.5e-01\n1.000000e-09\t7.6e-01\n2.0e-09\t7.7e-01\n"
    points = parse_wrdata_real(text)
    assert points == [(0.0, 0.75), (1e-9, 0.76), (2e-9, 0.77)]


def test_unreadable_rows_are_skipped_not_guessed_at():
    text = ("# a header ngspice felt like writing\n"
            "0.0\t0.75\n"
            "not numbers at all\n"
            "1e-09\t0.76\n"
            "\n"
            "2e-09\t0.77\n")
    assert len(parse_wrdata_real(text)) == 3


def test_a_table_too_short_to_be_a_waveform_is_refused():
    with pytest.raises(NgspiceParseError) as caught:
        parse_wrdata_real("0.0\t0.75\n1e-09\t0.76\n")
    assert "time series" in str(caught.value)


# ---------------------------------------------------------------------------
# what the step measures
# ---------------------------------------------------------------------------


def test_slew_rate_recovers_a_known_slope():
    measured = measure_step(ramp(5.0), RISE_AT, FALL_AT, WINDOW)
    assert measured["slew_rise"] == pytest.approx(5e6, rel=0.01)
    assert measured["slew_fall"] == pytest.approx(5e6, rel=0.01)


def test_the_reported_rate_is_the_worse_edge():
    """A datasheet has to honour the slower one, so that is what is shown."""
    quick_up = ramp(9.0)[:]
    # Rebuild only the falling half at a slower rate.
    slow_down = ramp(3.0)
    joined = [
        point if point[0] < FALL_AT else slow_down[index]
        for index, point in enumerate(quick_up)
    ]
    measured = measure_step(joined, RISE_AT, FALL_AT, WINDOW)
    assert measured["slew_rise"] > measured["slew_fall"]
    assert measured["slew_rate"] == measured["slew_fall"]


def test_settling_time_is_measured_from_the_edge():
    """A 0.3 V step at 5 V/us takes 60 ns to arrive and then it is there."""
    measured = measure_step(ramp(5.0), RISE_AT, FALL_AT, WINDOW)
    assert measured["settling_time"] == pytest.approx(60e-9, rel=0.05)


def test_an_output_that_never_settles_says_so():
    """None is a real answer: it did not settle inside the window."""
    creeping = [(t, 0.75 + 0.3 * (t / 750e-9)) for t in
                [750e-9 * i / 3999 for i in range(4000)]]
    measured = measure_step(creeping, RISE_AT, FALL_AT, WINDOW)
    assert measured["settling_time"] is None


def test_overshoot_is_a_fraction_of_the_step():
    """Ring 10 percent past the final value and the number says 0.1."""
    series = []
    for index in range(4000):
        time = 750e-9 * index / 3999
        if time < RISE_AT:
            value = 0.75
        elif time < RISE_AT + 20e-9:
            # A real edge, not a vertical line: the measurement needs
            # samples between the tenth and the ninetieth percentile.
            value = 0.75 + 0.33 * (time - RISE_AT) / 20e-9
        elif time < RISE_AT + 40e-9:
            value = 1.08          # 10 percent past a 0.30 V step
        else:
            value = 1.05
        series.append((time, value))
    measured = measure_step(series, RISE_AT, FALL_AT, WINDOW)
    assert measured["overshoot"] == pytest.approx(0.1, rel=0.02)


def test_a_flat_output_is_not_a_step():
    flat = [(750e-9 * i / 3999, 0.9) for i in range(4000)]
    with pytest.raises(NgspiceParseError) as caught:
        measure_step(flat, RISE_AT, FALL_AT, WINDOW)
    assert "edge" in str(caught.value)


def test_a_clipping_buffer_is_refused_rather_than_measured():
    """Against the rails it is stuck, not slewing, and the numbers would lie."""
    railed = ([(t * 1e-9, 0.05) for t in range(100)]
              + [(1e-7 + t * 1e-9, 1.75) for t in range(100)])
    with pytest.raises(circuits.BiasError) as caught:
        circuits.measure_step_response(
            railed, circuits.defaults("opamp_two_stage"), WINDOW
        )
    assert "clipping" in str(caught.value)


# ---------------------------------------------------------------------------
# framing and plumbing
# ---------------------------------------------------------------------------


def test_the_window_follows_the_parts_that_set_the_slew():
    """Twice the compensation is half the rate, so twice the window."""
    params = circuits.defaults("opamp_two_stage")
    slow = dict(params, ibias=1e-6, cc=2e-11)
    assert circuits.opamp_step_window(slow) > circuits.opamp_step_window(params)

    doubled = dict(slow, cc=slow["cc"] * 2)
    assert circuits.opamp_step_window(doubled) == pytest.approx(
        2 * circuits.opamp_step_window(slow), rel=1e-9
    )


def test_the_window_never_falls_below_its_floor():
    """However fast the amplifier, there is a shortest useful look."""
    params = dict(circuits.defaults("opamp_two_stage"), ibias=2e-4, cc=5e-14)
    assert circuits.opamp_step_window(params) == circuits.STEP_WINDOW_FLOOR_S


def test_only_the_amplifiers_declare_a_step():
    assert circuits.has_step("opamp_two_stage")
    assert circuits.has_step("ota_5t")
    for circuit_id in ("divider", "rc_lowpass", "nfet_cs_amp"):
        assert not circuits.has_step(circuit_id)


def test_a_circuit_without_a_step_says_so_clearly():
    with pytest.raises(circuits.NoStepResponseError) as caught:
        circuits.run_step("divider", circuits.defaults("divider"))
    assert "SKY130 amplifiers" in str(caught.value)


def test_the_waveform_is_thinned_but_keeps_its_ends():
    dense = [(i * 1e-9, i * 0.001) for i in range(5000)]
    kept = circuits._decimate(dense, 900)
    assert len(kept) == 900
    assert kept[0] == [dense[0][0], dense[0][1]]
    assert kept[-1] == [dense[-1][0], dense[-1][1]]


def test_a_short_waveform_is_left_alone():
    short = [(i * 1e-9, i * 0.01) for i in range(10)]
    assert len(circuits._decimate(short, 900)) == 10


def test_no_declared_width_exceeds_what_the_device_accepts():
    """The model refuses 101 um. A parameter that offers more is a trap:
    the form takes the value and ngspice fails on it."""
    for circuit in circuits.catalog():
        for spec in circuit["params"]:
            if spec["key"].startswith("w") and circuit["pdk"]:
                assert spec["max"] <= circuits.SKY130_MAX_WIDTH_M, (
                    circuit["id"], spec["key"]
                )


def test_the_step_testbench_closes_the_loop_for_real():
    """No servo, no lifted feedback: the output goes back to the inverting
    gate and the pulse drives the other one."""
    params = circuits.defaults("opamp_two_stage")
    netlist = circuits.build_opamp_step(params, 250e-9, ["out.data"])
    assert "Lfb" not in netlist          # no DC servo
    assert "Einv" not in netlist         # nothing flipping the sign
    assert "PULSE(" in netlist
    assert "tran " in netlist
    # M1's gate is the inverting input and it is tied to the output node.
    assert " out tail " in netlist
    assert netlist.rstrip().endswith(".end")


def test_the_catalogue_advertises_the_step():
    listing = {item["id"]: item for item in circuits.catalog()}
    assert listing["divider"]["step"] is None
    step = listing["opamp_two_stage"]["step"]
    assert [item["key"] for item in step["readout"]] == [
        "slew_rate", "settling_time", "overshoot"
    ]
    assert "unity buffer" in step["caption"]


# ---------------------------------------------------------------------------
# live runs: a real transient against the real model library
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
def live_step():
    return circuits.run_step(
        "opamp_two_stage", circuits.defaults("opamp_two_stage")
    )


@requires_live_pdk
def test_live_step_lands_near_the_current_over_the_capacitor(live_step):
    """The textbook says a two-stage amplifier slews at its tail current
    over its compensation capacitor. The measurement should land under
    that ceiling and within sight of it, not somewhere unrelated."""
    params = circuits.defaults("opamp_two_stage")
    tail = params["ibias"] * circuits.OPAMP_W5 / circuits.OPAMP_W8
    ceiling = tail / params["cc"]

    assert 0.5 * ceiling < live_step["slew_rate"] < ceiling


@requires_live_pdk
def test_live_step_settles_and_does_not_ring(live_step):
    """Seventy degrees of phase margin should not overshoot measurably."""
    assert live_step["settling_time"] is not None
    assert live_step["settling_time"] < live_step["window"]
    assert live_step["overshoot"] < 0.05


@requires_live_pdk
def test_live_step_returns_a_drawable_waveform(live_step):
    waveform = live_step["waveform"]
    assert len(waveform) == circuits.WAVEFORM_POINTS
    assert waveform[0][0] == 0.0
    # Three windows: the flat, the pulse, and the tail after it comes back.
    assert waveform[-1][0] == pytest.approx(3 * live_step["window"], rel=0.01)
    assert all(len(point) == 2 for point in waveform)


@requires_live_pdk
def test_live_step_measures_both_edges(live_step):
    assert live_step["slew_rise"] > 0
    assert live_step["slew_fall"] > 0
    assert live_step["slew_rate"] == min(
        live_step["slew_rise"], live_step["slew_fall"]
    )
