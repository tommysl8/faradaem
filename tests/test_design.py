"""The design iterator: scoring, the search itself, and the job API.

The search is proved on synthetic evaluators whose feasible regions are known
exactly, so its behaviour is pinned without a simulator. One live test at the
bottom drives the whole stack against real ngspice on the macromodel circuit,
where an evaluation costs milliseconds rather than a PDK library load.
"""

import json
import math
import time

import pytest

import server
from spice import circuits, design
from tests.test_routes import address, fetch  # noqa: F401 - shared live-server fixture
from spice.runner import NgspiceParseError, find_ngspice

GOALS = [{"key": "m", "label": "measure", "op": ">=", "unit": "", "default": 10.0}]


# ---- margins ---------------------------------------------------------------


def test_margin_directions():
    assert design.goal_margin(">=", 10.0, 15.0) == pytest.approx(0.5)
    assert design.goal_margin(">=", 10.0, 5.0) == pytest.approx(-0.5)
    assert design.goal_margin("<=", 10.0, 5.0) == pytest.approx(0.5)
    assert design.goal_margin("<=", 10.0, 15.0) == pytest.approx(-0.5)


def test_score_is_the_worst_margin():
    goals = [
        {"key": "a", "op": ">=", "label": "", "unit": "", "default": 1.0},
        {"key": "b", "op": "<=", "label": "", "unit": "", "default": 1.0},
    ]
    score, margins = design.score_measurement(
        goals, {"a": 10.0, "b": 100.0}, {"a": 20.0, "b": 90.0}
    )
    assert margins["a"] == pytest.approx(1.0)
    assert margins["b"] == pytest.approx(0.1)
    assert score == pytest.approx(0.1)


# ---- the search on synthetic evaluators ------------------------------------


def rising(params):
    """m grows with x, so the search must walk x upward."""
    return {"m": params["x"]}


def test_search_walks_to_a_reachable_target():
    result = design.optimize(
        rising, {"x": 1.0}, {"x": (0.1, 1000.0)}, GOALS, {"m": 10.0}, 60,
    )
    assert result["feasible"]
    assert result["best"]["measured"]["m"] >= 10.0
    assert result["evals"] <= 60
    assert "met" in result["reason"]


def test_search_stops_at_the_first_feasible_point():
    calls = []

    def counting(params):
        calls.append(dict(params))
        return {"m": params["x"]}

    design.optimize(counting, {"x": 1.0}, {"x": (0.1, 1000.0)}, GOALS, {"m": 1.5}, 60)
    # x doubles once to 2.0, which is already past 1.5. Nothing runs after it.
    assert len(calls) == 2


def test_search_respects_bounds():
    result = design.optimize(
        rising, {"x": 1.0}, {"x": (0.1, 4.0)}, GOALS, {"m": 100.0}, 60,
    )
    assert not result["feasible"]
    assert all(entry["params"]["x"] <= 4.0 + 1e-9 for entry in result["history"])
    assert result["best"]["params"]["x"] == pytest.approx(4.0, rel=1e-6)
    assert "converged" in result["reason"]


def test_search_runs_out_of_budget_honestly():
    result = design.optimize(
        rising, {"x": 1.0}, {"x": (0.1, 1e12)}, GOALS, {"m": 1e9}, 7,
    )
    assert not result["feasible"]
    assert result["evals"] == 7
    assert "budget" in result["reason"]


def test_a_feasible_start_costs_one_evaluation():
    result = design.optimize(
        rising, {"x": 50.0}, {"x": (0.1, 1000.0)}, GOALS, {"m": 10.0}, 60,
    )
    assert result["feasible"]
    assert result["evals"] == 1
    assert "starting point" in result["reason"]


def test_unmeasurable_candidates_are_recorded_and_skipped():
    def spiky(params):
        if params["x"] > 3.0:
            raise NgspiceParseError("nothing to measure here")
        return {"m": params["x"]}

    result = design.optimize(
        spiky, {"x": 1.0}, {"x": (0.1, 1000.0)}, GOALS, {"m": 100.0}, 30,
    )
    assert not result["feasible"]
    errors = [entry for entry in result["history"] if entry["error"]]
    assert errors, "the failing region was never even probed"
    assert result["best"]["measured"]["m"] <= 3.0


