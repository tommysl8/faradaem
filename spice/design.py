"""The design iterator: a spec, a search, and a real simulator in the loop.

This module turns "I want an amplifier with these numbers" into a sequence of
ngspice runs. It never computes a circuit value: every candidate the search
considers is simulated for real, and the only arithmetic here is comparing
measured numbers against the targets the user asked for.

The search itself is deliberately plain: coordinate descent on the logarithm
of each tunable component, with a step that halves whenever a full pass finds
no improvement. Component values live on ratio scales, which is why the search
walks exponents rather than values, and the objective is the worst normalised
margin across the goals, so the search always works on whichever target is
furthest from being met. The search stops the moment every goal is satisfied.
"""

from __future__ import annotations

import math

from . import circuits, runner

#: Initial step, in decades: one step doubles or halves a component.
INITIAL_STEP = math.log10(2.0)

#: The search has converged when a step moves a value by less than about 5%.
MIN_STEP = math.log10(1.05)

#: Errors that mean "this candidate cannot be measured", not "stop searching".
CANDIDATE_ERRORS = (
    circuits.CircuitInputError,
    runner.NgspiceParseError,
    runner.NgspiceRunError,
)


class DesignError(ValueError):
    """A design request that cannot be run. Maps to HTTP 400."""


def goal_margin(op, target, value):
    """How far a measured value is past its target, as a fraction of the target.

    Positive means the goal is met with room to spare, zero means exactly met,
    negative means missed. Both directions normalise by the target, so a
    margin of -0.5 always reads "half the target short", whatever the unit.
    """
    if op == ">=":
        return value / target - 1.0
    if op == "<=":
        return 1.0 - value / target
    raise ValueError("Unknown goal op " + repr(op))


def score_measurement(goals, targets, measured):
    """The worst margin across all goals, plus each goal's own margin."""
    margins = {}
    worst = math.inf
    for item in goals:
        value = measured[item["key"]]
        margin = goal_margin(item["op"], targets[item["key"]], value)
        margins[item["key"]] = margin
        worst = min(worst, margin)
    return worst, margins


def optimize(evaluate, start, bounds, goals, targets, max_evals,
             on_eval=None, should_stop=None):
    """Search the tunable space until every goal is met or the budget runs out.

    evaluate(params) must return a measured dict holding every goal key, or
    raise one of CANDIDATE_ERRORS to mark the point unmeasurable. start maps
    every tunable to its initial value; bounds maps each to (low, high).

    Returns a dict with the best point found, the full history, whether it is
    feasible, and why the search ended.
    """
    names = sorted(start)
    logs = {name: math.log10(start[name]) for name in names}
    log_bounds = {
        name: (math.log10(bounds[name][0]), math.log10(bounds[name][1]))
        for name in names
    }
    for name in names:
        low, high = log_bounds[name]
        logs[name] = min(max(logs[name], low), high)

    state = {"evals": 0, "history": [], "best": None}

    # Coordinate descent revisits points as it steps back and forth. Each of
    # those would be a whole ngspice run, so measured points are remembered.
    seen = {}

    def point(log_values):
        # Ten significant figures: enough to be exact for any component value,
        # short enough that 10**log10 round-trip noise never reaches a netlist
        # or the form the result is applied back into.
        return {
            name: float("%.10g" % (10.0 ** log_values[name])) for name in names
        }

    def run(log_values):
        """Returns (entry, fresh): fresh is False for a memoised revisit."""
        key = tuple(round(log_values[name], 9) for name in names)
        if key in seen:
            return seen[key], False
        params = point(log_values)
        state["evals"] += 1
        try:
            measured = evaluate(params)
            score, margins = score_measurement(goals, targets, measured)
            entry = {
                "evals": state["evals"],
                "params": params,
                "measured": {item["key"]: measured[item["key"]] for item in goals},
                "margins": margins,
                "score": score,
                "feasible": score >= 0.0,
                "error": None,
            }
        except CANDIDATE_ERRORS as exc:
            entry = {
                "evals": state["evals"],
                "params": params,
                "measured": None,
                "margins": None,
                "score": None,
                "feasible": False,
                "error": str(exc).splitlines()[0],
            }
        seen[key] = entry
        state["history"].append(entry)
        if entry["score"] is not None and (
            state["best"] is None
            or state["best"]["score"] is None
            or entry["score"] > state["best"]["score"]
        ):
            state["best"] = entry
        if on_eval is not None:
            on_eval(entry, state["best"])
        return entry, True

    def finished(reason):
        best = state["best"]
        return {
            "best": best,
            "history": state["history"],
            "feasible": bool(best and best["feasible"]),
            "evals": state["evals"],
            "reason": reason,
        }

    def out_of_budget():
        return state["evals"] >= max_evals

    def stopping():
        return should_stop is not None and should_stop()

    current, _ = run(dict(logs))
    if current["feasible"]:
        return finished("The starting point already meets every target.")
    if out_of_budget():
        return finished("The evaluation budget ran out.")
    if stopping():
        return finished("Stopped on request.")

    step = INITIAL_STEP
    here = dict(logs)
    here_score = current["score"]

    while True:
        improved = False
        for name in names:
            for direction in (1.0, -1.0):
                low, high = log_bounds[name]
                candidate = dict(here)
                moved = min(max(candidate[name] + direction * step, low), high)
                if abs(moved - candidate[name]) < 1e-12:
                    continue
                candidate[name] = moved

                entry, fresh = run(candidate)

                if entry["feasible"]:
                    return finished("Every target is met.")
                if out_of_budget():
                    return finished("The evaluation budget ran out.")
                if stopping():
                    return finished("Stopped on request.")

                if entry["score"] is None:
                    continue

                # Strictly better always moves. An exact tie moves only onto a
                # freshly measured point: measurements can sit on plateaus
                # where one knob alone changes nothing, and drifting along the
                # plateau lets the next knob find the edge. Restricting drift
                # to fresh points is what makes a cycle impossible.
                better = here_score is None or entry["score"] > here_score
                drifts = (
                    here_score is not None
                    and entry["score"] == here_score
                    and fresh
                )
                if better or drifts:
                    here = dict(candidate)
                    here_score = entry["score"]
                    improved = True

        if not improved:
            step /= 2.0
            if step < MIN_STEP:
                return finished(
                    "The search converged without meeting every target. "
                    "The best point found is reported; loosen a target or "
                    "start from different values to search elsewhere."
                )


