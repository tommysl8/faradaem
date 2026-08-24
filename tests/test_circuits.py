"""V0.1.5: the circuit registry, the new measurements, and live sweeps.

The measurement maths is proved against synthetic transfer functions generated
from the closed-form expressions, so the numerics are pinned without a
simulator. Only the tests at the bottom run ngspice.
"""

import math

import pytest

from spice import circuits
from spice.runner import (
    NgspiceParseError,
    compute_bode,
    find_ngspice,
    measure_bandpass,
    measure_closedloop,
    measure_highpass,
)

ALL_IDS = [
    "divider",
    "rc_lowpass",
    "rc_highpass",
    "rlc_bandpass",
    "inverting_amp",
    "twopole_amp",
    "nfet_cs_amp",
    "opamp_two_stage",
    "ota_5t",
]

#: AC circuits that need no PDK.  The SKY130 stage is exercised in
#: tests/test_pdk.py, which skips when the model library is absent.
AC_IDS = ["rc_lowpass", "rc_highpass", "rlc_bandpass", "inverting_amp"]

#: Circuits that ship a closed-form check.  nfet_cs_amp deliberately does
#: not: square law does not describe a 150 nm short-channel device, and a
#: check that is permanently tens of percent out would read as a fault.
CHECKED_IDS = [
    "divider",
    "rc_lowpass",
    "rc_highpass",
    "rlc_bandpass",
    "inverting_amp",
    "twopole_amp",
]


def sweep_points(transfer, fstart, fstop, per_decade=20):
    """Rows shaped like wrdata output, sampled from a closed-form H(f)."""
    decades = math.log10(fstop / fstart)
    count = int(round(decades * per_decade)) + 1
    rows = []
    for index in range(count):
        frequency = fstart * 10.0 ** (index * decades / (count - 1))
        response = transfer(frequency)
        rows.append((frequency, response.real, response.imag))
    return rows


def highpass_h(fc):
    def transfer(f):
        u = 1j * f / fc
        return u / (1.0 + u)
    return transfer


def bandpass_h(f0, q):
    def transfer(f):
        u = f / f0
        return (1j * u / q) / (1.0 - u * u + 1j * u / q)
    return transfer


def closedloop_h(gain, f3db):
    def transfer(f):
        return gain / (1.0 + 1j * f / f3db)
    return transfer


# ---- registry shape -----------------------------------------------------


def test_catalogue_holds_exactly_the_catalogued_circuits():
    assert set(circuits.CIRCUITS) == set(ALL_IDS)
    assert circuits.CIRCUIT_ORDER == ALL_IDS


@pytest.mark.parametrize("circuit_id", ALL_IDS)
def test_every_circuit_is_complete(circuit_id):
    circuit = circuits.get_circuit(circuit_id)
    for field in ("id", "name", "analysis", "caption", "params",
                  "build", "measure", "readout"):
        assert circuit[field], field
    # checks may legitimately be empty -- see CHECKED_IDS.
    assert isinstance(circuit["checks"], list)
    assert circuit["id"] == circuit_id
    assert circuit["analysis"] in ("dc", "ac")
    if circuit["analysis"] == "ac":
        assert callable(circuit["centre"])


@pytest.mark.parametrize("circuit_id", ALL_IDS)
def test_every_param_spec_is_complete(circuit_id):
    for spec in circuits.get_circuit(circuit_id)["params"]:
        assert spec["key"] and spec["label"]
        assert spec["unit"] is not None
        assert isinstance(spec["default"], float)
        assert spec["min"] < spec["max"]
        assert spec["min"] <= spec["default"] <= spec["max"], spec["key"]


@pytest.mark.parametrize("circuit_id", ALL_IDS)
def test_every_check_points_at_a_real_measurement(circuit_id):
    circuit = circuits.get_circuit(circuit_id)
    for item in circuit["checks"]:
        assert item["key"] and item["label"] and item["measured"]
        assert 0 < item["tolerance"] <= 1
        assert callable(item["formula"])


@pytest.mark.parametrize("circuit_id", ALL_IDS)
def test_readout_only_references_declared_checks(circuit_id):
    circuit = circuits.get_circuit(circuit_id)
    keys = {item["key"] for item in circuit["checks"]}
    readout = circuit["readout"]
    for entry in [readout["headline"]] + list(readout["stats"]):
        if entry["check"] is not None:
            assert entry["check"] in keys, entry


