"""The four-way comparison, run so that its answer would survive review.

The research plan asks one question of the first year: does a language
model, a numerical optimizer, a person, or the two machines together size
an amplifier better. Answering it is not a matter of running four things
and printing a table. It is a matter of making the four comparable, and
almost everything that makes them comparable is a decision that can be got
wrong quietly.

The decisions this module makes, and why:

*The spec has to be one nobody has already solved.* At the registry
defaults every circuit passes its own default targets, so the optimizer
returns on its first evaluation and all four arms produce the same row.
AMP-1 below is infeasible at the reference sizing by a third of a
bandwidth, and the harness refuses to run until it has proved that again.

*The cost axis has to be the same one.* Simulations are counted at the
ngspice subprocess, not at the caller, because a session that calls the
corner suite spends eleven runs and used to be counted as none. Every arm
gets the same budget, enforced at the same place.

*No arm may start from another arm's answer.* The registry defaults are
byte-identical to the hand sizing, so the numerical arm starts cold, at the
geometric centre of its declared box. What the hand sizing and the hand
heuristic are worth is measured separately, as ablations, rather than
folded into the arm that is supposed to be free of them.

*The search space has to be the same one.* The load, the channel length and
the untuned widths are part of the problem, not of the answer; they are
frozen for every arm and an attempt to move them is refused and recorded.

*An arm is scored on the design it declares*, re-simulated afterwards by
the harness. Scoring the best point a session happened to touch gives the
model an oracle the optimizer does not get.

What this module does not do is decide who won. It measures, records, and
refuses to report a ranking it does not have the arms for.
"""

import hashlib
import json
import math
import random
import time

from . import circuits, design, ledger, runner

#: The spec the comparison is run at. Every number here was chosen against
#: a measurement of the reference sizing rather than picked to look hard:
#: bandwidth is 1.47x what the hand design achieves and power is 4 percent
#: under what it draws, so the search has to find transconductance per amp
#: rather than buy bandwidth with current. Gain sits just under the
#: reference so the search cannot pay for bandwidth by throwing gain away,
#: and phase margin binds from the other side at the cold start.
AMP1 = {
    "id": "AMP-1",
    "circuit": "opamp_two_stage",
    "targets": {
        "loop_gain_db": 70.0,
        "f_crossover": 20.0e6,
        "phase_margin": 60.0,
        "power": 150.0e-6,
    },
    # The problem statement, not the answer: the load it drives, the
    # channel length, and the two widths the comparison does not tune.
    "fixed": {"cl": 2e-12, "l": 5e-7, "wload": 1e-5, "w7": 1e-5},
    "conditions": "CL 2 pF, VDD 1.8 V, VCM 0.9 V, 27 C, tt corner",
    #: Simulations, per arm, counted at the subprocess. The reference costs
    #: one and the optimizer converges well inside this from a cold start.
    "budget": 60,
}

#: How many independent cold starts the numerical arm runs. Coordinate
#: descent is deterministic, so one run of one start is reproducible; what
#: is not reproducible is which start you happened to choose, and that is
#: what the restarts measure.
RESTARTS = 5

#: An arm ends in exactly one of these. Anything that is not "completed"
#: may not be reported as a result.
STATUSES = ("completed", "budget_exhausted", "aborted", "not_run", "error")


class ExperimentError(RuntimeError):
    """Raised when the experiment cannot be run or reported as intended."""


class PartialComparisonError(ExperimentError):
    """Raised on any attempt to rank four arms when fewer than four ran."""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# the search space, and what is not in it
# ---------------------------------------------------------------------------


def bounds_of(spec):
    circuit = circuits.get_circuit(spec["circuit"])
    specs = {item["key"]: item for item in circuit["params"]}
    block = circuit["design"]
    return {name: (specs[name]["min"], specs[name]["max"])
            for name in block["tunable"]}


