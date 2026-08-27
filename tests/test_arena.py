"""The arena: one set of rules, any contestant, a scoreboard that ranks
met-and-cheap above met-and-expensive above everything else.

The contract is proved with contestants the tests control; the live
contest runs the reference contestants on the two-pole macromodel under
a spec its registry sizing provably misses.
"""

import pytest

from spice import arena, runner
from spice.runner import find_ngspice


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(), reason="a real ngspice is needed",
)


#: The two-pole macromodel's registry sizing measures 54 degrees of phase
#: margin, so a 60 degree floor is a spec it provably misses and the
#: pre-flight accepts. Cheap enough that the whole contest is a test.
SPEC = {
    "id": "ARENA-T1",
    "circuit": "twopole_amp",
    "targets": {"phase_margin": 60.0, "f_crossover": 1.5e5},
    "fixed": {"rin": 1000.0, "rf": 10000.0, "a0": 1e5},
    "budget": 40,
}


def test_spec_view_withholds_the_registry_sizing():
    view = arena.spec_view(SPEC)
    assert view["tunable"] == ["gbw", "fp2"]
    assert "defaults" not in view
    assert view["bounds"]["gbw"][0] > 0
    assert view["budget"] == 40


def test_contest_refuses_an_empty_roster():
    with pytest.raises(arena.ArenaError):
        arena.contest(SPEC, {})


def test_scoreboard_ranks_feasible_cheap_first():
    results = [
        {"arm": "slow_but_met", "status": "completed",
         "feasible_nominal": True, "sims_total": 40,
         "margins": {"phase_margin": 0.05}},
        {"arm": "fast_and_met", "status": "completed",
         "feasible_nominal": True, "sims_total": 9,
         "margins": {"phase_margin": 0.01}},
        {"arm": "cheap_but_missed", "status": "completed",
         "feasible_nominal": False, "sims_total": 2,
         "margins": {"phase_margin": -0.4}},
        {"arm": "crashed", "status": "error", "sims_total": 0},
    ]
    board = arena.scoreboard(results)
    assert [row["arm"] for row in board] == [
        "fast_and_met", "slow_but_met", "cheap_but_missed", "crashed"]


def test_a_crashing_contestant_is_a_result_not_a_crash():
    def saboteur(view, tools):
        raise RuntimeError("the method fell over")

    found = arena.run_contestant(SPEC, "saboteur", saboteur)
    assert found["status"] == "error"
    assert found["reason"] == "the method fell over"


def test_a_contestant_that_declares_nothing_is_aborted():
    found = arena.run_contestant(SPEC, "mute", lambda view, tools: None)
    assert found["status"] == "aborted"


@requires_ngspice
def test_the_budget_ends_a_spendthrift_mid_thought():
    def spendthrift(view, tools):
        centre = {name: (low * high) ** 0.5
                  for name, (low, high) in view["bounds"].items()}
        while True:
            tools["simulate"](centre)

    found = arena.run_contestant(SPEC, "spendthrift", spendthrift)
    assert found["status"] == "budget_exhausted"
    assert found["sims_total"] == SPEC["budget"]


@requires_ngspice
def test_frozen_parameters_overwrite_a_contestants_values():
    declared = {}

    def meddler(view, tools):
        declared.update(view["fixed"])
        return {"gbw": 3e6, "fp2": 1.2e6, "a0": 1e9}

    found = arena.run_contestant(SPEC, "meddler", meddler)
    assert found["declared_params"]["a0"] == SPEC["fixed"]["a0"]


def test_a_missing_tunable_is_refused_by_the_toolbox():
    def half_hearted(view, tools):
        return tools["simulate"]({"gbw": 1e6})  # fp2 left to "the default"

    found = arena.run_contestant(SPEC, "half_hearted", half_hearted)
    assert found["status"] == "error"
    assert "withheld on purpose" in found["reason"]


def test_an_out_of_bounds_declaration_is_an_error_not_a_score():
    for cheat_params in ({"gbw": 1e12, "fp2": 1e6},        # beyond the max
                         {"gbw": float("nan"), "fp2": 1e6}):  # not a number
        found = arena.run_contestant(SPEC, "cheat",
                                     lambda view, tools: cheat_params)
        assert found["status"] == "error"
        assert "outside the declared box" in found["reason"]


def test_a_design_that_will_not_simulate_is_a_result_not_a_crash(monkeypatch):
    def boom(spec, params, book, name):
        raise runner.NgspiceRunError("the bias collapsed")

    monkeypatch.setattr(arena.experiment, "_verify", boom)
    found = arena.run_contestant(
        SPEC, "confident", lambda view, tools: {"gbw": 3e6, "fp2": 1e6})
    assert found["status"] == "error"
    assert "would not simulate" in found["reason"]


@requires_ngspice
def test_a_spent_budget_that_missed_the_spec_reads_budget_exhausted():
    spec = dict(SPEC, id="ARENA-T2", budget=8,
                targets={"phase_margin": 89.0, "f_crossover": 5e6})
    found = arena.run_contestant(spec, "cold", arena.optimizer_contestant)
    assert found["status"] == "budget_exhausted"


@requires_ngspice
def test_frozen_overwrites_are_recorded():
    def meddler(view, tools):
        return {"gbw": 3e6, "fp2": 1.2e6, "a0": 1e9}

    found = arena.run_contestant(SPEC, "meddler", meddler)
    assert found["overridden"] == ["a0"]


@requires_ngspice
def test_the_reference_contestants_run_a_full_live_contest():
    found = arena.contest(SPEC, {
        "optimizer_cold": arena.optimizer_contestant,
        "optimizer_seeded": arena.seeded_contestant,
    })
    assert found["preflight"]["vacuous"] is False
    assert len(found["results"]) == 2
    board = found["scoreboard"]
    assert board[0]["rank"] == 1
    met = [row for row in board if row["feasible"]]
    assert met, "the shipped search should meet this spec within budget"
    for result in found["results"]:
        assert result["sims_total"] <= SPEC["budget"] + 1