@pytest.mark.parametrize("circuit_id", CHECKED_IDS)
def test_checks_evaluate_on_the_defaults(circuit_id):
    values = circuits.analytic_values(circuit_id, circuits.defaults(circuit_id))
    assert values
    for key, value in values.items():
        assert math.isfinite(value), key


def test_unknown_circuit_raises_with_a_helpful_message():
    with pytest.raises(circuits.UnknownCircuitError) as excinfo:
        circuits.get_circuit("op_amp_deluxe")
    assert "rlc_bandpass" in str(excinfo.value)


def test_catalog_is_json_ready():
    import json

    listing = circuits.catalog()
    assert [item["id"] for item in listing] == ALL_IDS
    json.dumps(listing)  # must not contain callables


def test_catalog_omits_the_check_formulas():
    for entry in circuits.catalog():
        for item in entry["checks"]:
            assert "formula" not in item


# ---- closed-form helpers ------------------------------------------------


def test_default_rlc_lands_on_the_documented_resonance():
    params = circuits.defaults("rlc_bandpass")
    assert circuits.rlc_centre(params) == pytest.approx(5032.9, rel=1e-3)
    assert circuits.rlc_q(params) == pytest.approx(3.1623, rel=1e-3)


def test_default_amp_lands_on_the_documented_gain_and_bandwidth():
    params = circuits.defaults("inverting_amp")
    assert circuits.amp_midband_db(params) == pytest.approx(20.0, abs=1e-9)
    assert circuits.amp_noise_gain(params) == pytest.approx(11.0)
    assert circuits.amp_bandwidth(params) == pytest.approx(90909.0, rel=1e-3)


def test_gain_bandwidth_product_is_scaled_by_the_noise_gain():
    # (Rf/Rin)*GBW/N, which is 0.909*GBW at the defaults -- not GBW itself.
    params = circuits.defaults("inverting_amp")
    assert circuits.amp_gain_bw(params) == pytest.approx(909090.9, rel=1e-4)


# ---- sweep framing ------------------------------------------------------


@pytest.mark.parametrize(
    "circuit_id,expected",
    [
        ("rc_lowpass", 1000.9745),
        ("rc_highpass", 1000.9745),
        ("rlc_bandpass", 5032.9),
        ("inverting_amp", 90909.0),
    ],
)
def test_sweep_centres_on_the_right_frequency(circuit_id, expected):
    circuit = circuits.get_circuit(circuit_id)
    centre = circuit["centre"](circuits.defaults(circuit_id))
    assert centre == pytest.approx(expected, rel=1e-3)


@pytest.mark.parametrize("circuit_id", AC_IDS)
def test_sweep_spans_three_decades_either_side(circuit_id):
    circuit = circuits.get_circuit(circuit_id)
    centre = circuit["centre"](circuits.defaults(circuit_id))
    fstart, fstop = circuits.sweep_range(centre)
    assert fstart == pytest.approx(centre / 1000.0, rel=1e-9)
    assert fstop == pytest.approx(centre * 1000.0, rel=1e-9)


def test_sweep_range_clamps_to_what_ngspice_can_handle():
    fstart, fstop = circuits.sweep_range(1.0)
    assert fstart == pytest.approx(0.01)
    assert fstop == pytest.approx(1000.0)

    fstart, fstop = circuits.sweep_range(1e9)
    assert fstop == pytest.approx(circuits.FREQ_MAX)


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_sweep_range_rejects_a_nonsense_centre(bad):
    with pytest.raises(ValueError):
        circuits.sweep_range(bad)


# ---- builders -----------------------------------------------------------


OUT_PATH = r"C:\Users\tommy\AppData\Local\Temp\faradaem_ac.data"


def build(circuit_id):
    circuit = circuits.get_circuit(circuit_id)
    params = circuits.defaults(circuit_id)
    if circuit["analysis"] == "dc":
        return circuit["build"](params).splitlines()
    fstart, fstop = circuits.sweep_range(circuit["centre"](params))
    return circuit["build"](params, fstart, fstop, OUT_PATH).splitlines()


def test_highpass_netlist_has_series_c_and_shunt_r():
    lines = build("rc_highpass")
    assert "V1 in 0 AC 1" in lines
    assert "C1 in out 1.59e-07" in lines
    assert "R1 out 0 1000" in lines