def log_centre(spec):
    """The geometric centre of every tunable's declared box.

    A start that owes nothing to anyone's design. The alternative was the
    registry defaults, which are the hand sizing, which is another arm's
    answer.
    """
    return {
        name: float("%.10g" % (10.0 ** ((math.log10(low) + math.log10(high))
                                        / 2.0)))
        for name, (low, high) in bounds_of(spec).items()
    }


def random_start(spec, rng):
    """A log-uniform point inside the declared box."""
    return {
        name: float("%.10g" % (10.0 ** rng.uniform(math.log10(low),
                                                   math.log10(high))))
        for name, (low, high) in bounds_of(spec).items()
    }


def apply_fixed(spec, params):
    """The complete parameter set, with the frozen conditions restored.

    Returns (params, overridden). Anything an arm tried to change that it
    was not allowed to change is named, so a refusal is recorded rather
    than silently applied.
    """
    complete = dict(circuits.defaults(spec["circuit"]))
    complete.update(params or {})
    overridden = []
    for key, value in spec["fixed"].items():
        if key in (params or {}) and float(params[key]) != float(value):
            overridden.append(key)
        complete[key] = value
    return complete, overridden


def measure(spec, params):
    """One measurement of a complete design, at the frozen conditions."""
    complete, _ = apply_fixed(spec, params)
    return circuits.simulate(spec["circuit"], complete)


def score(spec, measured):
    block = circuits.get_circuit(spec["circuit"])["design"]
    return design.score_measurement(block["goals"], spec["targets"], measured)


# ---------------------------------------------------------------------------
# the pre-flight: a spec nobody has already solved
# ---------------------------------------------------------------------------


def preflight(spec, book=None):
    """Prove the spec is not already met by the reference sizing.

    Published with the results. A comparison run at a spec the hand design
    already passes is four identical rows, and the reader has no way to
    tell that from four methods agreeing.
    """
    reference = dict(circuits.defaults(spec["circuit"]))
    measured = measure(spec, reference)
    value, margins = score(spec, measured)
    binding = min(margins, key=lambda key: margins[key])

    found = {
        "spec": spec["id"],
        "reference_params": reference,
        "measured": measured,
        "margins": margins,
        "worst_margin": margins[binding],
        "binding_goal": binding,
        "vacuous": value >= 0.0,
    }
    if book is not None:
        book.record("note", by="tool", what="preflight", **{
            key: found[key] for key in
            ("spec", "margins", "worst_margin", "binding_goal", "vacuous")
        })
    if found["vacuous"]:
        raise ExperimentError(
            "The reference sizing already meets " + spec["id"] + " with worst "
            "margin " + ("%+.4f" % found["worst_margin"]) + ". Every arm "
            "would return on its first simulation and the table would be "
            "four identical rows. Tighten the spec before running anything."
        )
    return found


# ---------------------------------------------------------------------------
# the arms
# ---------------------------------------------------------------------------


def _verify(spec, params, book, arm):
    """Re-simulate a declared design, and score that.

    Not the best point the arm touched: the design it says it produced.
    An arm that measured something good and declared something else is
    scored on what it declared, the same as a person would be.
    """
    complete, _ = apply_fixed(spec, params)
    watcher = runner.SimObserver(ledger=book, arm=arm, phase="verify")
    with runner.observing(watcher):
        measured = circuits.simulate(spec["circuit"], complete)
    value, margins = score(spec, measured)
    return {
        "declared_params": complete,
        "declared_measured": measured,
        "margins": margins,
        "binding_goal": min(margins, key=lambda key: margins[key]),
        "feasible_nominal": value >= 0.0,
        "verify_sims": watcher.count,
    }


def _result(arm, status, **fields):
    if status not in STATUSES:
        raise ExperimentError("Unknown arm status " + repr(status))
    found = {"arm": arm, "status": status}
    found.update(fields)
    return found


