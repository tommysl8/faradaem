"""The four-way comparison from the research plan, measured head to head.

Four ways to arrive at an op-amp meeting one spec, all judged by the same
simulator on the same targets:

    human            the hand-designed Balanced sizing, simulated as-is
    optimizer        seed from the targets, iterate only if the seed is short
    llm              a model proposing parameter sets directly; its only tool
                     is the simulator, so every proposal is measured, but the
                     numeric search is on the model
    llm+optimizer    the full strategist, with seeding and the iterator

The LLM rows run once per provider that has a key, and are marked pending
when no key is set. Every number in the output came from ngspice.

Run it from the project root:

    .venv\\Scripts\\python.exe compare.py
    .venv\\Scripts\\python.exe compare.py --target power=1e-4 --budget 40
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time

from spice import circuits, design, llm, strategist

CIRCUIT = "opamp_two_stage"

LLM_ONLY_TOOLS = [
    tool for tool in strategist.TOOLS if tool["name"] in ("list_circuits", "simulate")
]


def spec_message(targets):
    return (
        "Design the SKY130 two-stage op-amp (circuit id opamp_two_stage) to "
        "meet all of these, then stop: open-loop gain at least "
        + "%g" % targets["loop_gain_db"] + " dB, unity-gain bandwidth at least "
        + "%g" % targets["f_crossover"] + " Hz, phase margin at least "
        + "%g" % targets["phase_margin"] + " degrees, power at most "
        + "%g" % targets["power"] + " W."
    )


def score_of(measured, goals, targets):
    try:
        score, _ = design.score_measurement(goals, targets, measured)
        return score
    except (KeyError, TypeError):
        return None


def row_human(goals, targets):
    balanced = [p for p in circuits.get_circuit(CIRCUIT)["presets"]
                if p["label"] == "Balanced"][0]["params"]
    started = time.time()
    measured = circuits.simulate(CIRCUIT, dict(balanced))
    return {
        "measured": {g["key"]: measured[g["key"]] for g in goals},
        "score": score_of(measured, goals, targets),
        "sims": 1,
        "seconds": time.time() - started,
    }


def row_optimizer(goals, targets, budget):
    started = time.time()
    seeded, resolved = design.seed_params(
        CIRCUIT, targets, circuits.defaults(CIRCUIT)
    )
    measured = circuits.simulate(CIRCUIT, seeded)
    sims = 1
    score = score_of(measured, goals, resolved)
    best = {g["key"]: measured[g["key"]] for g in goals}

    if score is None or score < 0:
        result = design.run_design(CIRCUIT, seeded, targets, budget)
        sims += result["evals"]
        if result["best"] is not None:
            best = result["best"]["measured"]
            score = result["best"]["score"]

    return {"measured": best, "score": score, "sims": sims,
            "seconds": time.time() - started}


def row_llm(provider, goals, targets, tools):
    """One strategist session; the best measured op-amp point wins."""
    client = llm.get_client(provider)
    started = time.time()
    best = None
    best_score = None
    sims = 0

    def on_event(event):
        nonlocal best, best_score, sims
        if event["kind"] != "tool" or not event["ok"]:
            return
        display = event["display"]
        candidates = []
        if display.get("circuit") == CIRCUIT and display.get("measured"):
            candidates.append(display["measured"])
            sims += 1
        if display.get("circuit") == CIRCUIT and display.get("best"):
            candidates.append(display["best"]["measured"])
            sims += display.get("evals") or 0
        for measured in candidates:
            score = score_of(measured, goals, targets)
            if score is not None and (best_score is None or score > best_score):
                best, best_score = measured, score

    state = strategist.advise(
        client,
        [{"role": "user", "text": spec_message(targets)}],
        on_event,
        tools=tools,
    )
    return {
        "measured": best, "score": best_score, "sims": sims,
        "seconds": time.time() - started, "state": state,
    }


def fmt_row(name, row):
    if row is None:
        return "%-28s %s" % (name, "pending: no API key set")
    if row.get("measured") is None:
        return "%-28s %s" % (name, "no measured op-amp point (state: "
                             + row.get("state", "?") + ")")
    m = row["measured"]
    return ("%-28s gain %6.2f dB  UGBW %9.4g Hz  PM %6.2f deg  "
            "power %7.2f uW  score %+.3f  sims %3d  %5.0f s" % (
                name, m["loop_gain_db"], m["f_crossover"], m["phase_margin"],
                m["power"] * 1e6, row["score"], row["sims"], row["seconds"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", action="append", default=[],
                        metavar="key=value",
                        help="override one target, e.g. --target power=1e-4")
    parser.add_argument("--budget", type=int, default=40,
                        help="iterator budget for the optimizer rows")
    parser.add_argument("--skip-llm", action="store_true",
                        help="run only the offline rows")
    args = parser.parse_args()

    block = circuits.get_circuit(CIRCUIT)["design"]
    goals = block["goals"]
    overrides = {}
    for item in args.target:
        key, _, value = item.partition("=")
        overrides[key] = value
    targets = design.resolve_targets(block, overrides)

    print("Spec:", spec_message(targets))
    print()

    results = {}
    results["human"] = row_human(goals, targets)
    print(fmt_row("human (Balanced)", results["human"]))
    results["optimizer"] = row_optimizer(goals, targets, args.budget)
    print(fmt_row("optimizer", results["optimizer"]))

    providers = [] if args.skip_llm else [
        item["name"] for item in llm.available_providers()
    ]
    for provider in ("anthropic", "openai"):
        for mode, tools in (("llm", LLM_ONLY_TOOLS), ("llm+optimizer", None)):
            name = mode + " (" + provider + ")"
            if provider not in providers:
                results[name] = None
                print(fmt_row(name, None))
                continue
            try:
                results[name] = row_llm(provider, goals, targets, tools)
            except llm.LlmError as exc:
                results[name] = {"error": str(exc)}
                print("%-28s error: %s" % (name, exc))
                continue
            print(fmt_row(name, results[name]))

    # Dash, not underscore: faradaem_* names are reserved for throwaway
    # simulation files, and the temp-hygiene test treats any leftover as a leak.
    out_path = os.path.join(tempfile.gettempdir(), "faradaem-comparison.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"targets": targets, "results": results}, handle, indent=1)
    print()
    print("Full results written to", out_path)


if __name__ == "__main__":
    main()