def design_block(circuit_id):
    """The declared design surface of a circuit, or a DesignError."""
    circuit = circuits.get_circuit(circuit_id)
    block = circuit.get("design")
    if not block:
        raise DesignError(
            "Circuit " + repr(circuit_id) + " does not declare a design block, "
            "so there is nothing to optimize. Pick a circuit that does."
        )
    return circuit, block


def resolve_targets(block, requested):
    """Merge requested targets over the declared defaults, validating keys."""
    known = {item["key"]: item for item in block["goals"]}
    targets = {key: item["default"] for key, item in known.items()}

    for key, raw in (requested or {}).items():
        if key not in known:
            raise DesignError(
                "Unknown target " + repr(key) + ". This circuit's targets are: "
                + ", ".join(sorted(known)) + "."
            )
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise DesignError(
                "Target " + repr(key) + " must be a number, got " + repr(raw) + "."
            ) from None
        if not math.isfinite(value) or value <= 0.0:
            raise DesignError(
                "Target " + repr(key) + " must be a positive number, got "
                + repr(raw) + "."
            )
        targets[key] = value

    return targets


def seed_params(circuit_id, targets, params):
    """A starting point generated from the spec alone.

    The circuit's seed rule proposes values; every parameter is then clamped
    to its declared range, the same guarantee the optimizer has. Returns the
    complete seeded parameter set and the resolved targets.
    """
    circuit, block = design_block(circuit_id)
    seed = block.get("seed")
    if seed is None:
        raise DesignError(
            "Circuit " + repr(circuit_id) + " does not provide a seed rule, so "
            "a design cannot be generated from the spec alone. Start from a "
            "preset and optimize instead."
        )
    targets = resolve_targets(block, targets)

    proposed = seed(targets, dict(params))
    seeded = {}
    for spec in circuit["params"]:
        key = spec["key"]
        value = float(proposed.get(key, params[key]))
        value = min(max(value, spec["min"]), spec["max"])
        # Ten significant figures, like the optimizer: exact for any component
        # value, and clean in the form the seed lands in.
        seeded[key] = float("%.10g" % value)
    return seeded, targets


def run_design(circuit_id, params, targets, max_evals,
               on_eval=None, should_stop=None, transform=None, ledger=None,
               arm=None):
    """Optimize one catalogued circuit toward a set of targets.

    params is the complete parameter set to start from; only the circuit's
    declared tunables move, and each stays inside its own declared min/max.

    transform is a netlist edit applied to every candidate, so a search can
    be run against something other than the ideal schematic. The one that
    matters is the drawn interconnect: sizing against a deck that does not
    have the wiring in it optimises a circuit nobody is going to build.

    ledger, if given, records every evaluation as it happens, so the run can
    be compared against another method afterwards rather than remembered.
    """
    circuit, block = design_block(circuit_id)
    targets = resolve_targets(block, targets)
    specs = {spec["key"]: spec for spec in circuit["params"]}

    start = {}
    bounds = {}
    for name in block["tunable"]:
        start[name] = float(params[name])
        bounds[name] = (specs[name]["min"], specs[name]["max"])

    fixed = dict(params)

    def evaluate(tuned):
        candidate = dict(fixed)
        candidate.update(tuned)
        return circuits.simulate(circuit_id, candidate, transform=transform)

    def watch(entry, best):
        if ledger is not None:
            ledger.record(
                "attempt", arm=arm, circuit=circuit_id, by="optimizer",
                params=entry.get("params"), measured=entry.get("measured"),
                margins=entry.get("margins"), score=entry.get("score"),
                feasible=entry.get("feasible"), error=entry.get("error"),
                loaded=transform is not None,
            )
        if on_eval is not None:
            on_eval(entry, best)

    result = optimize(
        evaluate, start, bounds, block["goals"], targets, max_evals,
        on_eval=watch, should_stop=should_stop,
    )
    result["targets"] = targets
    result["loaded"] = transform is not None
    return result