def arm_reference(spec, book=None):
    """The hand sizing, measured once.

    It did not see the spec and it does not search. It is the baseline
    every other arm is measured against, and it is not called a human
    design: a person sizing to this spec, with their attempts recorded,
    would be a different arm and has not been run.
    """
    params = dict(circuits.defaults(spec["circuit"]))
    if book is not None:
        book.record("start", by="human", arm="reference", **{
            "start_params": params, "from": "reference",
            "searched": False, "toolset": [],
        })
    watcher = runner.SimObserver(ledger=book, arm="reference",
                                 budget=spec["budget"])
    started = time.time()
    with runner.observing(watcher):
        pass
    verified = _verify(spec, params, book, "reference")

    found = _result(
        "reference", "completed", searched=False, iterations=1,
        sims_total=verified["verify_sims"], sims_to_feasible=None,
        wall_s=round(time.time() - started, 3), **verified)
    if book is not None:
        book.record("result", by="human", **found)
    return found


def arm_optimizer(spec, book=None, start=None, label="optimizer",
                  origin="log_centre", rng_seed=None):
    """The numerical search, from a start that owes nothing to any design."""
    start = start or log_centre(spec)
    complete, _ = apply_fixed(spec, start)
    if book is not None:
        book.record("start", by="optimizer", arm=label, **{
            "start_params": complete, "from": origin, "rng_seed": rng_seed,
            "tunable": sorted(bounds_of(spec)),
            "bounds": {k: list(v) for k, v in bounds_of(spec).items()},
            "toolset": [],
        })

    watcher = runner.SimObserver(ledger=book, arm=label,
                                 budget=spec["budget"], phase="search")
    started = time.time()
    status, outcome = "completed", None
    try:
        with runner.observing(watcher):
            outcome = design.run_design(
                spec["circuit"], complete, spec["targets"],
                spec["budget"], ledger=book, arm=label,
            )
    except runner.SimBudgetExhausted:
        status = "budget_exhausted"
    except design.CANDIDATE_ERRORS as exc:
        status = "error"
        outcome = {"reason": str(exc).splitlines()[0]}

    declared = None
    if outcome and outcome.get("best") and outcome["best"].get("params"):
        declared = outcome["best"]["params"]
    if declared is None:
        found = _result(label, "aborted" if status == "completed" else status,
                        sims_total=watcher.count,
                        reason=(outcome or {}).get(
                            "reason", "no measurable point was found"),
                        wall_s=round(time.time() - started, 3))
        if book is not None:
            book.record("result", by="optimizer", **found)
        return found

    verified = _verify(spec, declared, book, label)

    # An arm that ran out of evaluations without meeting the spec did not
    # complete: it was cut off. Reporting that as a completed result invites
    # the reading that the method converged on something and stopped.
    reason = (outcome or {}).get("reason") or ""
    if status == "completed" and not verified["feasible_nominal"]             and "budget" in reason.lower():
        status = "budget_exhausted"

    found = _result(
        label, status, searched=True,
        iterations=(outcome or {}).get("evals"),
        reason=(outcome or {}).get("reason"),
        origin=origin, rng_seed=rng_seed,
        sims_total=watcher.count + verified["verify_sims"],
        sims_search=watcher.count,
        wall_s=round(time.time() - started, 3), **verified)
    if book is not None:
        book.record("result", by="optimizer", **found)
    return found


