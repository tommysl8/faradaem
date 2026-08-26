"""The four-way comparison, and the properties that make it evidence.

Almost everything that makes four design methods comparable is a decision
that can be got wrong quietly: a spec everyone already meets, an arm
starting from another arm's answer, a cost axis that counts one arm's work
and not another's, a model scored on a point it measured and rejected.
Each test here is one of those.
"""

import math

import pytest

from spice import circuits, experiment, ledger, runner

requires_ngspice = pytest.mark.skipif(
    not runner.find_ngspice(),
    reason="ngspice is needed to measure anything",
)


# ---------------------------------------------------------------------------
# the spec
# ---------------------------------------------------------------------------


def test_the_spec_freezes_the_problem_and_not_the_answer():
    """The load a circuit drives is part of the question. An arm allowed to
    lower it is answering an easier one."""
    assert "cl" in experiment.AMP1["fixed"]
    tunable = circuits.get_circuit(experiment.AMP1["circuit"])["design"]["tunable"]
    for key in experiment.AMP1["fixed"]:
        assert key not in tunable, key


def test_every_target_names_a_goal_the_circuit_declares():
    block = circuits.get_circuit(experiment.AMP1["circuit"])["design"]
    declared = {goal["key"] for goal in block["goals"]}
    assert set(experiment.AMP1["targets"]) == declared


def test_a_fixed_condition_cannot_be_moved_and_the_attempt_is_named():
    params, overridden = experiment.apply_fixed(
        experiment.AMP1, {"cl": 9e-12, "ibias": 3e-5})
    assert params["cl"] == experiment.AMP1["fixed"]["cl"]
    assert overridden == ["cl"]
    # And what was legitimately set survives.
    assert params["ibias"] == 3e-5


def test_apply_fixed_returns_a_complete_parameter_set():
    params, _ = experiment.apply_fixed(experiment.AMP1, {"ibias": 1e-5})
    declared = {item["key"]
                for item in circuits.get_circuit(
                    experiment.AMP1["circuit"])["params"]}
    assert set(params) == declared


# ---------------------------------------------------------------------------
# the cold start
# ---------------------------------------------------------------------------


def test_the_numerical_arm_does_not_start_from_the_hand_design():
    """The registry defaults are byte-identical to the Balanced preset, so
    starting there is starting from the arm it is being compared against."""
    reference = circuits.defaults(experiment.AMP1["circuit"])
    cold = experiment.log_centre(experiment.AMP1)
    assert any(abs(cold[key] - reference[key]) > 1e-18 for key in cold)


def test_the_cold_start_is_the_centre_of_every_declared_box():
    cold = experiment.log_centre(experiment.AMP1)
    for name, (low, high) in experiment.bounds_of(experiment.AMP1).items():
        middle = 10.0 ** ((math.log10(low) + math.log10(high)) / 2.0)
        assert cold[name] == pytest.approx(middle, rel=1e-6)
        assert low <= cold[name] <= high


def test_random_starts_stay_inside_the_box_and_differ():
    import random
    rng = random.Random(7)
    points = [experiment.random_start(experiment.AMP1, rng) for _ in range(5)]
    for point in points:
        for name, (low, high) in experiment.bounds_of(experiment.AMP1).items():
            assert low <= point[name] <= high
    assert len({tuple(sorted(p.items())) for p in points}) == 5


def test_a_seeded_random_start_is_reproducible():
    """A restart nobody can reproduce is an anecdote."""
    import random
    one = experiment.random_start(experiment.AMP1, random.Random(11))
    two = experiment.random_start(experiment.AMP1, random.Random(11))
    assert one == two


# ---------------------------------------------------------------------------
# the pre-flight
# ---------------------------------------------------------------------------


@requires_ngspice
def test_the_spec_is_not_one_the_reference_already_meets():
    """At the registry defaults every circuit passes its own targets, so
    the optimizer returns on its first evaluation and all four arms produce
    the same row. This is the check that stops that being published."""
    found = experiment.preflight(experiment.AMP1)
    assert found["vacuous"] is False
    assert found["worst_margin"] < 0.0
    assert found["binding_goal"] in experiment.AMP1["targets"]