def test_bandpass_netlist_is_a_series_loop():
    lines = build("rlc_bandpass")
    assert "V1 in 0 AC 1" in lines
    assert "L1 in nlc 0.001" in lines
    assert "C1 nlc out 1e-06" in lines
    assert "R1 out 0 10" in lines


def test_inverting_amp_netlist_carries_the_macromodel():
    lines = build("inverting_amp")
    assert "V1 in 0 AC 1" in lines
    assert "Rin in vm 1000" in lines
    assert "Rf vm out 10000" in lines
    # E1 senses (0 - vm), which is what makes the stage inverting.
    assert "E1 p1 0 0 vm 100000" in lines
    assert "Rp p1 p2 1000" in lines
    assert "Eb out 0 p2 0 1" in lines


def test_macromodel_capacitor_places_the_pole_at_gbw_over_a0():
    params = circuits.defaults("inverting_amp")
    fstart, fstop = circuits.sweep_range(circuits.amp_bandwidth(params))
    lines = circuits.build_inverting_amp(params, fstart, fstop, OUT_PATH).splitlines()
    cp = float([ln for ln in lines if ln.startswith("Cp ")][0].split()[-1])

    pole = 1.0 / (2.0 * math.pi * circuits.MACROMODEL_RP * cp)
    assert pole == pytest.approx(params["gbw"] / params["a0"], rel=1e-9)


@pytest.mark.parametrize("circuit_id", AC_IDS)
def test_every_ac_netlist_sweeps_and_writes_data(circuit_id):
    lines = build(circuit_id)
    assert lines[0].startswith("*")
    assert any(line.startswith("ac dec 20 ") for line in lines)
    wrdata = [line for line in lines if line.startswith("wrdata")][0]
    assert "\\" not in wrdata           # forward slashes only
    assert wrdata.endswith(" v(out)")
    assert lines[-1] == ".end"
    assert ".control" in lines and ".endc" in lines


def test_divider_builder_still_produces_the_v0_0_netlist():
    lines = build("divider")
    assert "V1 in 0 DC 5" in lines
    assert "R1 in out 10000" in lines
    assert "R2 out 0 10000" in lines
    assert "print v(out)" in lines


# ---- measure_highpass ---------------------------------------------------


@pytest.mark.parametrize("fc", [10.0, 1000.0, 47_000.0])
def test_measure_highpass_recovers_the_corner(fc):
    bode = compute_bode(sweep_points(highpass_h(fc), fc / 1000.0, fc * 1000.0))
    measured = measure_highpass(bode)
    assert measured["f3db"] == pytest.approx(fc, rel=0.005)
    assert measured["passband_db"] == pytest.approx(0.0, abs=0.05)
    assert measured["phase_at_f3db"] == pytest.approx(45.0, abs=1.0)


def test_highpass_phase_leads_at_low_frequency():
    fc = 1000.0
    bode = compute_bode(sweep_points(highpass_h(fc), fc / 1000.0, fc * 1000.0))
    assert bode["phase_deg"][0] == pytest.approx(90.0, abs=0.1)
    assert bode["phase_deg"][-1] == pytest.approx(0.0, abs=0.1)


def test_measure_highpass_raises_when_the_corner_is_below_the_sweep():
    # Sweep entirely in the passband: nothing ever falls 3 dB.
    bode = compute_bode(sweep_points(highpass_h(1.0), 1e4, 1e6))
    with pytest.raises(NgspiceParseError) as excinfo:
        measure_highpass(bode)
    assert "not bracketed" in str(excinfo.value)


# ---- measure_bandpass ---------------------------------------------------


#: A -3 dB bandwidth spans roughly 1/(Q*ln10) decades, so a high-Q peak needs a
#: fine grid before its skirts can be interpolated at all. At Q=10 the whole
#: bandwidth is 0.043 decades -- narrower than one sample at 20 points/decade.
FINE_GRID = 200


def bandpass_bode(f0, q, per_decade=FINE_GRID, decades=2):
    return compute_bode(sweep_points(
        bandpass_h(f0, q), f0 / 10.0 ** decades, f0 * 10.0 ** decades, per_decade
    ))


@pytest.mark.parametrize("q", [1.0, 3.1623, 10.0])
def test_measure_bandpass_recovers_centre_and_q(q):
    f0 = 5032.9
    measured = measure_bandpass(bandpass_bode(f0, q))

    assert measured["f0_measured"] == pytest.approx(f0, rel=0.005)
    assert measured["q_measured"] == pytest.approx(q, rel=0.02)
    assert measured["peak_gain_db"] == pytest.approx(0.0, abs=0.05)


