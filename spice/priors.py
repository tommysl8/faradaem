"""What is experience worth? The ledger answers, in simulations saved.

Every design run this tool has ever made left its attempts in the ledger:
sizings, and the numbers ngspice measured for them. That pile of spent
simulations is the tool's experience, and this module puts it to work the
only honest way there is: as a starting point. Given a new spec, the
library of past measurements is re-scored against the new targets, at zero
simulations, because the measurements already exist, and the best past
design becomes the search's first candidate.

No model is fitted and nothing interpolates. A warm start is a measured
point, chosen by arithmetic over stored numbers; the search that follows
is the same coordinate descent as always, and every claim about the new
spec still comes from fresh simulation. The prior chooses where to stand,
never what to report.

learning_curve() is the experiment this enables: a sequence of specs,
each solved twice under the same counted budget, once cold from the
geometric centre of the box, once warm from the library of every attempt
made on the specs before it. The warm arm at spec N may only use
experience from specs 1..N-1, so the curve measures what accumulated
experience is worth at each step, in simulations, on the same axis every
other comparison in this project uses. Whether the curve bends is not
asserted here; it is what the experiment exists to find out.
"""

from __future__ import annotations

import math

from . import circuits, design, ledger, runner


class PriorsError(ValueError):
    """A priors request that cannot run. Maps to HTTP 400."""


# ---------------------------------------------------------------------------
# the library: measured attempts, harvested from the ledger
# ---------------------------------------------------------------------------


def harvest(circuit_id, directory=None):
    """Every measured attempt on this circuit found in the ledger.

    Attempts recorded against a loaded netlist (the drawn parasitics) are
    left out: they measured a different deck, and a prior built on them
    would answer a different question. Duplicated sizings keep one entry.
    """
    block = circuits.get_circuit(circuit_id).get("design")
    if not block:
        raise PriorsError(
            "Circuit " + repr(circuit_id) + " declares no design block, so "
            "there is no search for a prior to start."
        )
    tunable = block["tunable"]
    library, seen = [], set()
    for path in ledger.runs(directory):
        for record in ledger.read(path)["records"]:
            if record.get("kind") != "attempt":
                continue
            if record.get("circuit") != circuit_id:
                continue
            if record.get("loaded"):
                continue
            params, measured = record.get("params"), record.get("measured")
            if not params or not measured:
                continue
            if not all(key in params for key in tunable):
                continue
            if not all(item["key"] in measured for item in block["goals"]):
                continue
            key = tuple(round(float(params[name]), 12) for name in tunable)
            if key in seen:
                continue
            seen.add(key)
            library.append({"params": params, "measured": measured})
    return library


def pick(circuit_id, targets, library):
    """The stored design that best fits the new targets, at zero cost.

    Every library entry's stored measurement is re-scored against the new
    targets and the best worst-margin wins. The winner's margin is under
    the old deck and the old conditions, so it is a place to stand, not a
    result; the caller simulates before believing anything.
    """
    circuit, block = design.design_block(circuit_id)
    targets = design.resolve_targets(block, targets)
    best = None
    for entry in library:
        worst, _ = design.score_measurement(
            block["goals"], targets, entry["measured"])
        if best is None or worst > best["stored_margin"]:
            best = {"params": entry["params"], "stored_margin": worst}
    return best


def warm_start(circuit_id, targets, directory=None, library=None):
    """The full parameter set a warm search should start from, or None
    when the ledger holds no experience of this circuit."""
    if library is None:
        library = harvest(circuit_id, directory)
    if not library:
        return None
    found = pick(circuit_id, targets, library)
    start = circuits.defaults(circuit_id)
    start.update(found["params"])
    return {"params": start, "stored_margin": found["stored_margin"],
            "library_size": len(library)}


# ---------------------------------------------------------------------------
# the learning curve
# ---------------------------------------------------------------------------


def log_centre(circuit_id):
    """The geometric centre of the tunable box: the cold arm's start,
    owing nothing to any design (the experiment module's convention)."""
    circuit, block = design.design_block(circuit_id)
    specs = {item["key"]: item for item in circuit["params"]}
    centre = circuits.defaults(circuit_id)
    for name in block["tunable"]:
        low, high = specs[name]["min"], specs[name]["max"]
        centre[name] = float("%.10g" % (
            10.0 ** ((math.log10(low) + math.log10(high)) / 2.0)))
    return centre


