"""Run the simulator backwards: which condition explains these numbers?

When measurements disagree with prediction, the question is always "what
happened to this circuit?", and the usual answer is an experienced guess.
This module makes the question a search problem. Given a sizing and a set
of observed metrics, it walks a declared hypothesis space, process corner,
supply, temperature, and optionally a mismatch draw, re-simulating the
design under each candidate condition until one reproduces the observation
or the space is exhausted.

The hypothesis space is closed and printed with every answer, because an
inverse problem is only honest about what it searched. A verdict of
"explained" means a condition in the space reproduces every watched metric
within the declared tolerance; "unexplained" means the search finished and
no condition fits, and the nearest one is reported with its residual so
the reader can see how far the story falls short. An unexplained result is
a finding: it says the cause lies outside process, supply, temperature and
mismatch, or outside the grids searched, and says so with numbers. A
search cut short, by its budget or by the caller, may not say that: it
returns "inconclusive", because "nothing in the space explains it" is a
claim about the space, and a truncated search never saw the space.

There is no silicon yet, so the module carries its own validation:
blind_trial() draws a condition in secret, simulates the "measurement",
and requires explain() to recover the condition from the numbers alone.
The same machinery that will read a chip is proved on problems whose
answer is known.

Simulations are counted at the ngspice subprocess by runner.SimObserver,
so the cost of an explanation is reported as observed, never estimated.
"""

from __future__ import annotations

import math

from . import circuits, design, pvt, runner

#: The hypothesis grids. Coarse on purpose: the first question is which
#: story explains the numbers, not its third significant figure. The
#: refinement stage narrows supply and temperature after the grid picks
#: the neighbourhood.
CORNERS = ("tt", "ss", "ff", "sf", "fs")
VDD_GRID = (1.62, 1.71, 1.8, 1.89, 1.98)
TEMP_GRID = (-40.0, 0.0, 27.0, 85.0, 125.0)

#: A candidate explains the observation when the rms relative error across
#: the watched metrics is inside this. Re-simulating the true condition
#: reproduces it to solver noise, orders of magnitude below; the tolerance
#: is loose enough to forgive rounding in a transcribed measurement and
#: tight enough that adjacent corners cannot impersonate each other.
EXPLAINED_RMS = 0.005

#: A verdict is exactly one of these.
VERDICTS = ("explained", "unexplained", "inconclusive")


class ForensicsError(ValueError):
    """An explanation request that cannot run. Maps to HTTP 400."""


def watched_keys(circuit):
    """The metrics an explanation must reproduce: the design goals when a
    design block exists, the readout keys otherwise. The same set the PVT
    suite watches, for the same reason: these are the published numbers."""
    block = circuit.get("design")
    if block:
        return [item["key"] for item in block["goals"]]
    keys = [circuit["readout"]["headline"]["key"]]
    keys.extend(stat["key"] for stat in circuit["readout"]["stats"])
    return keys


def residual(observed, simulated, keys):
    """RMS relative error between an observation and one simulation.

    Relative, because the watched metrics live on wildly different scales;
    floored, so a metric observed at zero cannot divide the answer away.
    """
    total = 0.0
    for key in keys:
        scale = max(abs(float(observed[key])), 1e-12)
        total += ((float(simulated[key]) - float(observed[key])) / scale) ** 2
    return math.sqrt(total / len(keys))


def _condition(corner, vdd, temp, seed=None):
    return {"corner": corner, "vdd": round(float(vdd), 4),
            "temp": round(float(temp), 2), "seed": seed}


def describe(condition):
    text = condition["corner"] + ", " + ("%.2f" % condition["vdd"]) + " V, " \
        + ("%g" % condition["temp"]) + " C"
    if condition.get("seed"):
        text += ", mismatch seed " + str(condition["seed"])
    return text