@pytest.mark.parametrize("q", [1.0, 3.1623, 10.0])
def test_bandpass_skirts_straddle_the_centre(q):
    f0 = 5032.9
    measured = measure_bandpass(bandpass_bode(f0, q))

    assert measured["f_lower"] < measured["f0_measured"] < measured["f_upper"]
    assert measured["bw"] == pytest.approx(f0 / q, rel=0.02)
    # Resonance sits at the geometric mean of the two skirts.
    geometric = math.sqrt(measured["f_lower"] * measured["f_upper"])
    assert geometric == pytest.approx(f0, rel=0.01)


def test_moderate_q_is_resolved_at_the_shipped_sweep_density():
    """The default RLC (Q~3.16) is measurable on the real 20-point grid."""
    f0 = 5032.9
    measured = measure_bandpass(bandpass_bode(f0, 3.1623, per_decade=20, decades=3))
    assert measured["f0_measured"] == pytest.approx(f0, rel=0.005)
    assert measured["q_measured"] == pytest.approx(3.1623, rel=0.03)


def test_high_q_is_under_resolved_at_the_shipped_sweep_density():
    """A known limit, pinned so it cannot regress silently.

    At 20 points per decade a Q of 10 has its whole -3 dB bandwidth inside one
    sample interval, so the interpolated skirts sit too close together and Q
    reads high. Raising Q past roughly 5 needs a denser sweep, not a different
    measurement. The default RLC is well inside the usable range.
    """
    f0 = 5032.9
    coarse = measure_bandpass(bandpass_bode(f0, 10.0, per_decade=20, decades=3))
    fine = measure_bandpass(bandpass_bode(f0, 10.0, per_decade=FINE_GRID))

    assert coarse["f0_measured"] == pytest.approx(f0, rel=0.005)  # peak is still fine
    assert coarse["q_measured"] > 11.0                            # but Q reads high
    assert fine["q_measured"] == pytest.approx(10.0, rel=0.02)


def test_quadratic_interpolation_beats_the_nearest_sample():
    """The peak rarely lands on a sample; interpolation is why f0 comes out close."""
    f0 = 5032.9
    # Offset the grid so no sample coincides with resonance.
    bode = compute_bode(sweep_points(bandpass_h(f0, 3.1623), f0 / 977.0, f0 * 1013.0))
    nearest = bode["freq"][bode["mag_db"].index(max(bode["mag_db"]))]
    measured = measure_bandpass(bode)

    assert nearest != pytest.approx(f0, rel=1e-6)  # the grid really does miss it
    assert abs(measured["f0_measured"] - f0) < abs(nearest - f0)


def test_measure_bandpass_raises_when_a_skirt_is_out_of_range():
    f0 = 5032.9
    # Stop the sweep just past the peak: the upper skirt is never reached.
    bode = compute_bode(sweep_points(bandpass_h(f0, 10.0), f0 / 100.0, f0 * 1.02))
    with pytest.raises(NgspiceParseError) as excinfo:
        measure_bandpass(bode)
    assert "not bracketed" in str(excinfo.value)


def test_measure_bandpass_names_which_skirt_is_missing():
    f0 = 5032.9
    bode = compute_bode(sweep_points(bandpass_h(f0, 10.0), f0 * 0.99, f0 * 1000.0))
    with pytest.raises(NgspiceParseError) as excinfo:
        measure_bandpass(bode)
    assert "lower" in str(excinfo.value)


# ---- measure_closedloop -------------------------------------------------


@pytest.mark.parametrize("gain,f3db", [(10.0, 90909.0), (100.0, 9901.0), (1.0, 500000.0)])
def test_measure_closedloop_recovers_midband_and_bandwidth(gain, f3db):
    bode = compute_bode(
        sweep_points(closedloop_h(gain, f3db), f3db / 1000.0, f3db * 1000.0)
    )
    measured = measure_closedloop(bode)

    assert measured["midband_db"] == pytest.approx(20.0 * math.log10(gain), abs=0.05)
    assert measured["f3db"] == pytest.approx(f3db, rel=0.01)
    assert measured["gain_bw_product"] == pytest.approx(gain * f3db, rel=0.01)