def test_should_stop_ends_the_search():
    seen = []

    def watched(params):
        seen.append(1)
        return {"m": params["x"]}

    result = design.optimize(
        watched, {"x": 1.0}, {"x": (0.1, 1e9)}, GOALS, {"m": 1e6}, 100,
        should_stop=lambda: len(seen) >= 5,
    )
    assert result["evals"] == 5
    assert "Stopped" in result["reason"]


def test_two_dimensional_search_finds_a_corner():
    """Both knobs must move: m is the smaller of the two."""
    def corner(params):
        return {"m": min(params["x"], params["y"])}

    result = design.optimize(
        corner, {"x": 1.0, "y": 1.0}, {"x": (0.1, 100.0), "y": (0.1, 100.0)},
        GOALS, {"m": 6.0}, 80,
    )
    assert result["feasible"]
    assert result["best"]["params"]["x"] >= 6.0
    assert result["best"]["params"]["y"] >= 6.0


# ---- request validation ----------------------------------------------------


def test_targets_merge_over_defaults():
    _, block = design.design_block("twopole_amp")
    targets = design.resolve_targets(block, {"phase_margin": 70})
    assert targets["phase_margin"] == 70.0
    assert targets["f_crossover"] == 1e5


@pytest.mark.parametrize(
    "requested,fragment",
    [
        ({"nonsense": 1}, "Unknown target"),
        ({"phase_margin": "abc"}, "must be a number"),
        ({"phase_margin": -5}, "positive"),
        ({"phase_margin": 0}, "positive"),
    ],
)
def test_bad_targets_are_named(requested, fragment):
    _, block = design.design_block("twopole_amp")
    with pytest.raises(design.DesignError) as excinfo:
        design.resolve_targets(block, requested)
    assert fragment in str(excinfo.value)


def test_a_circuit_without_a_design_block_is_refused():
    with pytest.raises(design.DesignError) as excinfo:
        design.design_block("divider")
    assert "does not declare a design block" in str(excinfo.value)


def test_validate_design_request_shapes():
    body = {
        "circuit": "twopole_amp",
        "params": circuits.defaults("twopole_amp"),
        "targets": {"phase_margin": 65},
        "max_evals": 20,
    }
    circuit_id, params, targets, max_evals = server.validate_design_request(body)
    assert circuit_id == "twopole_amp"
    assert targets["phase_margin"] == 65.0
    assert max_evals == 20


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda b: b.update(circuit="divider",
                            params=circuits.defaults("divider")), "design block"),
        (lambda b: b.update(max_evals=1), "between"),
        (lambda b: b.update(max_evals=10000), "between"),
        (lambda b: b.update(max_evals="lots"), "whole number"),
        (lambda b: b.update(targets={"zap": 1}), "Unknown target"),
        (lambda b: b["params"].pop("gbw"), "Missing required parameter"),
    ],
)
def test_validate_design_request_rejects(mutate, fragment):
    body = {
        "circuit": "twopole_amp",
        "params": dict(circuits.defaults("twopole_amp")),
        "targets": {},
        "max_evals": 20,
    }
    mutate(body)
    with pytest.raises(server.ValidationError) as excinfo:
        server.validate_design_request(body)
    assert fragment in str(excinfo.value)


# ---- the job API over a live socket ----------------------------------------


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(),
    reason="ngspice is not available, so the live design job cannot run",
)


@requires_ngspice
def test_design_job_reaches_the_spec_over_http(address):
    start = dict(circuits.defaults("twopole_amp"), fp2=1e4)
    status, _, body = fetch(address, "/api/design", "POST", json.dumps({
        "circuit": "twopole_amp",
        "params": start,
        "targets": {},
        "max_evals": 60,
    }))
    assert status == 200
    job = json.loads(body)["job"]

    deadline = time.time() + 60.0
    snapshot = None
    while time.time() < deadline:
        status, _, body = fetch(address, "/api/design/status?job=" + job)
        assert status == 200
        snapshot = json.loads(body)
        if snapshot["status"] != "running":
            break
        time.sleep(0.1)

    assert snapshot is not None
    assert snapshot["status"] == "done", snapshot
    assert snapshot["feasible"] is True
    best = snapshot["best"]
    assert best["measured"]["phase_margin"] >= 60.0
    assert best["measured"]["f_crossover"] >= 1e5
    assert best["params"]["fp2"] > 1e4


