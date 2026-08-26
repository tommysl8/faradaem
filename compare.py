"""The four-way comparison from the research plan, run end to end.

Four ways to arrive at one op-amp meeting one spec, judged by the same
simulator on the same targets, counted on the same axis:

    reference        the sizing already in the registry, measured once. It
                     did not see the spec and it does not search. It is the
                     baseline, and it is not a human-design arm: a person
                     designing to this spec, with their attempts recorded,
                     has not been run.
    optimizer        the numerical search, from the geometric centre of the
                     declared box -- cold, owing nothing to any design.
    llm              a model with the simulator and nothing else: it must
                     propose complete parameter sets and measure each.
    llm+optimizer    the same model with the full shipped toolset.

and two ablations, which are the honest measurement of what the hand design
and the hand-written seed heuristic are worth:

    optimizer_from_reference   the same search, started at the hand sizing
    optimizer_from_seed        the same search, started at the seed rule

Every simulation is counted where it happens, at the ngspice subprocess, so
a session that calls the corner suite is charged the eleven runs it spends.
Every arm gets the same budget. Every arm is scored on the design it
declares, re-simulated afterwards by this harness rather than on the best
point it happened to touch. Every attempt is written to the ledger.

    .venv\\Scripts\\python.exe compare.py
    .venv\\Scripts\\python.exe compare.py --repeats 3 --provider anthropic
    .venv\\Scripts\\python.exe compare.py --skip-llm
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time

from spice import circuits, design, experiment, ledger, llm


def run_all(spec, repeats, provider, skip_llm, restarts, book):
    """Every arm, in an order that puts the cheap certainties first."""
    results = []

    found = experiment.preflight(spec, book)
    print(experiment.table([], found))

    print("Running: reference ...", flush=True)
    results.append(experiment.arm_reference(spec, book))

    print("Running: optimizer (cold start) ...", flush=True)
    results.append(experiment.arm_optimizer(spec, book))

    sets = experiment.toolsets(spec)
    for name in ("llm", "llm_optimizer"):
        for index in range(repeats):
            label = name if repeats == 1 else "%s#%d" % (name, index + 1)
            if skip_llm:
                results.append(experiment._result(
                    label, "not_run", why="--skip-llm was given"))
                continue
            print("Running: %s (%s) ..." % (label, provider), flush=True)
            results.append(experiment.arm_llm(
                spec, provider, sets[name], label, book,
                message=experiment.spec_message(spec, sets[name])))

    # The ablations, below the four: what the priors are worth.
    print("Running: ablation, optimizer from the reference sizing ...",
          flush=True)
    results.append(experiment.arm_optimizer(
        spec, book, start=dict(circuits.defaults(spec["circuit"])),
        label="optimizer_from_reference", origin="reference"))

    try:
        seeded, _ = design.seed_params(
            spec["circuit"], spec["targets"],
            dict(circuits.defaults(spec["circuit"])))
        print("Running: ablation, optimizer from the seed heuristic ...",
              flush=True)
        results.append(experiment.arm_optimizer(
            spec, book, start=seeded, label="optimizer_from_seed",
            origin="seed_heuristic"))
    except design.DesignError as exc:
        results.append(experiment._result(
            "optimizer_from_seed", "not_run", why=str(exc).splitlines()[0]))

    rng = random.Random(20260826)
    for index in range(restarts):
        label = "optimizer_restart#%d" % (index + 1)
        print("Running: %s ..." % label, flush=True)
        results.append(experiment.arm_optimizer(
            spec, book, start=experiment.random_start(spec, rng),
            label=label, origin="random", rng_seed=20260826 + index))

    return results, found


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=3,
                        help="LLM sessions per arm; they are not deterministic")
    parser.add_argument("--restarts", type=int, default=0,
                        help="extra random cold starts for the numerical arm")
    parser.add_argument("--provider", default="anthropic")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--budget", type=int, default=None,
                        help="simulations per arm; the spec's default is 60")
    args = parser.parse_args()

    spec = dict(experiment.AMP1)
    if args.budget:
        spec["budget"] = args.budget

    book = ledger.Ledger()
    print("Ledger:", book.path)
    print()

    started = time.time()
    results, found = run_all(spec, args.repeats, args.provider,
                             args.skip_llm, args.restarts, book)

    print()
    print(experiment.table(results, found))
    print()
    print("Ran in %.0f s. Every number above came from ngspice."
          % (time.time() - started))

    book.record("end", by="tool",
                arms_planned=len(results),
                arms_completed=sum(1 for row in results
                                   if row["status"] == "completed"))

    out = os.path.join(os.path.dirname(book.path), book.run_id + "-summary.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump({"spec": spec, "preflight": found, "results": results},
                  handle, indent=1, default=str)
    print("Summary:", out)

    # The plan asks for a comparison of four approaches. If fewer than four
    # ran, say so rather than printing three rows and a blank.
    try:
        experiment.ranked([row for row in results
                           if row["arm"] in ("reference", "optimizer",
                                             "llm", "llm_optimizer")])
    except experiment.PartialComparisonError as exc:
        print()
        print("No four-way ranking:", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
