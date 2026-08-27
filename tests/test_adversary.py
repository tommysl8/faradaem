"""The adversary: staged attack, three verdicts, counted budget.

The verdict logic is proved against a fake simulator, so the tests know
the ground truth the attack is supposed to find. The live attack runs
against the real PDK under a small budget and may return any verdict; what
it must do is stay inside the budget and report honestly.
"""

import pytest

from spice import adversary, circuits, pvt, runner
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


GOOD = {"loop_gain_db": 45.0, "f_crossover": 8e6,
        "phase_margin": 70.0, "power": 5e-5}
BAD = {"loop_gain_db": 20.0, "f_crossover": 8e6,
       "phase_margin": 70.0, "power": 5e-5}


def _fake(monkeypatch, responder):
    monkeypatch.setattr(circuits, "simulate",
                        lambda circuit_id, params, transform=None:
                        responder(transform))


def test_attack_refuses_non_pdk_circuits():
    with pytest.raises(pvt.PvtError):
        adversary.attack("twopole_amp", circuits.defaults("twopole_amp"))


def test_attack_refuses_a_budget_below_the_corners():
    with pytest.raises(adversary.AdversaryError):
        adversary.attack("ota_5t", circuits.defaults("ota_5t"), budget=3)


def test_a_design_that_holds_everywhere_survives(monkeypatch):
    _fake(monkeypatch, lambda transform: dict(GOOD))
    found = adversary.attack("ota_5t", circuits.defaults("ota_5t"), budget=40)
    assert found["verdict"] == "survived"
    assert found["breaking"] is None
    assert found["worst"] is not None
    assert "adversarial simulations" in adversary.claim(found)


def test_a_violated_target_breaks_and_names_the_condition(monkeypatch):
    calls = {"n": 0}

    def responder(transform):
        calls["n"] += 1
        return dict(BAD) if calls["n"] == 3 else dict(GOOD)

    _fake(monkeypatch, responder)
    found = adversary.attack("ota_5t", circuits.defaults("ota_5t"), budget=40)
    assert found["verdict"] == "broken"
    assert found["breaking"]["margins"]["loop_gain_db"] < 0.0
    assert "Broken at" in adversary.claim(found)
    # The attack stops at the break instead of spending the rest.
    assert len(found["rows"]) == 3


def test_an_unmeasurable_condition_denies_the_certificate(monkeypatch):
    calls = {"n": 0}

    def responder(transform):
        calls["n"] += 1
        if calls["n"] == 2:
            raise runner.NgspiceRunError("the bias collapsed")
        return dict(GOOD)

    _fake(monkeypatch, responder)
    found = adversary.attack("ota_5t", circuits.defaults("ota_5t"), budget=40)
    assert found["verdict"] == "unmeasurable"
    assert "Not certified" in adversary.claim(found)


def test_targets_override_the_declared_defaults(monkeypatch):
    _fake(monkeypatch, lambda transform: dict(GOOD))
    found = adversary.attack("ota_5t", circuits.defaults("ota_5t"),
                             targets={"loop_gain_db": 60.0}, budget=40)
    assert found["verdict"] == "broken"
    assert found["breaking"]["margins"]["loop_gain_db"] < 0.0


def test_mismatch_conditions_run_at_the_typical_corner(monkeypatch):
    _fake(monkeypatch, lambda transform: dict(GOOD))
    found = adversary.attack("ota_5t", circuits.defaults("ota_5t"), budget=40)
    seeded = [row for row in found["rows"] if row["seed"] is not None]
    assert seeded, "the surviving attack should have reached mismatch"
    assert all(row["corner"] == "tt" for row in seeded)


def test_a_stopped_attack_is_aborted_never_survived(monkeypatch):
    calls = {"n": 0}
    _fake(monkeypatch, lambda transform: dict(GOOD))

    def stop():
        calls["n"] += 1
        return calls["n"] > 3

    found = adversary.attack("ota_5t", circuits.defaults("ota_5t"),
                             budget=40, should_stop=stop)
    assert found["verdict"] == "aborted"
    assert "certifies nothing" in adversary.claim(found)


@requires_live_pdk
def test_a_small_live_attack_stays_inside_its_budget():
    found = adversary.attack("ota_5t", circuits.defaults("ota_5t"), budget=6)
    assert found["verdict"] in adversary.VERDICTS
    assert 0 < found["sims"] <= 6
    assert found["rows"]
    assert adversary.claim(found)
