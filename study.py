"""The studies: folklore, put to measurement.

Each subcommand runs one of the standing studies end to end, writes every
simulation to the ledger, and prints what was found and what it cost. The
copy is deliberate about claims: "survived" always carries a count,
"not found" never says impossible, and an explanation names the space it
searched.

    .venv\\Scripts\\python.exe study.py adversary ota_5t --budget 40
    .venv\\Scripts\\python.exe study.py forensics ota_5t --trials 3
    .venv\\Scripts\\python.exe study.py atlas twopole_amp --steps 5
    .venv\\Scripts\\python.exe study.py atlas ota_5t --steps 4 --per-cell 25
    .venv\\Scripts\\python.exe study.py curve twopole_amp --specs 12
    .venv\\Scripts\\python.exe study.py arena

The atlas axes default to the circuit's first two declared goals; pass
--x/--y with lo:hi ranges to chart something else. PDK circuits cost
seconds per simulation, so their charts are bench jobs: start small.
"""

from __future__ import annotations

import argparse
import random
import sys

from spice import (adversary, arena, atlas, circuits, experiment, forensics,
                   ledger, priors)


def _book(label):
    book = ledger.Ledger()
    book.record("note", by="tool", what="study", study=label)
    print("ledger: " + book.path)
    return book


def cmd_adversary(args):
    params = circuits.defaults(args.circuit)
    book = _book("adversary")

    def narrate(row):
        margin = row["worst_margin"]
        text = "  %-12s %s" % (row["stage"], adversary.describe(row))
        if margin is not None:
            text += "  worst margin %+.1f%%" % (margin * 100.0)
        else:
            text += "  " + (row["error"] or "")
        print(text, flush=True)

    found = adversary.attack(args.circuit, params, budget=args.budget,
                             on_each=narrate, book=book)
    print()
    print("verdict: " + found["verdict"]
          + "  (" + str(found["sims"]) + " simulations, "
          + ("%.0f" % found["seconds"]) + " s)")
    print(adversary.claim(found))


def cmd_forensics(args):
    params = circuits.defaults(args.circuit)
    book = _book("forensics")
    rng = random.Random(args.rng_seed)
    hits = 0
    for index in range(args.trials):
        trial = forensics.blind_trial(args.circuit, params, rng,
                                      budget=args.budget, book=book)
        hits += 1 if trial["match"] else 0
        print("trial %d: drew %s; recovered %s; %s (%d sims, residual %s)"
              % (index + 1,
                 forensics.describe(trial["truth"]),
                 forensics.describe(trial["recovered"])
                 if trial["recovered"] else "nothing",
                 "MATCH" if trial["match"] else "MISS",
                 trial["sims"],
                 "%.2e" % trial["residual"]
                 if trial["residual"] is not None else "none"),
              flush=True)
    print()
    print("recovered %d of %d blind conditions" % (hits, args.trials))


def _default_axes(circuit_id, steps):
    block = circuits.get_circuit(circuit_id).get("design")
    if not block:
        raise SystemExit("Circuit " + repr(circuit_id) + " declares no "
                         "design block, so there is nothing to chart.")
    if len(block["goals"]) < 2:
        raise SystemExit("Circuit " + repr(circuit_id) + " declares only "
                         "one goal; pass both axes with --x and --y.")
    axes = []
    for item in block["goals"][:2]:
        # A factor of two each way around the declared default: wide
        # enough to cross the frontier, narrow enough to stay on the map.
        axes.append(atlas.axis(item["key"], item["default"] / 2.0,
                               item["default"] * 2.0, steps))
    return axes


def _parse_axis(text, steps):
    key, _, span = text.partition("=")
    lo, _, hi = span.partition(":")
    try:
        lo_value, hi_value = float(lo), float(hi)
    except ValueError:
        raise SystemExit("An axis is key=lo:hi, for example "
                         "loop_gain_db=30:80. Got: " + text)
    # atlas.axis speaks for itself on a bad range or step count; its
    # message names the actual problem, so it is not rewritten here.
    try:
        return atlas.axis(key, lo_value, hi_value, steps)
    except atlas.AtlasError as exc:
        raise SystemExit(str(exc))