def test_design_status_of_an_unknown_job_is_404(address):
    status, _, body = fetch(address, "/api/design/status?job=nope")
    assert status == 404
    assert "Unknown design job" in json.loads(body)["error"]


def test_design_stop_of_an_unknown_job_is_404(address):
    status, _, body = fetch(address, "/api/design/stop", "POST",
                            json.dumps({"job": "nope"}))
    assert status == 404


# ---- spec-first seeding ----------------------------------------------------


def test_seed_scales_with_the_spec():
    params = circuits.defaults("opamp_two_stage")
    seeded, targets = design.seed_params(
        "opamp_two_stage", {"power": 5e-5}, params
    )
    # Compensation tracks the load; bias tracks the budget.
    assert seeded["cc"] == pytest.approx(0.5 * params["cl"])
    assert seeded["ibias"] == pytest.approx(5e-5 / 12.0)
    assert targets["power"] == 5e-5
    # Untouched givens survive.
    assert seeded["cl"] == params["cl"]
    assert seeded["l"] == params["l"]


def test_seed_stays_inside_every_declared_range():
    params = circuits.defaults("opamp_two_stage")
    specs = {spec["key"]: spec for spec in
             circuits.get_circuit("opamp_two_stage")["params"]}
    for power in (1e-6, 5e-5, 2e-4, 1.0):
        for cl in (1e-13, 2e-12, 1e-10):
            case = dict(params, cl=cl)
            seeded, _ = design.seed_params(
                "opamp_two_stage", {"power": power}, case
            )
            for key, value in seeded.items():
                assert specs[key]["min"] <= value <= specs[key]["max"], (
                    power, cl, key, value
                )


def test_seed_is_refused_where_no_rule_exists():
    with pytest.raises(design.DesignError) as excinfo:
        design.seed_params("twopole_amp", {}, circuits.defaults("twopole_amp"))
    assert "seed rule" in str(excinfo.value)


def test_seed_endpoint_returns_params_and_resolved_targets(address):
    status, _, body = fetch(address, "/api/design/seed", "POST", json.dumps({
        "circuit": "opamp_two_stage",
        "params": circuits.defaults("opamp_two_stage"),
        "targets": {"power": 5e-5},
    }))
    assert status == 200
    payload = json.loads(body)
    assert payload["params"]["ibias"] == pytest.approx(5e-5 / 12.0)
    assert payload["targets"]["phase_margin"] == 60.0


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"circuit": "twopole_amp", "params": "DEFAULTS", "targets": {}},
         "seed rule"),
        ({"circuit": "opamp_two_stage", "params": "DEFAULTS",
          "targets": {"nope": 1}}, "Unknown target"),
    ],
)
def test_seed_endpoint_rejects(address, body, fragment):
    body = dict(body)
    body["params"] = circuits.defaults(body["circuit"])
    status, _, payload = fetch(address, "/api/design/seed", "POST",
                               json.dumps(body))
    assert status == 400
    assert fragment in json.loads(payload)["error"]


def sky130_present():
    from spice.runner import sky130_available
    return sky130_available()


@requires_ngspice
def test_generate_flow_reaches_the_spec_end_to_end():
    """The whole story: spec in, seeded design, iterate only if short."""
    if not sky130_present():
        pytest.skip("the SKY130 model library is not installed")

    params = circuits.defaults("opamp_two_stage")
    targets_in = {"loop_gain_db": 60.0, "f_crossover": 5e6,
                  "phase_margin": 60.0, "power": 2e-4}
    seeded, targets = design.seed_params("opamp_two_stage", targets_in, params)

    measured = circuits.simulate("opamp_two_stage", seeded)
    goals = circuits.get_circuit("opamp_two_stage")["design"]["goals"]
    score, _ = design.score_measurement(goals, targets, measured)

    if score < 0:
        result = design.run_design("opamp_two_stage", seeded, targets_in, 40)
        assert result["feasible"], result["reason"]
        best = result["best"]["measured"]
    else:
        best = {item["key"]: measured[item["key"]] for item in goals}

    assert best["loop_gain_db"] >= 60.0
    assert best["f_crossover"] >= 5e6
    assert best["phase_margin"] >= 60.0
    assert best["power"] <= 2e-4
