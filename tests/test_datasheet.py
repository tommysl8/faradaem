"""V1.3: rejection and range, the rest of the datasheet.

An AC analysis superposes every source in a deck at once, so one copy of an
amplifier cannot be asked three questions. These testbenches ask one
question of each of four identical copies, which is the only honest way to
get a differential gain, a common-mode gain, a supply gain and a transfer
curve out of a single library load.

Above the live-run marker everything is pure. Below it a real deck runs
against the real model library and skips cleanly when either is absent.
"""

import pytest

from spice import circuits
from spice.runner import (
    NgspiceParseError,
    find_ngspice,
    measure_follower_range,
    rejection_db,
    sky130_available,
)


def bode(*magnitudes):
    """The shape compute_bode returns, with only what rejection reads."""
    return {"freq": [10.0 * (i + 1) for i in range(len(magnitudes))],
            "mag_db": list(magnitudes)}


def follower(low, high, supply=1.8, step=0.01):
    """A transfer curve that follows between two points and clips outside.

    Below the low end the amplifier is dead and the output sits at zero;
    above the high end it saturates. Both flats have to be rejected by the
    measurement, and for opposite reasons.
    """
    points = []
    value = 0.0
    while value <= supply + 1e-9:
        if value < low:
            out = 0.0
        elif value > high:
            out = high
        else:
            out = value
        points.append((round(value, 6), out))
        value += step
    return points


# ---------------------------------------------------------------------------
# rejection
# ---------------------------------------------------------------------------


def test_rejection_is_the_gap_at_the_flat_end():
    assert rejection_db(bode(71.5, 71.4), bode(-5.0, -5.3)) == pytest.approx(76.5)


def test_rejection_reads_the_lowest_frequency():
    """Both curves are flat down there, which is where a datasheet quotes it."""
    assert rejection_db(bode(60.0, 40.0), bode(0.0, 30.0)) == pytest.approx(60.0)


def test_rejection_needs_something_to_read():
    with pytest.raises(NgspiceParseError):
        rejection_db({"mag_db": []}, bode(1.0))


# ---------------------------------------------------------------------------
# the range a follower actually follows
# ---------------------------------------------------------------------------


def test_the_range_excludes_the_dead_bottom_of_the_sweep():
    """Output zero against input zero is agreement, not following.

    This is the trap the measurement exists to avoid: at the bottom of the
    sweep the pair has no headroom, everything sits at zero, and an error
    test alone would call that a working buffer.
    """
    measured = measure_follower_range(follower(0.14, 1.38))
    assert measured["input_low"] == pytest.approx(0.14, abs=0.03)
    assert measured["input_high"] == pytest.approx(1.38, abs=0.03)


def test_the_range_reports_both_axes():
    measured = measure_follower_range(follower(0.20, 1.40))
    assert measured["input_range"] == pytest.approx(
        measured["input_high"] - measured["input_low"], rel=1e-9
    )
    assert measured["output_swing"] == pytest.approx(
        measured["output_high"] - measured["output_low"], rel=1e-9
    )


def test_a_buffer_that_never_follows_is_refused():
    stuck = [(i * 0.01, 0.0) for i in range(181)]
    with pytest.raises(NgspiceParseError) as caught:
        measure_follower_range(stuck)
    assert "never followed" in str(caught.value)


def test_a_curve_too_short_to_read_is_refused():
    with pytest.raises(NgspiceParseError):
        measure_follower_range([(0.0, 0.0), (0.1, 0.1)])


def test_a_gain_stage_is_not_mistaken_for_a_follower():
    """Slope two is an amplifier, not a buffer, however small the error."""
    doubling = [(i * 0.01, i * 0.02) for i in range(181)]
    with pytest.raises(NgspiceParseError):
        measure_follower_range(doubling)


# ---------------------------------------------------------------------------
# the deck
# ---------------------------------------------------------------------------


def test_the_deck_holds_four_independent_copies():
    params = circuits.defaults("opamp_two_stage")
    netlist = circuits.build_opamp_datasheet(
        params, ["dc.data", "dm.data", "cm.data", "ps.data"]
    )
    devices = [line for line in netlist.splitlines() if line.startswith("XM")]
    assert len(devices) == 32               # eight transistors, four times

    for tag in ("a", "c", "s", "b"):
        assert "Ib" + tag + " " in netlist
        assert "XM" + tag + "1 " in netlist

    # One stimulus each, and the supply copy hangs off its own rail.
    assert "Vsa asrc 0 DC 0 AC 1" in netlist
    assert "Vccm ccm 0 DC 0.9 AC 1" in netlist
    assert "Vsvdd svdd 0 DC 1.8 AC 1" in netlist
    assert "Vbin binp 0 DC 0.9" in netlist


