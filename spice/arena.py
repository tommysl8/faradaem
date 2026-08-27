"""The referee: any design method, scored under one set of rules.

The four-way comparison proved that methods can be compared honestly when
the comparison is built for it. This module opens that harness to methods
it has never met. A contestant is one callable; the arena gives it a spec
and a toolbox, counts every simulation it spends at the ngspice
subprocess, and scores the design it declares. Nothing about the
contestant's insides is trusted, because nothing needs to be: the rules
are enforced where they cannot be argued with.

THE RULES, in full:

1. The spec is proved non-vacuous first. A spec the reference sizing
   already meets is refused, because every contestant would tie on their
   first simulation and the scoreboard would be noise.
2. Every contestant gets the same simulation budget, enforced at the
   subprocess. Spending it all ends the run mid-thought.
3. Contestants start cold. The toolbox holds the simulator, the circuit's
   declared bounds, the seed rule, and the shipped optimizer; it does not
   hold the registry sizing, which is a designer's answer.
4. The frozen parameters are the problem statement. A contestant that
   returns values for them has those values overwritten, and the
   overwrite is recorded.
5. A contestant is scored on the design it returns, re-simulated by the
   referee, never on the best point it happened to touch. The
   verification simulation is on the referee's account.
6. Everything lands in the ledger: every simulation, every declared
   design, every score. A result that cannot be replayed is not a result.

A contestant is:

    def my_method(spec, tools):
        ...
        return {"wpair": 2.4e-5, "ibias": 3.1e-5}   # the tunables

spec is a read-only view: circuit id, targets, fixed values, tunable
names, bounds, and budget. tools is the closed toolbox: simulate(params)
measures a candidate (counted); seed() proposes a start from the targets
when the circuit declares a seed rule; optimize(start, max_evals) runs
the shipped search. Raising SimBudgetExhausted, or anything else, ends
the contestant's run with the corresponding status; whatever it declared
before that is not scored, because it declared nothing.
"""

from __future__ import annotations

import time

from . import circuits, design, experiment, runner

#: What a contestant may end as. Mirrors the experiment's statuses.
STATUSES = experiment.STATUSES


class ArenaError(RuntimeError):
    """A contest that cannot be run or scored as intended."""


def spec_view(spec):
    """What a contestant is told. The registry defaults are not in it."""
    circuit = circuits.get_circuit(spec["circuit"])
    block = circuit["design"]
    return {
        "id": spec["id"],
        "circuit": spec["circuit"],
        "targets": dict(spec["targets"]),
        "fixed": dict(spec["fixed"]),
        "tunable": list(block["tunable"]),
        "bounds": {name: list(pair)
                   for name, pair in experiment.bounds_of(spec).items()},
        "goals": [dict(item) for item in block["goals"]],
        "budget": spec["budget"],
        "has_seed": bool(block.get("seed")),
    }


def _require_tunables(spec, params):
    """Every tunable must be given by the contestant. The registry sizing
    is withheld on purpose (rule 3), so nothing fills a gap silently."""
    block = circuits.get_circuit(spec["circuit"])["design"]
    missing = [name for name in block["tunable"]
               if name not in (params or {})]
    if missing:
        raise ArenaError(
            "Every tunable must be given: missing "
            + ", ".join(sorted(missing)) + ". The registry sizing is "
            "withheld on purpose, so nothing fills the gap."
        )


def _outside_bounds(spec, params):
    """Declared tunables that leave the declared box, or are not numbers.

    The search space is part of the rules: a contestant scored on a point
    outside the box was not solving the same problem as the others.
    Tunables the spec also freezes are exempt, because the freeze wins
    anyway and is recorded.
    """
    circuit = circuits.get_circuit(spec["circuit"])
    limits = {item["key"]: (item["min"], item["max"])
              for item in circuit["params"]}
    bad = []
    for name in circuit["design"]["tunable"]:
        if name in spec["fixed"] or name not in (params or {}):
            continue
        try:
            value = float(params[name])
        except (TypeError, ValueError):
            bad.append(name)
            continue
        low, high = limits[name]
        if not (low <= value <= high):
            bad.append(name)
    return bad


def _toolbox(spec):
    """The closed toolset a contestant works with. Every path into ngspice
    goes through the observer already watching this thread.

    One inherited semantic, shared with the experiment's arms: a tunable
    the spec also freezes is still walked by the shipped search, and the
    freeze is restored at scoring time, so the declared design is always
    judged at the frozen value. Consistent for every contestant, wasteful
    for the search, and recorded here so nobody rediscovers it as a bug.
    """
    def simulate(params):
        _require_tunables(spec, params)
        complete, _ = experiment.apply_fixed(spec, params)
        return circuits.simulate(spec["circuit"], complete)

    def seed(targets=None):
        complete, _ = experiment.apply_fixed(spec, {})
        seeded, _ = design.seed_params(
            spec["circuit"], targets or spec["targets"], complete)
        seeded, _ = experiment.apply_fixed(spec, seeded)
        return seeded

    def optimize(start, max_evals):
        _require_tunables(spec, start)
        complete, _ = experiment.apply_fixed(spec, start)
        return design.run_design(spec["circuit"], complete,
                                 spec["targets"], max_evals)

    return {"simulate": simulate, "seed": seed, "optimize": optimize}