@requires_ngspice
def test_a_spec_the_reference_already_meets_is_refused():
    easy = dict(experiment.AMP1, id="EASY", targets={
        "loop_gain_db": 40.0, "f_crossover": 1e6,
        "phase_margin": 45.0, "power": 1e-3,
    })
    with pytest.raises(experiment.ExperimentError) as caught:
        experiment.preflight(easy)
    assert "four identical rows" in str(caught.value)


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def test_four_approaches_cannot_be_ranked_when_fewer_than_four_ran():
    """The plan asks for a comparison of four. Three and a blank is not
    that comparison, and a table with one row marked pending invites
    exactly the reading it should not get."""
    results = [
        {"arm": "reference", "status": "completed", "feasible_nominal": True,
         "sims_total": 1},
        {"arm": "optimizer", "status": "completed", "feasible_nominal": True,
         "sims_total": 20},
        {"arm": "llm", "status": "not_run", "why": "no API key"},
        {"arm": "llm_optimizer", "status": "not_run", "why": "no API key"},
    ]
    with pytest.raises(experiment.PartialComparisonError) as caught:
        experiment.ranked(results)
    assert "llm" in str(caught.value)


def test_the_table_says_which_arms_did_not_run():
    results = [
        {"arm": "reference", "status": "completed", "feasible_nominal": False,
         "binding_goal": "f_crossover", "sims_total": 1,
         "declared_measured": {"loop_gain_db": 71.5, "f_crossover": 1.36e7,
                               "phase_margin": 74.6, "power": 1.56e-4}},
        {"arm": "llm", "status": "not_run", "why": "no API key is set"},
    ]
    text = experiment.table(results)
    assert "Did not run: llm" in text
    assert "No ranking of four approaches is possible" in text


def test_an_arm_that_did_not_complete_is_never_given_a_result_row():
    results = [
        {"arm": "optimizer", "status": "budget_exhausted",
         "reason": "spent its budget", "sims_total": 60},
    ]
    text = experiment.table(results)
    assert "budget_exhausted" in text
    # No measured numbers are printed for it.
    assert "gain" not in text.split("\n")[-1]


def test_an_unknown_status_is_refused():
    with pytest.raises(experiment.ExperimentError):
        experiment._result("optimizer", "went_fine")


def test_the_module_refuses_to_name_a_human_design_arm():
    """The reference sizing did not see the spec. Calling it a human design
    and putting it against arms that did is circular."""
    source = open(experiment.__file__, encoding="utf-8").read().lower()
    flowed = " ".join(source.split())
    assert "it is not called a human design" in flowed
    # The arm is named for what it is: the sizing already in the registry.
    assert "def arm_reference" in source
    assert "def arm_human" not in source


# ---------------------------------------------------------------------------
# the arms, against the real simulator
# ---------------------------------------------------------------------------


@requires_ngspice
def test_the_reference_arm_measures_once_and_declares_what_it_measured(tmp_path):
    book = ledger.Ledger(directory=str(tmp_path), stamp_provenance=False)
    found = experiment.arm_reference(experiment.AMP1, book)

    assert found["status"] == "completed"
    assert found["searched"] is False
    assert found["sims_total"] == 1
    assert found["declared_params"] == circuits.defaults(
        experiment.AMP1["circuit"])
    # And it is recorded, so the run can be compared afterwards.
    kinds = {item["kind"] for item in ledger.read(book.path)["records"]}
    assert {"start", "sim", "result"} <= kinds


def test_an_llm_arm_without_a_key_is_not_run_and_never_a_result(
        tmp_path, monkeypatch):
    """A key that is missing must produce an arm that did not run, never an
    arm that ran badly, and never a measured row."""
    from spice import llm, strategist

    def refuse(provider):
        raise llm.LlmError("no key for " + provider)

    monkeypatch.setattr(llm, "get_client", refuse)
    book = ledger.Ledger(directory=str(tmp_path), stamp_provenance=False)
    tools = [t for t in strategist.TOOLS if t["name"] == "simulate"]
    found = experiment.arm_llm(experiment.AMP1, "openai", tools, "llm",
                               book, message="design it")

    assert found["status"] == "not_run"
    assert "declared_measured" not in found
    assert found["why"]
