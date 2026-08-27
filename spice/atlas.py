"""The capability atlas: what a circuit family can be made to do, measured.

Ask what a five-transistor OTA can deliver and the answer is folklore:
rules of thumb, a designer's recollection, a textbook's example. This
module replaces the folklore for one process and one topology at a time.
It lays a grid over two of the circuit's spec axes, and at every grid
point it runs the same design search the tool ships, under a counted
simulation budget, and records whether a sizing was found that meets that
cell's targets.

The claims a cell may make are deliberately weak, and that is the point:

    met         a sizing was found and measured meeting these targets, and
                the sizing is stored with the cell, so the claim can be
                re-simulated by anyone.
    not_found   the search spent its budget without meeting the targets.
                This is a statement about the search, never about physics:
                "not found within 30 evaluations from this start" is all
                the evidence supports, and all the cell says.
    not_run     the chart's total budget ran out before this cell.

The boundary between met and not_found, read across the grid, is the
measured frontier of what this tool can currently deliver, a lower bound
on the topology drawn from evidence rather than authority. Every claim of
"met" carries its sizing; nothing here asserts impossibility.

A met cell is not re-simulated after the search: the declared point is the
best point, and the best point was measured by the search itself, memoised
against revisits. Re-running the same deck would double the atlas's cost
to confirm what determinism already guarantees. The stored sizing makes
the check available to anyone who doubts it.

Charts are stored beside the ledger under <ledger-root>/atlas/, following
the characterization store's pattern, because an atlas that takes hours to
draw should outlive the process that drew it.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import time

from . import circuits, design, ledger, runner

#: The smallest grid worth calling an atlas: below this it is a spot check.
MIN_STEPS = 2
MAX_STEPS = 12

_SAFE_ID = re.compile(r"^[a-z0-9_]+-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")

#: A cell's verdict is exactly one of these.
VERDICTS = ("met", "not_found", "not_run")

#: Where a cell's search starts. "defaults" is the registry sizing;
#: "seed" asks the circuit's seed rule for a start fitted to the cell's
#: own targets, when the circuit declares one.
START_POLICIES = ("defaults", "seed")


class AtlasError(ValueError):
    """A chart request that cannot run. Maps to HTTP 400."""


def axis(key, lo, hi, steps):
    """One spec axis: geometrically spaced targets from lo to hi.

    Geometric, because specs live on ratio scales: the step from 40 to
    50 dB and the step from 60 to 75 dB are the same size decision.
    """
    if not (0 < lo < hi):
        raise AtlasError("Axis " + repr(key) + " needs 0 < lo < hi.")
    if not MIN_STEPS <= steps <= MAX_STEPS:
        raise AtlasError(
            "Axis steps must be between " + str(MIN_STEPS) + " and "
            + str(MAX_STEPS) + "."
        )
    ratio = (hi / lo) ** (1.0 / (steps - 1))
    values = [float("%.6g" % (lo * ratio ** index)) for index in range(steps)]
    return {"key": key, "values": values}


def _goal_of(block, key):
    for item in block["goals"]:
        if item["key"] == key:
            return item
    raise AtlasError(
        "Axis " + repr(key) + " is not a goal of this circuit. Its goals "
        "are: " + ", ".join(item["key"] for item in block["goals"]) + "."
    )


def chart(circuit_id, axis_x, axis_y, per_cell=30, start="defaults",
          fixed_targets=None, budget=None, on_cell=None, should_stop=None,
          book=None):
    """Draw the atlas for one circuit over two spec axes.

    axis_x and axis_y come from axis(). per_cell is each cell's search
    budget in evaluations; budget, when given, is a hard ceiling on the
    whole chart's simulations, enforced at the subprocess. fixed_targets
    pins the remaining goals; unset ones hold their declared defaults.
    """
    circuit, block = design.design_block(circuit_id)
    goal_x = _goal_of(block, axis_x["key"])
    goal_y = _goal_of(block, axis_y["key"])
    if axis_x["key"] == axis_y["key"]:
        raise AtlasError("The two axes must be different goals.")
    if start not in START_POLICIES:
        raise AtlasError(
            "Unknown start policy " + repr(start) + ". One of: "
            + ", ".join(START_POLICIES) + "."
        )
    if start == "seed" and not block.get("seed"):
        raise AtlasError(
            "Circuit " + repr(circuit_id) + " declares no seed rule, so the "
            "seed start policy cannot be honoured. Use \"defaults\"."
        )

    defaults = circuits.defaults(circuit_id)
    if book is not None:
        book.record("start", by="tool", what="atlas", circuit=circuit_id,
                    axis_x=axis_x, axis_y=axis_y, per_cell=per_cell,
                    start=start, budget=budget,
                    fixed_targets=fixed_targets or {})

    cells = []
    watcher = runner.SimObserver(ledger=book, budget=budget, phase="atlas")
    exhausted = False
    with runner.observing(watcher):
        for iy, y_value in enumerate(axis_y["values"]):
            for ix, x_value in enumerate(axis_x["values"]):
                cell = {"ix": ix, "iy": iy,
                        "x": x_value, "y": y_value}
                # A cell only starts if the whole per-cell allowance is
                # left: a cell searched on scraps would report "not found"
                # for cells the budget, not the physics, gave up on.
                remaining = (None if budget is None
                             else budget - watcher.count)
                if exhausted or (remaining is not None
                                 and remaining < per_cell) \
                        or (should_stop is not None and should_stop()):
                    cell.update(verdict="not_run", sims=0, evals=0,
                                params=None, measured=None,
                                worst_margin=None,
                                reason="the chart budget ran out"
                                if remaining is not None
                                and remaining < per_cell else "not run")
                    cells.append(cell)
                    continue

                requested = dict(fixed_targets or {})
                requested[axis_x["key"]] = x_value
                requested[axis_y["key"]] = y_value

                if start == "seed":
                    begin, _ = design.seed_params(circuit_id, requested,
                                                  dict(defaults))
                else:
                    begin = dict(defaults)

                before = watcher.count
                try:
                    found = design.run_design(
                        circuit_id, begin, requested, per_cell,
                        should_stop=should_stop, ledger=book, arm=None)
                except runner.SimBudgetExhausted:
                    exhausted = True
                    cell.update(verdict="not_run", sims=watcher.count - before,
                                evals=0, params=None, measured=None,
                                worst_margin=None,
                                reason="the chart budget ran out here")
                    cells.append(cell)
                    if on_cell is not None:
                        on_cell(cell)
                    continue

                best = found["best"]
                cell["sims"] = watcher.count - before
                cell["evals"] = found["evals"]
                cell["worst_margin"] = None if best is None else best["score"]
                # The search swallows a mid-cell budget stop as unmeasurable
                # candidates (they are the same exception family), so a cell
                # whose history carries those refusals was cut off, not
                # searched out. The history is the evidence; a count
                # comparison would also condemn a cell that legitimately
                # finished exactly at the boundary.
                cut_off = any(
                    entry.get("error") and "budget" in entry["error"]
                    for entry in found["history"])
                if cut_off and not found["feasible"]:
                    exhausted = True
                    cell.update(verdict="not_run", params=None, measured=None,
                                reason="the chart budget ran out here")
                    cells.append(cell)
                    if on_cell is not None:
                        on_cell(cell)
                    continue
                # A stop that fires mid-cell ends the search early with a
                # normal return. That cell was interrupted, not searched
                # out, and "not found within budget" would be a lie about
                # a budget it never got to spend.
                if (not found["feasible"] and should_stop is not None
                        and should_stop()):
                    cell.update(verdict="not_run", params=None, measured=None,
                                reason="stopped before the search finished")
                    cells.append(cell)
                    if on_cell is not None:
                        on_cell(cell)
                    continue
                if found["feasible"]:
                    cell.update(verdict="met", params=best["params"],
                                measured=best["measured"],
                                reason=found["reason"])
                else:
                    cell.update(verdict="not_found", params=None,
                                measured=None, reason=found["reason"])
                cells.append(cell)
                if on_cell is not None:
                    on_cell(cell)

    found = {
        "circuit": circuit_id,
        "axes": {
            "x": dict(axis_x, op=goal_x["op"]),
            "y": dict(axis_y, op=goal_y["op"]),
        },
        "start": start,
        "per_cell": per_cell,
        "budget": budget,
        "fixed_targets": fixed_targets or {},
        "cells": cells,
        "met": sum(1 for cell in cells if cell["verdict"] == "met"),
        "not_found": sum(1 for cell in cells
                         if cell["verdict"] == "not_found"),
        "not_run": sum(1 for cell in cells if cell["verdict"] == "not_run"),
        "sims": watcher.count,
        "seconds": round(watcher.seconds, 3),
        "when_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    }
    found["frontier"] = frontier(found)
    if book is not None:
        book.record("result", by="tool", what="atlas", sims=watcher.count,
                    met=found["met"], not_found=found["not_found"],
                    not_run=found["not_run"])
    return found


def frontier(chart_result):
    """Per column of x, the hardest y target met within budget.

    Hardness follows the y goal's own direction: for a ">=" goal a larger
    target is harder, for "<=" a smaller one. Columns where nothing was
    met report None, which means exactly "nothing met here within budget".
    """
    op = chart_result["axes"]["y"]["op"]
    columns = {}
    for cell in chart_result["cells"]:
        if cell["verdict"] != "met":
            continue
        held = columns.get(cell["ix"])
        harder = cell["y"] if held is None else (
            max(held, cell["y"]) if op == ">=" else min(held, cell["y"]))
        columns[cell["ix"]] = harder
    return [
        {"ix": index, "x": value, "hardest_y_met": columns.get(index)}
        for index, value in enumerate(chart_result["axes"]["x"]["values"])
    ]


def render(chart_result):
    """The atlas as a text grid, hardest y first: met cells are filled.

    A reading aid for the terminal, not the record; the record is the
    stored chart with every cell's sizing.
    """
    lines = []
    x_values = chart_result["axes"]["x"]["values"]
    y_values = chart_result["axes"]["y"]["values"]
    marks = {("met"): "#", ("not_found"): ".", ("not_run"): " "}
    by_pos = {(cell["ix"], cell["iy"]): cell
              for cell in chart_result["cells"]}
    order = list(enumerate(y_values))
    if chart_result["axes"]["y"]["op"] == ">=":
        order.reverse()
    for iy, y_value in order:
        row = "".join(
            marks[by_pos[(ix, iy)]["verdict"]] if (ix, iy) in by_pos else " "
            for ix in range(len(x_values)))
        lines.append(("%10.4g | " % y_value) + row)
    lines.append(" " * 10 + " +-" + "-" * len(x_values))
    lines.append(" " * 13 + "%s from %.4g to %.4g; # met, . not found "
                 "within budget, blank not run"
                 % (chart_result["axes"]["x"]["key"],
                    x_values[0], x_values[-1]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the store: charts that outlive the process that drew them
# ---------------------------------------------------------------------------


def store_root():
    """Where charts live: beside the ledger, never the project."""
    return os.path.join(ledger.root(), "atlas")


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def store(result):
    """Write one chart; return its id, circuit-stamp-token like every
    other store beside the ledger."""
    ident = "%s-%s-%s" % (result["circuit"], _stamp(), secrets.token_hex(3))
    directory = store_root()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, ident + ".json")
    with io.open(path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(dict(result, id=ident), stream, indent=1)
    return ident


def load(ident):
    """One stored chart, or None. Malformed ids are refused so a request
    can never walk the filesystem."""
    if not _SAFE_ID.match(ident or ""):
        return None
    path = os.path.join(store_root(), ident + ".json")
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as stream:
        return json.load(stream)


def listing(circuit_id=None):
    """Stored charts, newest first."""
    directory = store_root()
    if not os.path.isdir(directory):
        return []
    rows = []
    for name in sorted(os.listdir(directory), reverse=True):
        if not name.endswith(".json"):
            continue
        ident = name[:-5]
        if not _SAFE_ID.match(ident):
            continue
        if circuit_id and not ident.startswith(circuit_id + "-"):
            continue
        found = load(ident)
        if not found:
            continue
        rows.append({
            "id": ident,
            "circuit": found["circuit"],
            "axes": [found["axes"]["x"]["key"], found["axes"]["y"]["key"]],
            "grid": [len(found["axes"]["x"]["values"]),
                     len(found["axes"]["y"]["values"])],
            "met": found["met"],
            "sims": found["sims"],
            "when_utc": found.get("when_utc"),
        })
    return rows
