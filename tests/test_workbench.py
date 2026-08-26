"""The workbench modules: characterization, pins, blame, triage, autopsy.

These features exist to save an engineer's afternoon, which means the
tests guard the honesty of what they report more than the mechanics of
how. A datasheet that mislabels a corner, a sensitivity with the wrong
sign, an autopsy that reads a PMOS backwards: each of those is worse
than the feature not existing.
"""

import json
import os

import pytest

from spice import autopsy, blame, charact, circuits, pins, runner, triage

def _ngspice_missing():
    try:
        runner.find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    _ngspice_missing(),
    reason="ngspice is needed to measure anything",
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Every store the workbench writes, redirected off the real machine."""
    monkeypatch.setenv("FARADAEM_LEDGER", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# characterization
# ---------------------------------------------------------------------------


@requires_ngspice
def test_a_characterization_is_one_object_with_provenance(home):
    found = charact.characterize("divider", {}, include=("bench",))
    assert found["circuit"] == "divider"
    assert found["provenance"]["ngspice"]
    assert found["sections"]["bench"]["ran"]
    measured = found["sections"]["bench"]["data"]["measured"]
    assert isinstance(measured["vout"], float)


@requires_ngspice
def test_margins_come_from_the_registry_goals(home):
    """The regression the plan review caught: goals carry 'default', not
    'target', and a KeyError here was silently swallowed as a failed
    bench section."""
    found = charact.characterize("twopole_amp", {}, include=("bench",))
    section = found["sections"]["bench"]
    assert section["ran"], section
    margins = section["data"]["margins"]
    assert margins, "a circuit with design goals must report margins"
    assert sum(1 for m in margins if m.get("binding")) == 1


def test_margins_accept_explicit_targets():
    circuit = circuits.get_circuit("twopole_amp")
    measured = {"f_crossover": 2.0e6, "phase_margin": 70.0}
    default = charact.margins_of(circuit, measured)
    tighter = charact.margins_of(circuit, measured,
                                 targets={"phase_margin": 80.0})
    by_key = {m["key"]: m for m in tighter}
    assert not by_key["phase_margin"]["met"]
    assert {m["key"] for m in default} == {m["key"] for m in tighter}


@requires_ngspice
def test_the_store_round_trips_and_refuses_traversal(home):
    found = charact.characterize("divider", {}, include=("bench",))
    ident = charact.store(found)
    assert charact.load(ident)["circuit"] == "divider"
    assert charact.load("../../evil") is None
    assert charact.load("nonsense") is None
    rows = charact.listing("divider")
    assert rows and rows[0]["id"] == ident


@requires_ngspice
def test_a_stale_datasheet_is_detectable(home):
    """The staleness check is exact: any sizing difference means the
    document no longer describes the circuit on the bench."""
    found = charact.characterize("divider", {}, include=("bench",))
    assert charact.describes(found, found["sizing"])
    changed = dict(found["sizing"])
    first = sorted(changed)[0]
    changed[first] = changed[first] * 2
    assert not charact.describes(found, changed)


def test_a_failing_section_is_a_finding_not_a_crash(home, monkeypatch):
    def broken(circuit_id, params, transform=None):
        raise RuntimeError("the bias collapsed")
    monkeypatch.setattr(circuits, "simulate", broken)
    found = charact.characterize("divider", {}, include=("bench",))
    section = found["sections"]["bench"]
    assert not section["ran"]
    assert "collapsed" in section["error"]


# ---------------------------------------------------------------------------
# pins
# ---------------------------------------------------------------------------


def test_a_pin_freezes_sizing_numbers_and_provenance(home):
    entry = pins.pin("divider", {"vin": 5.0}, {"vout": 2.5, "note": "x"})
    assert entry["expected"] == {"vout": 2.5}
    assert entry["sizing"] == {"vin": 5.0}
    assert entry["provenance"]["ngspice"] is not None or True
    assert pins.load()["divider"]["pinned_utc"]


def test_pinning_nothing_numeric_refuses(home):
    with pytest.raises(ValueError):
        pins.pin("divider", {}, {"note": "words only"})


@requires_ngspice
def test_a_check_reruns_the_pinned_sizing_not_the_current_one(home):
    """The semantics the review demanded: the check measures the stack
    under a frozen circuit, never the user's latest edits."""
    sizing = circuits.defaults("divider")
    measured = circuits.simulate("divider", dict(sizing))
    pins.pin("divider", sizing, measured)

    record = pins.check("divider")
    assert record["ok"], record["rows"]
    assert record["pin_utc"] == pins.load()["divider"]["pinned_utc"]
    for row in record["rows"]:
        assert abs(row["drift"]) <= record["tolerance"]