def cmd_atlas(args):
    if args.x and args.y:
        axis_x = _parse_axis(args.x, args.steps)
        axis_y = _parse_axis(args.y, args.steps)
    else:
        axis_x, axis_y = _default_axes(args.circuit, args.steps)
    book = _book("atlas")

    def narrate(cell):
        print("  cell (%d,%d) %s=%.4g %s=%.4g: %s (%d sims)"
              % (cell["ix"], cell["iy"], axis_x["key"], cell["x"],
                 axis_y["key"], cell["y"], cell["verdict"], cell["sims"]),
              flush=True)

    found = atlas.chart(args.circuit, axis_x, axis_y,
                        per_cell=args.per_cell, budget=args.budget,
                        on_cell=narrate, book=book)
    ident = atlas.store(found)
    print()
    print(atlas.render(found))
    print()
    print("met %d, not found within budget %d, not run %d; %d simulations"
          % (found["met"], found["not_found"], found["not_run"],
             found["sims"]))
    print("stored: " + ident)


def cmd_curve(args):
    book = _book("curve")
    rng = random.Random(args.rng_seed)
    specs = priors.random_specs(args.circuit, args.specs, rng)

    def narrate(row):
        print("  spec %2d: cold %3d sims (%s), warm %3d sims (%s, from %s)"
              % (row["index"] + 1,
                 row["cold"]["sims"],
                 "met" if row["cold"]["feasible"] else "not met",
                 row["warm"]["sims"],
                 "met" if row["warm"]["feasible"] else "not met",
                 row["warm"]["start"]),
              flush=True)

    found = priors.learning_curve(args.circuit, specs,
                                  per_spec=args.per_spec, book=book,
                                  on_row=narrate)
    summary = found["summary"]
    print()
    print("specs solved by both arms: %d of %d"
          % (summary["solved_by_both"], summary["specs"]))
    print("simulations on those specs: cold %d, warm %d"
          % (summary["cold_sims_on_solved"], summary["warm_sims_on_solved"]))


def cmd_arena(args):
    book = _book("arena")
    spec = experiment.AMP1
    contestants = {
        "optimizer_cold": arena.optimizer_contestant,
        "optimizer_seeded": arena.seeded_contestant,
    }
    found = arena.contest(spec, contestants, book=book,
                          on_result=lambda row: print(
                              "  %-18s %s, %s sims"
                              % (row["arm"], row["status"],
                                 row.get("sims_total")), flush=True))
    print()
    for row in found["scoreboard"]:
        print("%d. %-18s %s  %s sims  worst margin %s"
              % (row["rank"], row["arm"],
                 "met" if row["feasible"] else "not met",
                 row["sims_total"],
                 "%+.3f" % row["worst_margin"]
                 if row["worst_margin"] is not None else "none"))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="study.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    one = commands.add_parser("adversary",
                              help="spend a budget trying to break a design")
    one.add_argument("circuit")
    one.add_argument("--budget", type=int, default=40)
    one.set_defaults(run=cmd_adversary)

    one = commands.add_parser("forensics",
                              help="recover blind conditions from numbers")
    one.add_argument("circuit")
    one.add_argument("--trials", type=int, default=3)
    one.add_argument("--budget", type=int, default=30)
    one.add_argument("--rng-seed", type=int, default=None)
    one.set_defaults(run=cmd_forensics)

    one = commands.add_parser("atlas",
                              help="chart the measured frontier of a circuit")
    one.add_argument("circuit")
    one.add_argument("--steps", type=int, default=5)
    one.add_argument("--per-cell", type=int, default=30)
    one.add_argument("--budget", type=int, default=None)
    one.add_argument("--x", default=None, help="key=lo:hi")
    one.add_argument("--y", default=None, help="key=lo:hi")
    one.set_defaults(run=cmd_atlas)

    one = commands.add_parser("curve",
                              help="measure what accumulated experience saves")
    one.add_argument("circuit")
    one.add_argument("--specs", type=int, default=12)
    one.add_argument("--per-spec", type=int, default=40)
    one.add_argument("--rng-seed", type=int, default=None)
    one.set_defaults(run=cmd_curve)

    one = commands.add_parser("arena",
                              help="the reference contestants, under the rules")
    one.set_defaults(run=cmd_arena)

    args = parser.parse_args(argv)
    args.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
