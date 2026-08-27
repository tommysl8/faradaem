"""The skeptic with a budget: signoff as a bounded, recorded attack.

"Passed the corner suite" is a claim about eleven conditions someone chose
in advance. This module makes the opposite claim available: an adversary is
given a simulation budget and told to break the design, searching process,
supply, temperature and mismatch for any condition where a target fails.
The strength of the resulting verdict is not a checkmark, it is the budget
the attack spent failing: "survived 40 adversarial simulations" says more
than "passed", and it says exactly how much more.

The search is greedy and staged, cheap certainties first: the five process
corners at nominal conditions, then the supply and temperature extremes on
the worst corner found, then a local walk of the two continuous knobs
toward worse margin, then mismatch seeds at the worst environment. Mismatch
runs at the typical process corner because the PDK carries no mismatch
statistics at the skewed corners (the same limit pvt.py documents).

Four verdicts, and only four. "broken" means a condition was measured
violating a target, and the condition is named. "survived" means the
attack ran itself out, of conditions to try or of budget to try them
with, without finding a violation; its claim always carries the count,
because "survived" is a statement about the search, never a proof.
"unmeasurable" means some condition inside the operating envelope would
not simulate at all; a circuit that cannot be measured at a condition it
claims to operate at has not earned "survived", so the failure to measure
is surfaced instead of skipped. "aborted" means the caller stopped the
attack before it finished; an interrupted attack has earned nothing, and
saying "survived" about it would be the checkmark this module exists to
replace.

Every simulation is counted at the ngspice subprocess by runner.SimObserver,
so the budget the verdict cites is the budget that was actually enforced.
"""

from __future__ import annotations

from . import circuits, design, pvt, runner

#: The operating envelope the adversary may search. The same limits the PVT
#: suite checks, held here so the attack cannot wander to conditions the
#: design never claimed.
VDD_MIN = 1.62
VDD_MAX = 1.98
TEMP_MIN = -40.0
TEMP_MAX = 125.0

#: Nominal conditions, where the corner stage runs.
VDD_NOM = 1.8
TEMP_NOM = 27.0

#: The five process corners, typical first so the baseline is measured
#: before anything skewed.
CORNERS = ("tt", "ss", "ff", "sf", "fs")

#: The refinement stage steps the continuous knobs by these amounts, halving
#: once when a full pass finds nothing worse. Two passes at most: the knobs
#: are bounded and the budget is better spent on mismatch than on chasing
#: fractions of a degree Celsius.
VDD_STEP = 0.09
TEMP_STEP = 40.0

#: How many mismatch seeds the last stage draws, budget permitting.
MISMATCH_SEEDS = 8

#: A verdict is exactly one of these.
VERDICTS = ("broken", "survived", "unmeasurable", "aborted")

#: The stages, in the order they run.
STAGES = ("corners", "environment", "refine", "mismatch")


class AdversaryError(ValueError):
    """An attack that cannot be run. Maps to HTTP 400."""


def _clamp(value, low, high):
    return min(max(value, low), high)


def _condition(stage, corner, vdd, temp, seed=None):
    return {"stage": stage, "corner": corner, "vdd": round(float(vdd), 4),
            "temp": round(float(temp), 2), "seed": seed}


def _key(condition):
    return (condition["corner"], condition["vdd"], condition["temp"],
            condition["seed"])