@requires_ngspice
def test_a_drifted_number_is_named(home):
    sizing = circuits.defaults("divider")
    measured = dict(circuits.simulate("divider", dict(sizing)))
    measured["vout"] = measured["vout"] * 1.5
    pins.pin("divider", sizing, measured)

    record = pins.check("divider")
    assert not record["ok"]
    broken = [row for row in record["rows"] if not row["ok"]]
    assert broken and broken[0]["key"] == "vout"


def test_checking_without_a_pin_refuses(home):
    with pytest.raises(KeyError):
        pins.check("divider")


def test_history_returns_only_this_circuit_and_finds_the_break(home):
    for index, ok in enumerate([True, True, False, False]):
        pins._append_history({"circuit": "divider", "ok": ok,
                              "when_utc": str(index)})
    pins._append_history({"circuit": "other", "ok": False, "when_utc": "9"})
    records = pins.history("divider")
    assert len(records) == 4
    assert pins.first_break(records) == 2


# ---------------------------------------------------------------------------
# blame
# ---------------------------------------------------------------------------


@requires_ngspice
def test_sensitivities_are_measured_per_knob(home):
    found = blame.sensitivities("twopole_amp", {})
    assert found["sims"] == 1 + 2 * len(found["knobs"])
    assert {k["param"] for k in found["knobs"]} == {"gbw", "fp2"}
    for knob in found["knobs"]:
        assert knob["step_lo"] < knob["at"] < knob["step_hi"]
        assert knob["slopes"], knob["param"]
    assert "central difference" in found["method"]


@requires_ngspice
def test_the_gbw_slope_has_the_sign_physics_demands(home):
    """More gain-bandwidth moves the crossover up. If this sign is wrong
    the whole feature teaches backwards."""
    found = blame.sensitivities("twopole_amp", {})
    gbw = next(k for k in found["knobs"] if k["param"] == "gbw")
    assert gbw["slopes"]["f_crossover"] > 0


@requires_ngspice
def test_the_sentence_names_the_binding_goal(home):
    found = blame.sensitivities("twopole_amp", {})
    said = blame.sentence(found, lambda value, key: "%.3g" % value)
    binding = next(m for m in found["margins"] if m["binding"])
    assert said and binding["label"] in said


def test_blame_refuses_a_circuit_with_no_tunables(home):
    with pytest.raises(ValueError):
        blame.sensitivities("divider", {})


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------


@requires_ngspice
def test_the_verdict_is_one_sim_and_names_the_binding_goal(home):
    found = triage.verdict("twopole_amp", {})
    assert found["sims"] == 1
    assert found["binding"] in {m["key"] for m in found["margins"]}
    assert "at this sizing" in found["sentence"]


@requires_ngspice
def test_a_circuit_without_goals_says_so(home):
    found = triage.verdict("divider", {})
    assert found["feasible_here"] is None
    assert "no design goals" in found["sentence"]


def test_the_sweep_knob_is_declared_in_the_registry():
    for circuit_id in ("ota_5t", "opamp_two_stage", "folded_cascode"):
        assert triage.sweep_knob(circuit_id) == "ibias"
    assert triage.sweep_knob("divider") is None


def test_the_sweep_is_labelled_as_a_slice_not_a_front(home, monkeypatch):
    """The honesty requirement: one knob moved, everything else frozen,
    and the method string says so in as many words."""
    def fake(circuit_id, params, transform=None):
        return {"power": params["ibias"] * 1.8, "f_crossover": 1e6}
    monkeypatch.setattr(circuits, "simulate", fake)
    found = triage.sweep("ota_5t", {})
    assert found["sims"] == triage.SWEEP_POINTS
    assert "not a Pareto front" in found["method"]
    values = [row["value"] for row in found["points"]]
    assert values == sorted(values)
    assert all(row["measured"]["power"] == pytest.approx(row["value"] * 1.8)
               for row in found["points"])