def arm_llm(spec, provider, toolset, label, book=None, message=None):
    """A model driving the tools, scored on the design it submits.

    toolset is the exact tool list the model is given: that, and nothing
    else, is what separates this arm from the one beside it. The prompt is
    generated from the tool list so it never instructs the model to use a
    tool it does not have.
    """
    from . import llm, strategist

    if book is not None:
        book.record("start", by="llm", arm=label, **{
            "start_params": None, "from": "none",
            "toolset": [tool["name"] for tool in toolset],
            "llm": {"provider": provider},
            "tools_sha256": digest([t["name"] for t in toolset]),
            "user_sha256": digest(message or ""),
        })

    try:
        client = llm.get_client(provider)
    except Exception as exc:                               # noqa: BLE001
        found = _result(label, "not_run", provider=provider,
                        why=str(exc).splitlines()[0])
        if book is not None:
            book.record("result", by="llm", **found)
        return found

    submitted = {}

    def run_tool(name, arguments, on_progress=None):
        if name == "submit_design":
            submitted["params"] = arguments.get("params") or {}
            submitted["note"] = arguments.get("note")
            return ({"accepted": True}, {"submitted": submitted["params"]})

        # The frozen conditions are the problem, not the answer. An attempt
        # to move one is refused in words the model can act on, and the
        # refusal is recorded rather than quietly applied.
        if "params" in (arguments or {}):
            complete, overridden = apply_fixed(spec, arguments["params"])
            if overridden and book is not None:
                book.record("decision", by="llm", arm=label, accepted=False,
                            reason="fixed_param", keys=overridden)
            arguments = dict(arguments, params=complete)
        return strategist.run_tool(name, arguments, on_progress)

    watcher = runner.SimObserver(ledger=book, arm=label,
                                 budget=spec["budget"], phase="search")
    started = time.time()
    status = "completed"
    turns = []
    try:
        with runner.observing(watcher):
            state = strategist.advise(
                client, [{"role": "user", "text": message}],
                turns.append, run_tool_fn=run_tool, tools=toolset,
            )
    except runner.SimBudgetExhausted:
        status, state = "budget_exhausted", "stopped"
    except Exception as exc:                               # noqa: BLE001
        status, state = "error", str(exc).splitlines()[0]

    if "params" not in submitted:
        found = _result(label, "aborted" if status == "completed" else status,
                        provider=provider, state=state,
                        sims_total=watcher.count,
                        reason="the session ended without submitting a design",
                        wall_s=round(time.time() - started, 3))
        if book is not None:
            book.record("result", by="llm", **found)
        return found

    verified = _verify(spec, submitted["params"], book, label)
    found = _result(
        label, status, provider=provider, state=state, searched=True,
        sims_total=watcher.count + verified["verify_sims"],
        sims_search=watcher.count,
        note=submitted.get("note"),
        wall_s=round(time.time() - started, 3), **verified)
    if book is not None:
        book.record("result", by="llm", **found)
    return found


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def ranked(results):
    """The arms in order, refused unless all four actually ran.

    The plan asks for a comparison of four approaches. Three arms and a
    blank is not that comparison, and printing it in a table with one row
    marked pending invites exactly the reading it should not get.
    """
    completed = [row for row in results if row["status"] == "completed"]
    if len(completed) < len(results):
        missing = [row["arm"] for row in results if row["status"] != "completed"]
        raise PartialComparisonError(
            "Ranking four approaches needs four that ran. These did not: "
            + ", ".join(missing) + ". Report the arms that ran and say "
            "plainly which did not."
        )
    return sorted(completed,
                  key=lambda row: (not row["feasible_nominal"],
                                   row.get("sims_total", 10 ** 6)))