def test_the_supply_copy_holds_its_input_still():
    """The servo inductor opens above DC. Without this the gate floats and
    the supply rejection reads as gain, which it did before this line."""
    netlist = circuits.build_opamp_datasheet(
        circuits.defaults("opamp_two_stage"), ["a", "b", "c", "d"]
    )
    assert "Cgnds sinn 0 1e9" in netlist


def test_the_buffer_copy_closes_its_own_loop():
    netlist = circuits.build_opamp_datasheet(
        circuits.defaults("opamp_two_stage"), ["a", "b", "c", "d"]
    )
    # b's inverting gate is its own output: no servo on this copy.
    assert "XMb1 bd1 bout btail" in netlist
    assert "Lfbb" not in netlist


def test_one_run_writes_the_dc_sweep_before_the_ac_one():
    netlist = circuits.build_opamp_datasheet(
        circuits.defaults("opamp_two_stage"),
        ["dc.data", "dm.data", "cm.data", "ps.data"]
    )
    body = netlist[netlist.index(".control"):]
    assert body.index("dc Vbin") < body.index("wrdata dc.data")
    assert body.index("wrdata dc.data") < body.index("ac dec")
    assert body.index("ac dec") < body.index("wrdata dm.data")
    assert netlist.rstrip().endswith(".end")


def test_the_ota_deck_has_no_second_stage():
    netlist = circuits.build_ota_datasheet(
        circuits.defaults("ota_5t"), ["a", "b", "c", "d"]
    )
    devices = [line for line in netlist.splitlines() if line.startswith("XM")]
    assert len(devices) == 24               # six transistors, four times
    # No Miller compensation: no zero-nulling resistor and no node between
    # one and the output. ("Cc" alone would match the common-mode coupling
    # capacitor, which every copy has and which is not compensation.)
    assert "Rz" not in netlist
    assert "zx" not in netlist


def test_a_dead_amplifier_is_refused_rather_than_quoted():
    """Rejection is a ratio to the gain. Without gain it is noise."""
    with pytest.raises(circuits.BiasError) as caught:
        circuits.measure_datasheet(
            [bode(2.0), bode(1.0), bode(1.0)],
            follower(0.2, 1.4),
            circuits.defaults("opamp_two_stage"),
        )
    assert "too little to reject" in str(caught.value)


def test_only_the_amplifiers_declare_a_datasheet():
    assert circuits.has_datasheet("opamp_two_stage")
    assert circuits.has_datasheet("ota_5t")
    for circuit_id in ("divider", "rc_lowpass", "nfet_cs_amp"):
        assert not circuits.has_datasheet(circuit_id)


def test_a_circuit_without_one_says_so_clearly():
    with pytest.raises(circuits.NoDatasheetError) as caught:
        circuits.run_datasheet("divider", circuits.defaults("divider"))
    assert "SKY130 amplifiers" in str(caught.value)


def test_the_catalogue_advertises_it():
    listing = {item["id"]: item for item in circuits.catalog()}
    assert listing["divider"]["datasheet"] is None
    sheet = listing["opamp_two_stage"]["datasheet"]
    assert [item["key"] for item in sheet["readout"]] == [
        "cmrr_db", "psrr_db", "input_range", "output_swing"
    ]


# ---------------------------------------------------------------------------
# live runs
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
def live_sheet():
    return circuits.run_datasheet(
        "opamp_two_stage", circuits.defaults("opamp_two_stage")
    )


@requires_live_pdk
def test_live_rejection_beats_the_gain_it_is_measured_against(live_sheet):
    """A differential pair exists to reject what both inputs do together,
    so CMRR above the gain is the whole point of the topology."""
    assert live_sheet["cmrr_db"] > live_sheet["gain_db"]
    assert 40.0 < live_sheet["cmrr_db"] < 140.0
    assert 30.0 < live_sheet["psrr_db"] < 140.0


@requires_live_pdk
def test_live_gain_matches_the_open_loop_sweep(live_sheet):
    """The same amplifier measured in a different deck must agree with
    itself, which is what makes the other numbers trustworthy."""
    swept = circuits.simulate(
        "opamp_two_stage", circuits.defaults("opamp_two_stage")
    )
    assert live_sheet["gain_db"] == pytest.approx(swept["loop_gain_db"], abs=1.0)


@requires_live_pdk
def test_live_range_sits_inside_the_supply(live_sheet):
    assert 0.0 < live_sheet["input_low"] < live_sheet["input_high"]
    assert live_sheet["input_high"] < live_sheet["supply"]
    # Headroom is lost at both ends, so the range cannot be the whole rail.
    assert 0.5 < live_sheet["input_range"] < live_sheet["supply"]


@requires_live_pdk
def test_live_transfer_curve_comes_back_drawable(live_sheet):
    curve = live_sheet["transfer"]
    assert len(curve) > 100
    assert all(len(point) == 2 for point in curve)
    assert curve[0][0] == pytest.approx(0.0, abs=1e-9)
    assert curve[-1][0] == pytest.approx(live_sheet["supply"], abs=0.02)