def attack(circuit_id, params, targets=None, budget=40,
           on_each=None, should_stop=None, book=None):
    """Spend a simulation budget trying to break one design.

    params is the complete sizing under attack. targets override the
    circuit's declared goal defaults; the goals themselves come from the
    design block, so the adversary attacks the same claims the datasheet
    makes. Returns the verdict, the full trace, and the worst condition
    seen, whatever the verdict.
    """
    pvt.require_supported(circuit_id)
    circuit, block = design.design_block(circuit_id)
    targets = design.resolve_targets(block, targets)
    if budget < len(CORNERS):
        raise AdversaryError(
            "An attack budget below " + str(len(CORNERS)) + " cannot even "
            "measure the process corners. Raise the budget."
        )

    state = {"rows": [], "worst": None, "broken": None, "unmeasurable": None,
             "stopped": False}
    seen = set()

    if book is not None:
        book.record("start", by="tool", what="adversary", circuit=circuit_id,
                    targets=targets, budget=budget)

    def run(condition):
        """Measure one condition. Returns its row, or None if already seen,
        stopped, or the budget refused the run."""
        if _key(condition) in seen:
            return None
        if should_stop is not None and should_stop():
            state["stopped"] = True
            return None
        seen.add(_key(condition))
        corner = condition["corner"]
        transform = pvt.make_transform(
            corner if corner != "tt" or condition["seed"] is None
            else pvt.MC_SECTION,
            condition["vdd"], condition["temp"], condition["seed"])
        row = dict(condition)
        try:
            measured = circuits.simulate(circuit_id, dict(params), transform)
        except runner.SimBudgetExhausted:
            raise
        except (circuits.CircuitInputError, runner.NgspiceParseError,
                runner.NgspiceRunError) as exc:
            row["measured"] = None
            row["margins"] = None
            row["worst_margin"] = None
            row["error"] = str(exc).splitlines()[0]
            if state["unmeasurable"] is None:
                state["unmeasurable"] = row
        else:
            worst, margins = design.score_measurement(
                block["goals"], targets, measured)
            row["measured"] = {item["key"]: measured[item["key"]]
                               for item in block["goals"]}
            row["margins"] = margins
            row["worst_margin"] = worst
            row["error"] = None
            if state["worst"] is None or worst < state["worst"]["worst_margin"]:
                state["worst"] = row
            if worst < 0.0 and state["broken"] is None:
                state["broken"] = row
        state["rows"].append(row)
        if book is not None:
            book.record("attempt", by="tool", what="adversary_condition",
                        condition={k: row[k] for k in
                                   ("stage", "corner", "vdd", "temp", "seed")},
                        measured=row["measured"], margins=row["margins"],
                        score=row["worst_margin"], error=row["error"])
        if on_each is not None:
            on_each(row)
        return row

    watcher = runner.SimObserver(ledger=book, arm=None, budget=budget,
                                 phase="adversary")
    try:
        with runner.observing(watcher):
            # Stage 1: the five process corners at nominal conditions.
            for corner in CORNERS:
                run(_condition("corners", corner, VDD_NOM, TEMP_NOM))
                if state["broken"]:
                    break

            # Stage 2: supply and temperature extremes, on the worst corner
            # found so far. The corner and the environment often gang up, so
            # the extremes are tried where the margin is already thinnest.
            if not state["broken"] and state["worst"] is not None:
                corner = state["worst"]["corner"]
                for vdd, temp in ((VDD_MIN, TEMP_MIN), (VDD_MIN, TEMP_MAX),
                                  (VDD_MAX, TEMP_MIN), (VDD_MAX, TEMP_MAX)):
                    run(_condition("environment", corner, vdd, temp))
                    if state["broken"]:
                        break

            # Stage 3: walk vdd and temperature locally from the worst
            # condition seen, keeping any step that makes the margin worse.
            if not state["broken"] and state["worst"] is not None:
                vdd_step, temp_step = VDD_STEP, TEMP_STEP
                for _ in range(2):
                    moved = False
                    here = state["worst"]
                    for dv, dt in ((vdd_step, 0.0), (-vdd_step, 0.0),
                                   (0.0, temp_step), (0.0, -temp_step)):
                        candidate = _condition(
                            "refine", here["corner"],
                            _clamp(here["vdd"] + dv, VDD_MIN, VDD_MAX),
                            _clamp(here["temp"] + dt, TEMP_MIN, TEMP_MAX))
                        row = run(candidate)
                        if state["broken"]:
                            break
                        if row is not None and state["worst"] is row:
                            moved = True
                    if state["broken"]:
                        break
                    if not moved:
                        vdd_step /= 2.0
                        temp_step /= 2.0

            # Stage 4: mismatch seeds. The PDK only carries mismatch at the
            # typical process corner, so the environment of the worst
            # condition is kept and the corner is not.
            if not state["broken"]:
                worst = state["worst"]
                vdd = worst["vdd"] if worst else VDD_NOM
                temp = worst["temp"] if worst else TEMP_NOM
                for index in range(MISMATCH_SEEDS):
                    run(_condition("mismatch", "tt", vdd, temp,
                                   seed=index + 1))
                    if state["broken"]:
                        break
    except runner.SimBudgetExhausted:
        pass

    if state["broken"] is not None:
        verdict = "broken"
    elif state["unmeasurable"] is not None:
        verdict = "unmeasurable"
    elif state["stopped"]:
        verdict = "aborted"
    else:
        verdict = "survived"

    found = {
        "circuit": circuit_id,
        "targets": targets,
        "budget": budget,
        "sims": watcher.count,
        "seconds": round(watcher.seconds, 3),
        "verdict": verdict,
        "rows": state["rows"],
        "worst": state["worst"],
        "breaking": state["broken"],
        "unmeasurable": state["unmeasurable"],
    }
    if book is not None:
        book.record("result", by="tool", what="adversary",
                    verdict=verdict, sims=watcher.count,
                    worst=(state["worst"] or {}).get("worst_margin"),
                    breaking=state["broken"] is not None)
    return found


def claim(found):
    """The one-line statement a datasheet may print for this attack.

    The wording is deliberate: "survived" always carries the number of
    simulations, because without it the claim is the checkmark this module
    exists to replace.
    """
    if found["verdict"] == "broken":
        row = found["breaking"]
        binding = min(row["margins"], key=lambda key: row["margins"][key])
        return ("Broken at " + describe(row) + ": " + binding + " misses by "
                + ("%.1f" % (abs(row["margins"][binding]) * 100.0)) + "%.")
    if found["verdict"] == "unmeasurable":
        return ("Not certified: the circuit would not measure at "
                + describe(found["unmeasurable"]) + ".")
    if found["verdict"] == "aborted":
        return ("Aborted after " + str(found["sims"]) + " simulations; an "
                "interrupted attack certifies nothing.")
    worst = found["worst"]
    text = ("Survived " + str(found["sims"]) + " adversarial simulations")
    if worst is not None:
        binding = min(worst["margins"], key=lambda key: worst["margins"][key])
        text += ("; thinnest margin " + ("%+.1f" % (worst["worst_margin"] * 100.0))
                 + "% on " + binding + " at " + describe(worst))
    return text + "."


def describe(row):
    text = row["corner"] + ", " + ("%.2f" % row["vdd"]) + " V, " \
        + ("%g" % row["temp"]) + " C"
    if row.get("seed") is not None:
        text += ", mismatch seed " + str(row["seed"])
    return text