def table(results, preflight_found=None):
    """The results as text, saying what ran and what did not."""
    lines = []
    if preflight_found is not None:
        lines.append(
            "Spec %s, verified not already met: worst margin %+.4f on %s at "
            "the reference sizing." % (
                preflight_found["spec"], preflight_found["worst_margin"],
                preflight_found["binding_goal"]))
        lines.append("")

    header = ("%-22s %-17s %6s %10s %7s %9s %5s" %
              ("arm", "status", "gain", "UGBW", "PM", "power", "sims"))
    lines.append(header)
    lines.append("-" * len(header))

    for row in results:
        if row["status"] != "completed":
            lines.append("%-22s %-17s %s" % (
                row["arm"], row["status"], row.get("why") or row.get("reason")
                or ""))
            continue
        measured = row["declared_measured"]
        lines.append("%-22s %-17s %6.2f %10.4g %7.2f %8.1fu %5d%s" % (
            row["arm"],
            "met" if row["feasible_nominal"] else "missed",
            measured["loop_gain_db"], measured["f_crossover"],
            measured["phase_margin"], measured["power"] * 1e6,
            row.get("sims_total", 0),
            "" if row["feasible_nominal"]
            else "  binding: " + row["binding_goal"]))

    not_run = [row["arm"] for row in results if row["status"] == "not_run"]
    if not_run:
        lines.append("")
        lines.append("Did not run: " + ", ".join(not_run)
                     + ". No ranking of four approaches is possible from "
                     "this run, and none is offered.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# what the model is given, and what it is asked
# ---------------------------------------------------------------------------

#: The tool that ends a session. An arm is scored on the design it declares,
#: not on the best point it happened to measure, because scoring the best
#: point touched hands the model an oracle the optimizer does not get.
SEP = chr(10) + chr(10)

SUBMIT_TOOL = {
    "name": "submit_design",
    "description": "Submit the finished design and end the session. Give the "
                   "complete parameter set you want scored. This is the only "
                   "design that will be measured; the harness re-simulates it "
                   "itself, so submit the one you believe in rather than the "
                   "best number you happened to see.",
    "schema": {
        "type": "object",
        "properties": {
            "params": {"type": "object",
                       "additionalProperties": {"type": "number"}},
            "note": {"type": "string",
                     "description": "One sentence on why this design."},
        },
        "required": ["params"],
    },
}


def toolsets(spec):
    """The two LLM arms, as the exact tool lists that separate them.

    Arm 3 has the simulator and nothing else: it must propose complete
    parameter sets and measure them itself. Arm 4 has everything the
    shipped strategist has. That difference, and no other, is what the two
    arms measure.
    """
    from . import strategist

    catalogue = {tool["name"]: tool for tool in strategist.TOOLS}
    llm_only = [catalogue["list_circuits"], catalogue["simulate"], SUBMIT_TOOL]
    full = list(strategist.TOOLS) + [SUBMIT_TOOL]
    return {"llm": llm_only, "llm_optimizer": full}


def spec_message(spec, toolset):
    """The task, worded identically for both LLM arms.

    The tool-flow sentence is generated from the tool list, so a model is
    never told to use a tool it has not been given -- the shipped prompt
    names seed_design and run_design, and arm 3 has neither. What the arms
    differ in is their tools; letting them differ in their instructions too
    would confound the thing being measured.
    """
    targets = spec["targets"]
    names = {tool["name"] for tool in toolset}

    task = (
        "Design the SKY130 two-stage op-amp, circuit id "
        + spec["circuit"] + ", to meet all of these at " + spec["conditions"]
        + ": open-loop gain at least " + ("%g" % targets["loop_gain_db"])
        + " dB, unity-gain bandwidth at least "
        + ("%g" % (targets["f_crossover"] / 1e6)) + " MHz, phase margin at "
        "least " + ("%g" % targets["phase_margin"]) + " degrees, power at "
        "most " + ("%g" % (targets["power"] * 1e6)) + " microwatts."
    )

    fixed = ("These are fixed and cannot be changed: "
             + ", ".join(sorted(spec["fixed"])) + ". They are the problem, "
             "not the answer, and an attempt to change one is refused.")

    budget = ("You have " + str(spec["budget"]) + " simulations. Every "
              "ngspice run counts against it, including any inside a tool "
              "that runs several.")

    stop = ("Stop at the first design that meets every target and submit it "
            "with submit_design. You are not asked to maximise anything "
            "beyond meeting the spec.")

    available = "The tools you have are: " + ", ".join(sorted(names)) + "."
    return SEP.join([task, fixed, budget, available, stop])
