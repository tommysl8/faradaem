"""Forensics: the inverse problem, proved on problems whose answer is known.

A fake simulator whose output is a known function of the condition gives
the search a ground truth; the tests require the search to recover it from
the numbers alone. The live test asks the real simulator only the cheapest
question, that a nominal observation is explained by the nominal condition.
"""

import math
import random

import pytest

from spice import circuits, forensics, pvt
from spice.runner import find_ngspice, sky130_available

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


#: A fake physics: every watched metric responds to corner, supply and
#: temperature differently, so no two grid conditions produce the same
#: numbers and recovery has a unique right answer.
CORNER_SHIFT = {"tt": 0.0, "ss": -3.0, "ff": 3.0, "sf": -1.0, "fs": 1.0}


def _metrics(corner, vdd, temp, seed=None):
    shift = CORNER_SHIFT[corner] + (0.37 * seed if seed else 0.0)
    return {
        "loop_gain_db": 40.0 + shift + 2.0 * (vdd - 1.8) - 0.01 * temp,
        "f_crossover": 8e6 * (1.0 + 0.05 * shift) * (vdd / 1.8),
        "phase_margin": 70.0 - shift + 0.02 * temp,
        "power": 5e-5 * (vdd / 1.8) ** 2 * (1.0 + 0.01 * shift),
    }


def _install(monkeypatch):
    """Route the fake physics through the real transform plumbing: the
    condition is captured where the netlist edit would be made."""
    captured = {}
    real = pvt.make_transform

    def capture(corner=None, vdd=None, temp=None, seed=None):
        captured["condition"] = (corner, vdd, temp, seed)
        return real(corner, vdd, temp, seed)

    def simulate(circuit_id, params, transform=None):
        corner, vdd, temp, seed = captured["condition"]
        if corner == pvt.MC_SECTION:
            corner = "tt"
        return _metrics(corner or "tt", vdd, temp, seed)

    monkeypatch.setattr(pvt, "make_transform", capture)
    monkeypatch.setattr(circuits, "simulate", simulate)


def test_residual_is_zero_for_a_perfect_story():
    observed = _metrics("ss", 1.62, 125.0)
    keys = list(observed)
    assert forensics.residual(observed, observed, keys) == 0.0


def test_residual_is_scale_free():
    observed = {"a": 100.0, "b": 1e-6}
    off = {"a": 101.0, "b": 1.01e-6}
    value = forensics.residual(observed, off, ["a", "b"])
    assert value == pytest.approx(0.01, rel=1e-6)


def test_explain_refuses_a_partial_observation():
    with pytest.raises(forensics.ForensicsError):
        forensics.explain("ota_5t", circuits.defaults("ota_5t"),
                          {"loop_gain_db": 40.0})


def test_explain_recovers_a_grid_condition(monkeypatch):
    _install(monkeypatch)
    truth = ("ss", 1.62, 125.0)
    observed = _metrics(*truth)
    found = forensics.explain("ota_5t", circuits.defaults("ota_5t"),
                              observed, budget=40)
    assert found["verdict"] == "explained"
    best = found["best"]
    assert (best["corner"], best["vdd"], best["temp"]) == truth


def test_explain_recovers_a_mismatch_draw(monkeypatch):
    _install(monkeypatch)
    observed = _metrics("tt", 1.8, 27.0, seed=3)
    found = forensics.explain("ota_5t", circuits.defaults("ota_5t"),
                              observed, budget=60, seeds=5)
    assert found["verdict"] == "explained"
    assert found["best"]["seed"] == 3


def test_an_observation_outside_the_space_is_unexplained(monkeypatch):
    _install(monkeypatch)
    observed = _metrics("tt", 1.8, 27.0)
    observed["loop_gain_db"] -= 12.0  # nothing on the grids does this
    found = forensics.explain("ota_5t", circuits.defaults("ota_5t"),
                              observed, budget=60)
    assert found["verdict"] == "unexplained"
    assert found["best"]["residual"] > forensics.EXPLAINED_RMS


def test_blind_trials_recover_what_was_drawn(monkeypatch):
    _install(monkeypatch)
    rng = random.Random(7)
    for _ in range(5):
        trial = forensics.blind_trial("ota_5t", circuits.defaults("ota_5t"),
                                      rng, budget=60, seeds=4)
        assert trial["match"], (trial["truth"], trial["recovered"])


def test_a_budget_truncated_search_is_inconclusive_not_unexplained(
        monkeypatch):
    _install(monkeypatch)
    observed = _metrics("tt", 1.8, 27.0)
    observed["loop_gain_db"] -= 12.0  # nothing on the grids does this
    # The fake simulator never installs an observer count, so truncation
    # is forced through should_stop instead: the claim under test is the
    # verdict wording, not the counting.
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 2

    found = forensics.explain("ota_5t", circuits.defaults("ota_5t"),
                              observed, budget=60, should_stop=stop)
    assert found["verdict"] == "inconclusive"
    assert found["truncated"] is True


def test_the_nominal_settle_exit_stops_the_search_early(monkeypatch):
    _install(monkeypatch)
    observed = _metrics("tt", 1.8, 27.0)
    found = forensics.explain("ota_5t", circuits.defaults("ota_5t"),
                              observed, budget=60)
    assert found["verdict"] == "explained"
    assert len(found["rows"]) == 1


@requires_live_pdk
def test_a_live_nominal_observation_explains_itself():
    params = circuits.defaults("ota_5t")
    measured = circuits.simulate("ota_5t", dict(params))
    keys = forensics.watched_keys(circuits.get_circuit("ota_5t"))
    observed = {key: measured[key] for key in keys}
    found = forensics.explain("ota_5t", params, observed, budget=8)
    assert found["verdict"] == "explained"
    assert found["best"]["corner"] == "tt"
    assert found["best"]["vdd"] == pytest.approx(1.8)
    assert found["best"]["temp"] == pytest.approx(27.0)