def run_contestant(spec, name, contestant, book=None):
    """One contestant, one budget, one declared design, one score."""
    view = spec_view(spec)
    tools = _toolbox(spec)
    if book is not None:
        book.record("start", by="tool", arm=name, what="arena_contestant",
                    spec=spec["id"], budget=spec["budget"])

    watcher = runner.SimObserver(ledger=book, arm=name,
                                 budget=spec["budget"], phase="search")
    started = time.time()
    status, declared, note = "completed", None, None
    try:
        with runner.observing(watcher):
            declared = contestant(view, tools)
    except runner.SimBudgetExhausted:
        status = "budget_exhausted"
    except Exception as exc:  # noqa: BLE001 - a crashed contestant is a result
        status = "error"
        note = str(exc).splitlines()[0]

    if not isinstance(declared, dict) or not declared:
        found = {"arm": name, "status": status if status != "completed"
                 else "aborted",
                 "reason": note or "no design was declared",
                 "sims_total": watcher.count,
                 "wall_s": round(time.time() - started, 3)}
        if book is not None:
            book.record("result", by="tool", **found)
        return found

    bad = _outside_bounds(spec, declared)
    if bad:
        found = {"arm": name, "status": "error",
                 "reason": "declared " + ", ".join(sorted(bad)) + " outside "
                 "the declared box; the search space is part of the rules",
                 "sims_total": watcher.count,
                 "wall_s": round(time.time() - started, 3)}
        if book is not None:
            book.record("result", by="tool", **found)
        return found

    # Rule 4: anything the contestant tried to change that it was not
    # allowed to change is named, then overwritten by the verification.
    _, overridden = experiment.apply_fixed(spec, declared)

    # The referee's re-simulation is a result either way: a declared
    # design that will not simulate is a crashed declaration, not a
    # crashed contest (rule 5 scores it; it just scores nothing).
    try:
        verified = experiment._verify(spec, declared, book, name)
    except (circuits.CircuitInputError, runner.NgspiceParseError,
            runner.NgspiceRunError, ValueError, TypeError) as exc:
        found = {"arm": name, "status": "error",
                 "reason": "the declared design would not simulate: "
                 + str(exc).splitlines()[0],
                 "overridden": overridden,
                 "sims_total": watcher.count,
                 "wall_s": round(time.time() - started, 3)}
        if book is not None:
            book.record("result", by="tool", **found)
        return found

    # Budget exhaustion inside the shipped search is swallowed as
    # unmeasurable candidates (the same exception family), so a run that
    # spent everything and still missed was cut off, not completed. The
    # experiment's arms make the same correction.
    if (status == "completed" and not verified["feasible_nominal"]
            and watcher.count >= spec["budget"]):
        status = "budget_exhausted"

    found = {
        "arm": name, "status": status,
        "sims_search": watcher.count,
        "sims_total": watcher.count + verified["verify_sims"],
        "wall_s": round(time.time() - started, 3),
        "reason": note,
        "overridden": overridden,
    }
    found.update(verified)
    if book is not None:
        book.record("result", by="tool", **{
            key: found[key] for key in
            ("arm", "status", "sims_search", "sims_total", "wall_s",
             "feasible_nominal", "binding_goal", "overridden")})
    return found


def contest(spec, contestants, book=None, on_result=None):
    """Every contestant against one spec, then the scoreboard.

    contestants maps a name to a callable. The pre-flight runs first and
    refuses a vacuous spec; its finding is part of the returned record,
    because the reader deserves the proof, not the assurance.
    """
    if not contestants:
        raise ArenaError("A contest needs at least one contestant.")
    # The pre-flight's simulation lands in the ledger like everything
    # else (rule 6): a proof that was not recorded is an assurance.
    watcher = runner.SimObserver(ledger=book, arm="preflight",
                                 phase="preflight")
    with runner.observing(watcher):
        found = experiment.preflight(spec, book)
    results = []
    for name in sorted(contestants):
        result = run_contestant(spec, name, contestants[name], book)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return {"spec": spec["id"], "preflight": found,
            "results": results, "scoreboard": scoreboard(results)}


def scoreboard(results):
    """Feasible designs first, cheapest first; the rest by best margin.

    A method that met the spec in fewer simulations beat one that met it
    in more: the budget is the price axis of the whole project. Methods
    that did not meet the spec are ordered by how close they came, and
    are listed below every one that did, however cheap they were.
    """
    def rank(result):
        feasible = bool(result.get("feasible_nominal"))
        margin = (result.get("margins") or {})
        worst = min(margin.values()) if margin else float("-inf")
        return (0 if feasible else 1,
                result.get("sims_total", 0) if feasible else -worst)

    ordered = sorted(results, key=rank)
    return [{"rank": index + 1, "arm": entry["arm"],
             "status": entry["status"],
             "feasible": bool(entry.get("feasible_nominal")),
             "sims_total": entry.get("sims_total"),
             "worst_margin": (min(entry["margins"].values())
                              if entry.get("margins") else None)}
            for index, entry in enumerate(ordered)]


# ---------------------------------------------------------------------------
# reference contestants: the house methods, entered under the same rules
# ---------------------------------------------------------------------------


def optimizer_contestant(view, tools):
    """The shipped search, cold from the geometric centre of the box."""
    centre = {
        name: float((low * high) ** 0.5)
        for name, (low, high) in view["bounds"].items()
    }
    found = tools["optimize"](centre, view["budget"])
    best = found.get("best") or {}
    return best.get("params")


def seeded_contestant(view, tools):
    """The seed rule's proposal, polished by the shipped search."""
    if not view["has_seed"]:
        return optimizer_contestant(view, tools)
    start = tools["seed"]()
    found = tools["optimize"](start, view["budget"])
    best = found.get("best") or {}
    return best.get("params")