def explain(circuit_id, params, observed, budget=30, seeds=0,
            on_each=None, should_stop=None, book=None):
    """Search the hypothesis space for the condition behind an observation.

    observed maps every watched metric of the circuit to its measured
    value. seeds > 0 adds mismatch draws 1..seeds to the space, each tried
    at the typical corner (the only corner the PDK carries mismatch for).
    Returns the verdict, the best condition, its residual and per-metric
    deltas, the full trace, and the simulations spent.
    """
    pvt.require_supported(circuit_id)
    circuit = circuits.get_circuit(circuit_id)
    keys = watched_keys(circuit)
    missing = [key for key in keys if key not in observed]
    if missing:
        raise ForensicsError(
            "The observation is missing " + ", ".join(sorted(missing))
            + ". Every watched metric is needed: a story that only has to "
            "explain some of the numbers is too easy to tell."
        )

    state = {"rows": [], "best": None, "best_tt": None, "truncated": False}
    seen = {}

    if book is not None:
        book.record("start", by="tool", what="forensics", circuit=circuit_id,
                    observed={key: observed[key] for key in keys},
                    budget=budget, seeds=seeds)

    def run(condition):
        key = (condition["corner"], condition["vdd"], condition["temp"],
               condition["seed"])
        if key in seen:
            return seen[key]
        if should_stop is not None and should_stop():
            state["truncated"] = True
            return None
        corner = condition["corner"]
        lib = pvt.MC_SECTION if condition["seed"] else corner
        transform = pvt.make_transform(lib, condition["vdd"],
                                       condition["temp"], condition["seed"])
        row = dict(condition)
        try:
            simulated = circuits.simulate(circuit_id, dict(params), transform)
        except runner.SimBudgetExhausted:
            raise
        except (circuits.CircuitInputError, runner.NgspiceParseError,
                runner.NgspiceRunError) as exc:
            row["residual"] = None
            row["error"] = str(exc).splitlines()[0]
        else:
            row["residual"] = residual(observed, simulated, keys)
            row["simulated"] = {key: simulated[key] for key in keys}
            row["error"] = None
            if state["best"] is None or row["residual"] < state["best"]["residual"]:
                state["best"] = row
            if (row["corner"] == "tt" and row["seed"] is None
                    and (state["best_tt"] is None
                         or row["residual"] < state["best_tt"]["residual"])):
                state["best_tt"] = row
        seen[key] = row
        state["rows"].append(row)
        if on_each is not None:
            on_each(row)
        return row

    def settled():
        """The story already fits to solver noise; searching further would
        spend simulations distinguishing between explanations that agree."""
        best = state["best"]
        return (best is not None and best["residual"] is not None
                and best["residual"] <= EXPLAINED_RMS / 10.0)

    watcher = runner.SimObserver(ledger=book, budget=budget, phase="forensics")
    try:
        with runner.observing(watcher):
            # Stage 1: the corner axis at nominal conditions. Process is the
            # coarsest lever, so it is identified first.
            for corner in CORNERS:
                run(_condition(corner, pvt.PVT_CONDITIONS[0]["vdd"],
                               pvt.PVT_CONDITIONS[0]["temp"]))
                if settled():
                    break

            # Stage 2: coordinate descent over the grids from the best
            # corner: try every supply at the best-so-far, then every
            # temperature, twice, so one axis can correct the other.
            for _ in range(2):
                if state["best"] is None or settled():
                    break
                here = state["best"]
                for vdd in VDD_GRID:
                    run(_condition(here["corner"], vdd, here["temp"]))
                here = state["best"]
                for temp in TEMP_GRID:
                    run(_condition(here["corner"], here["vdd"], temp))

            # Stage 2b: every corner again, in the environment stage 2
            # found. The greedy walk picked its corner at nominal
            # conditions, and a corner can impersonate another until the
            # supply and temperature stop flattering it.
            if state["best"] is not None and not settled():
                here = state["best"]
                for corner in CORNERS:
                    run(_condition(corner, here["vdd"], here["temp"]))

            # Stage 3: mismatch draws. A skewed die only exists at the
            # typical corner, and a mismatched typical die is easily read
            # as a mildly skewed corner, or partly absorbed into a wrong
            # temperature, which are exactly the traps a human forensics
            # guess falls into. So the die is treated as one more
            # coordinate of a joint search: the environment is re-walked
            # under tt, every draw is tried both there and at nominal
            # conditions (where a bench measurement usually happened),
            # and the environment is walked once more with the best draw
            # held. Revisits are memoised, so retreading costs nothing.
            if seeds and state["best"] is not None and not settled():
                here = state["best_tt"] or state["best"]
                for vdd in VDD_GRID:
                    run(_condition("tt", vdd, here["temp"]))
                if state["best_tt"] is not None:
                    here = state["best_tt"]
                    for temp in TEMP_GRID:
                        run(_condition("tt", here["vdd"], temp))
                best_seeded = None
                if state["best_tt"] is not None:
                    here = state["best_tt"]
                    for vdd, temp in ((here["vdd"], here["temp"]),
                                      (1.8, 27.0)):
                        for index in range(seeds):
                            row = run(_condition("tt", vdd, temp,
                                                 seed=index + 1))
                            if (row is not None
                                    and row.get("residual") is not None
                                    and (best_seeded is None
                                         or row["residual"]
                                         < best_seeded["residual"])):
                                best_seeded = row
                if best_seeded is not None and not settled():
                    here = best_seeded
                    for vdd in VDD_GRID:
                        run(_condition("tt", vdd, here["temp"],
                                       seed=here["seed"]))
                    for temp in TEMP_GRID:
                        run(_condition("tt", here["vdd"], temp,
                                       seed=here["seed"]))
    except runner.SimBudgetExhausted:
        state["truncated"] = True

    best = state["best"]
    if (best is not None and best["residual"] is not None
            and best["residual"] <= EXPLAINED_RMS):
        verdict = "explained"
    elif state["truncated"]:
        verdict = "inconclusive"
    else:
        verdict = "unexplained"

    deltas = None
    if best is not None and best.get("simulated"):
        deltas = {
            key: {"observed": float(observed[key]),
                  "simulated": best["simulated"][key],
                  "relative": (best["simulated"][key] - float(observed[key]))
                  / max(abs(float(observed[key])), 1e-12)}
            for key in keys
        }

    found = {
        "circuit": circuit_id,
        "keys": keys,
        "verdict": verdict,
        "truncated": state["truncated"],
        "best": best,
        "deltas": deltas,
        "rows": state["rows"],
        "sims": watcher.count,
        "seconds": round(watcher.seconds, 3),
        "budget": budget,
        "space": {"corners": list(CORNERS), "vdd": list(VDD_GRID),
                  "temp": list(TEMP_GRID), "seeds": seeds},
    }
    if book is not None:
        book.record("result", by="tool", what="forensics", verdict=verdict,
                    sims=watcher.count,
                    residual=None if best is None else best["residual"])
    return found