def test_measure_closedloop_raises_when_the_corner_is_above_the_sweep():
    bode = compute_bode(sweep_points(closedloop_h(10.0, 1e6), 1.0, 100.0))
    with pytest.raises(NgspiceParseError):
        measure_closedloop(bode)


# ---- live ngspice -------------------------------------------------------


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(),
    reason="ngspice is not available on this machine, so the live sweeps cannot run",
)


@pytest.fixture(scope="module")
def swept():
    """Every discrete AC circuit run once at its defaults, plus the divider.

    PDK circuits are excluded on purpose: they need a model library this
    machine may not have, and they belong to tests/test_pdk.py.
    """
    return {
        circuit_id: circuits.simulate(circuit_id, circuits.defaults(circuit_id))
        for circuit_id in CHECKED_IDS
    }


@requires_ngspice
def test_live_divider_is_still_exactly_half(swept):
    assert swept["divider"]["vout"] == pytest.approx(2.5, abs=1e-9)
    assert swept["divider"]["analytic"]["vout_ideal"] == pytest.approx(2.5)


@requires_ngspice
def test_live_rc_lowpass_unchanged(swept):
    result = swept["rc_lowpass"]
    assert result["f3db"] == pytest.approx(1000.9, rel=0.02)
    assert result["dc_gain_db"] == pytest.approx(0.0, abs=0.09)


@requires_ngspice
def test_live_rc_highpass_finds_its_corner(swept):
    result = swept["rc_highpass"]
    assert result["f3db"] == pytest.approx(1000.0, rel=0.02)
    assert result["passband_db"] == pytest.approx(0.0, abs=0.09)
    assert result["phase_at_f3db"] == pytest.approx(45.0, abs=2.0)
    assert result["phase_deg"][0] == pytest.approx(90.0, abs=2.0)


@requires_ngspice
def test_live_rlc_bandpass_resonates_where_predicted(swept):
    result = swept["rlc_bandpass"]
    assert result["f0_measured"] == pytest.approx(5032.9, rel=0.02)
    assert result["q_measured"] == pytest.approx(3.1623, rel=0.05)
    assert result["peak_gain_db"] == pytest.approx(0.0, abs=0.1)
    assert result["f_lower"] < result["f0_measured"] < result["f_upper"]


@requires_ngspice
def test_live_inverting_amp_hits_its_gain_and_bandwidth(swept):
    result = swept["inverting_amp"]
    assert result["midband_db"] == pytest.approx(20.0, abs=0.2)
    assert result["f3db"] == pytest.approx(90909.0, rel=0.05)


@requires_ngspice
def test_live_amp_trades_gain_for_bandwidth():
    """Ten times the feedback resistor, ten times less bandwidth."""
    params = circuits.defaults("inverting_amp")
    modest = circuits.simulate("inverting_amp", dict(params, rf=10000.0))
    greedy = circuits.simulate("inverting_amp", dict(params, rf=100000.0))

    assert greedy["midband_db"] - modest["midband_db"] == pytest.approx(20.0, abs=0.3)
    assert modest["f3db"] / greedy["f3db"] == pytest.approx(10.0, rel=0.1)
    # The product is what stays put, which is the whole point of GBW.
    assert greedy["gain_bw_product"] == pytest.approx(
        modest["gain_bw_product"], rel=0.12
    )


@requires_ngspice
@pytest.mark.parametrize("circuit_id", CHECKED_IDS)
def test_live_measurements_agree_with_their_analytic_checks(circuit_id, swept):
    circuit = circuits.get_circuit(circuit_id)
    result = swept[circuit_id]

    for item in circuit["checks"]:
        measured = result[item["measured"]]
        expected = result["analytic"][item["key"]]
        if item["unit"] == "dB":
            assert abs(measured - expected) < 0.2, item["key"]
        else:
            assert measured == pytest.approx(expected, rel=item["tolerance"]), item["key"]


@requires_ngspice
def test_live_sweeps_leave_no_temp_files_behind():
    import glob
    import tempfile

    pattern = tempfile.gettempdir() + "\\faradaem_*"
    before = set(glob.glob(pattern))
    circuits.simulate("rlc_bandpass", circuits.defaults("rlc_bandpass"))
    circuits.simulate("inverting_amp", circuits.defaults("inverting_amp"))
    assert set(glob.glob(pattern)) == before