def random_specs(circuit_id, count, rng, span=(0.7, 1.4)):
    """A sequence of specs scattered around the declared defaults.

    Each goal's default is scaled by a log-uniform factor inside span.
    Some will be easy and some infeasible; a learning curve needs both,
    because experience is only worth measuring on problems that vary.
    """
    circuit, block = design.design_block(circuit_id)
    lo, hi = math.log10(span[0]), math.log10(span[1])
    specs = []
    for _ in range(count):
        targets = {
            item["key"]: float("%.6g" % (
                item["default"] * 10.0 ** rng.uniform(lo, hi)))
            for item in block["goals"]
        }
        specs.append(targets)
    return specs


def _solve(circuit_id, start, targets, per_spec, book, arm,
           should_stop=None):
    """One counted solve. Returns what the curve needs and the attempts
    the library may later harvest from memory. arm is the ledger label
    that tells the two arms apart; the reader of the curve cannot, and
    must not have to, infer cold from warm by position."""
    attempts = []

    def keep(entry, best):
        if entry.get("measured"):
            attempts.append({"params": entry["params"],
                             "measured": entry["measured"]})

    watcher = runner.SimObserver(ledger=book, arm=arm, budget=per_spec + 1,
                                 phase="curve")
    try:
        with runner.observing(watcher):
            found = design.run_design(circuit_id, start, targets, per_spec,
                                      on_eval=keep, ledger=book, arm=arm,
                                      should_stop=should_stop)
        outcome = {"feasible": found["feasible"], "evals": found["evals"],
                   "reason": found["reason"]}
    except runner.SimBudgetExhausted:
        outcome = {"feasible": False, "evals": watcher.count,
                   "reason": "the simulation budget ran out"}
    outcome["sims"] = watcher.count
    return outcome, attempts


def learning_curve(circuit_id, specs, per_spec=40, book=None,
                   on_row=None, should_stop=None):
    """Solve each spec cold and warm; report both costs, counted.

    The warm arm's library holds every attempt made on earlier specs by
    either arm, and nothing from the current spec: experience means the
    past. A spec where the library is still empty runs the warm arm from
    the centre too, and says so, so the first row is a control rather
    than a gap.
    """
    circuit, block = design.design_block(circuit_id)
    centre = log_centre(circuit_id)
    library = []
    rows = []

    if book is not None:
        book.record("start", by="tool", what="learning_curve",
                    circuit=circuit_id, specs=len(specs), per_spec=per_spec)

    for index, requested in enumerate(specs):
        if should_stop is not None and should_stop():
            break
        targets = design.resolve_targets(block, requested)

        cold, cold_attempts = _solve(
            circuit_id, dict(centre), targets, per_spec, book, "cold",
            should_stop=should_stop)

        warm_from = "library"
        found = pick(circuit_id, targets, library) if library else None
        if found is None:
            begin, warm_from = dict(centre), "centre (library empty)"
        else:
            begin = circuits.defaults(circuit_id)
            begin.update(found["params"])
        warm, warm_attempts = _solve(
            circuit_id, begin, targets, per_spec, book, "warm",
            should_stop=should_stop)
        warm["start"] = warm_from

        # Only after both arms ran does this spec's experience join the
        # library: the warm arm at spec N must owe nothing to spec N.
        library.extend(cold_attempts)
        library.extend(warm_attempts)

        row = {"index": index, "targets": targets, "cold": cold,
               "warm": warm, "library_size_before": len(library)
               - len(cold_attempts) - len(warm_attempts)}
        rows.append(row)
        if book is not None:
            book.record("result", by="tool", what="curve_row", index=index,
                        cold_sims=cold["sims"], warm_sims=warm["sims"],
                        cold_feasible=cold["feasible"],
                        warm_feasible=warm["feasible"], warm_start=warm_from)
        if on_row is not None:
            on_row(row)

    both = [row for row in rows
            if row["cold"]["feasible"] and row["warm"]["feasible"]]
    summary = {
        "specs": len(rows),
        "solved_by_both": len(both),
        "cold_sims_total": sum(row["cold"]["sims"] for row in rows),
        "warm_sims_total": sum(row["warm"]["sims"] for row in rows),
        "cold_sims_on_solved": sum(row["cold"]["sims"] for row in both),
        "warm_sims_on_solved": sum(row["warm"]["sims"] for row in both),
    }
    if book is not None:
        book.record("end", by="tool", what="learning_curve", **summary)
    return {"circuit": circuit_id, "per_spec": per_spec, "rows": rows,
            "summary": summary}