def blind_trial(circuit_id, params, rng, budget=30, seeds=0, book=None):
    """Validation on a problem whose answer is known.

    A condition is drawn in secret, the "measurement" is simulated under
    it, and explain() gets only the numbers. Returns what was drawn, what
    was recovered, and whether they are the same condition. The drawing
    simulation is charged to the trial's own account, not the explainer's,
    because a real measurement costs the lab, not the analyst.
    """
    pvt.require_supported(circuit_id)
    circuit = circuits.get_circuit(circuit_id)
    keys = watched_keys(circuit)

    seed = rng.randint(1, seeds) if seeds and rng.random() < 0.5 else None
    # Mismatch statistics exist only at the typical process corner, so a
    # drawn die is a typical-process die; the same limit explain() lives by.
    corner = "tt" if seed else rng.choice(CORNERS)
    truth = _condition(corner, rng.choice(VDD_GRID), rng.choice(TEMP_GRID),
                       seed=seed)
    lib = pvt.MC_SECTION if truth["seed"] else truth["corner"]
    transform = pvt.make_transform(lib, truth["vdd"], truth["temp"],
                                   truth["seed"])
    drawing = runner.SimObserver(ledger=book, phase="forensics_draw")
    with runner.observing(drawing):
        measured = circuits.simulate(circuit_id, dict(params), transform)
    observed = {key: measured[key] for key in keys}

    found = explain(circuit_id, params, observed, budget=budget, seeds=seeds,
                    book=book)
    recovered = None
    if found["best"] is not None:
        recovered = {key: found["best"][key]
                     for key in ("corner", "vdd", "temp", "seed")}
    return {
        "truth": truth,
        "observed": observed,
        "recovered": recovered,
        "match": recovered == truth,
        "verdict": found["verdict"],
        "residual": None if found["best"] is None
        else found["best"]["residual"],
        "sims": found["sims"],
        "draw_sims": drawing.count,
        "explanation": found,
    }