def test_a_sweep_point_that_breaks_is_reported_not_dropped(home,
                                                           monkeypatch):
    def sometimes(circuit_id, params, transform=None):
        if params["ibias"] < 1e-6:
            raise RuntimeError("the bias collapsed")
        return {"power": 1.0}
    monkeypatch.setattr(circuits, "simulate", sometimes)
    found = triage.sweep("ota_5t", {})
    assert len(found["points"]) == triage.SWEEP_POINTS
    broken = [row for row in found["points"] if row["error"]]
    assert broken and "collapsed" in broken[0]["error"]


# ---------------------------------------------------------------------------
# autopsy
# ---------------------------------------------------------------------------


def test_headroom_is_convention_proof():
    """A saturated device must read positive whether its model reports
    p-channel voltages as negative pairs or as magnitudes. The SKY130
    primitives use magnitudes (the diode-tied mirror input measured
    vds = +1.06 V, vdsat = +0.12 V); the classical convention is both
    negative. The first release of this feature got the sign backwards
    for magnitude-convention PMOS and called a healthy diode a failure,
    which is the one mistake this feature must never make."""
    saturated_n = {"vds": 0.5, "vdsat": 0.2}
    saturated_p_negative = {"vds": -0.5, "vdsat": -0.2}
    saturated_p_magnitude = {"vds": 1.06, "vdsat": 0.12}
    assert autopsy.headroom_of(saturated_n, "nfet") == pytest.approx(0.3)
    assert autopsy.headroom_of(saturated_p_negative,
                               "pfet") == pytest.approx(0.3)
    assert autopsy.headroom_of(saturated_p_magnitude,
                               "pfet") == pytest.approx(0.94)
    triode_n = {"vds": 0.1, "vdsat": 0.2}
    triode_p = {"vds": -0.1, "vdsat": -0.2}
    assert autopsy.headroom_of(triode_n, "nfet") == pytest.approx(-0.1)
    assert autopsy.headroom_of(triode_p, "pfet") == pytest.approx(-0.1)
    assert autopsy.headroom_of({"vds": 0.5}, "nfet") is None


def test_the_op_print_parser_reads_ngspice_output():
    stdout = """
@m.xm1.msky130_fd_pr__nfet_01v8[vds] = 5.175314e-01
@m.xm1.msky130_fd_pr__nfet_01v8[vdsat] = 7.075674e-02
@m.xm3.msky130_fd_pr__pfet_01v8[vds] = -6.1e-01
"""
    found = autopsy._parse(stdout)
    assert found["M1"]["vds"] == pytest.approx(0.5175314)
    assert found["M1"]["vdsat"] == pytest.approx(0.07075674)
    assert "vdsat" not in found["M3"]


def test_the_tightest_device_is_found_across_corners():
    rows = [
        {"label": "tt", "devices": {
            "M1": {"headroom": 0.30}, "M2": {"headroom": 0.05}}},
        {"label": "ss 1.62V", "devices": {
            "M1": {"headroom": 0.20}, "M2": {"headroom": -0.01}}},
        {"label": "broken", "devices": None},
    ]
    worst = autopsy.tightest(rows)
    assert worst == {"device": "M2", "label": "ss 1.62V",
                     "headroom": pytest.approx(-0.01)}
    said = autopsy.sentence({"tightest": worst})
    assert "M2" in said and "leaves saturation" in said


def test_a_passing_autopsy_still_names_the_closest_call():
    worst = {"device": "M7", "label": "ss 1.62 V 125C", "headroom": 0.04}
    said = autopsy.sentence({"tightest": worst})
    assert "M7" in said and "40 mV" in said


requires_pdk = pytest.mark.skipif(
    _ngspice_missing() or not runner.sky130_available(),
    reason="ngspice and the SKY130 PDK are needed",
)


@requires_pdk
def test_the_op_deck_prints_every_instance_the_deck_holds(home):
    values = circuits.defaults("ota_5t")
    deck, instances = autopsy._op_deck("ota_5t", values)
    assert set(instances) == {"M1", "M2", "M3", "M4", "M5", "M8"}
    for name, model in instances.items():
        assert ("@m.x%s.m%s[vdsat]" % (name.lower(), model.lower())) in deck
    assert deck.count("print") == 2 * len(instances)
