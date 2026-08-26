"""When the wiring breaks the spec, size against the wiring and draw again.

The tool has been able to say what the interconnect costs since the wires
were first drawn: it measures the specs on the schematic, hangs the drawn
capacitance on the nets, and measures again. Drawing the two-stage's
compensation network cost three and a half degrees of phase margin, and
nothing did anything about it. A number nobody acts on is a diagnosis
without a treatment.

This closes that. If the drawn layout misses a target the schematic met,
the search runs again -- with the parasitics in the deck, so it is sizing
the circuit that exists rather than the one on paper -- and the result is
laid out again. The wiring changes when the sizing does, so the loop
re-extracts and re-checks each time rather than assuming the first
extraction still holds.

It terminates on the first of: the layout meets the spec, the sizing stops
moving, or the round budget runs out. It never reports a design it has not
laid out and measured, and it never reports success on a round it did not
finish.
"""

import time

from . import circuits, design, layout, runner

#: How many draw-and-resize rounds before giving up. Each round is a full
#: search plus two layout simulations, so this is minutes, not seconds.
MAX_ROUNDS = 3

#: A round that changes every tunable by less than this has converged: the
#: search is chasing noise in the extraction rather than a real gain.
SETTLED = 0.01

#: Why a loop stopped. Anything that is not "met" is not a success.
OUTCOMES = ("met", "unconverged", "settled", "rounds_exhausted",
            "no_layout", "error")


class LoopError(RuntimeError):
    """Raised when the loop cannot run, never when it merely fails to meet."""


def _moved(before, after):
    """The largest fractional move in any tunable between two sizings."""
    worst = 0.0
    for key, value in after.items():
        old = before.get(key)
        if not old:
            continue
        worst = max(worst, abs(value - old) / abs(old))
    return worst


def parasitics_of(circuit_id, params):
    """The drawn wiring for one sizing, as capacitance per net.

    Re-extracted every round, because the wiring is a consequence of the
    sizing: wider devices are further apart, and their nets are longer.
    Reusing the first round's extraction would be sizing against a layout
    that no longer exists.
    """
    from . import topologies

    tech = layout.tech_constants()
    block = circuits.get_circuit(circuit_id).get("floorplan")
    if block is None:
        raise LoopError(
            "The circuit " + repr(circuit_id) + " has no layout, so there is "
            "no interconnect to size against."
        )
    plan = layout.floorplan(
        block["devices"](params), tech,
        passives=topologies.drawable_passives(circuit_id, params))
    routed = layout.route(plan, topologies.circuit_nets(circuit_id, params),
                          tech)
    found = layout.routed_parasitics(routed, tech)
    for net, extra in layout.passive_parasitics(plan, tech).items():
        entry = found.setdefault(net, {"length_um": 0.0, "capacitance_f": 0.0,
                                       "devices": [], "segments": 0})
        entry["capacitance_f"] += extra
    return found, plan


def close_loop(circuit_id, params, targets, budget=40, max_rounds=MAX_ROUNDS,
               book=None, arm="closed_loop"):
    """Size, draw, measure the drawn circuit, and size again if it missed.

    Returns every round it ran, so the cost of closing the loop is visible
    rather than hidden behind a final answer.
    """
    block = circuits.get_circuit(circuit_id).get("design")
    if block is None:
        raise LoopError(
            "The circuit " + repr(circuit_id) + " has no design block, so "
            "there is nothing to re-size."
        )
    targets = design.resolve_targets(block, targets)

    sizing = dict(params)
    rounds = []
    outcome = "rounds_exhausted"
    started = time.time()

    for index in range(max_rounds):
        parasitics, plan = parasitics_of(circuit_id, sizing)
        transform = layout.parasitic_transform(parasitics)

        # The circuit as drawn: the same sizing, measured with its own
        # wiring in the deck. This is the number that decides the round.
        loaded = circuits.simulate(circuit_id, sizing, transform=transform)
        value, margins = design.score_measurement(
            block["goals"], targets, loaded)

        entry = {
            "round": index + 1,
            "params": dict(sizing),
            "loaded_measured": loaded,
            "margins": margins,
            "feasible": value >= 0.0,
            "binding_goal": min(margins, key=lambda key: margins[key]),
            "interconnect_f": sum(item["capacitance_f"]
                                  for item in parasitics.values()),
            "area_um2": plan["area_um2"],
        }

        if book is not None:
            book.record("layout", by="tool", arm=arm, circuit=circuit_id,
                        round=index + 1, params=dict(sizing),
                        area_um2=plan["area_um2"],
                        interconnect_f=entry["interconnect_f"],
                        feasible_loaded=entry["feasible"],
                        binding_goal=entry["binding_goal"])

        if entry["feasible"]:
            entry["action"] = "none: the drawn circuit meets the spec"
            rounds.append(entry)
            outcome = "met"
            break

        if index == max_rounds - 1:
            entry["action"] = "none: out of rounds"
            rounds.append(entry)
            break

        # Size again, against the deck that has the wiring in it. Sizing
        # against the schematic here would re-derive the design that just
        # failed.
        result = design.run_design(
            circuit_id, sizing, targets, budget,
            transform=transform, ledger=book, arm=arm,
        )
        if not result.get("best") or not result["best"].get("params"):
            entry["action"] = "gave up: no measurable point with the wiring in"
            rounds.append(entry)
            outcome = "unconverged"
            break

        resized = dict(sizing)
        resized.update(result["best"]["params"])
        move = _moved(sizing, resized)
        entry["action"] = ("resized against the drawn wiring, largest move "
                           + ("%.1f%%" % (move * 100.0)))
        entry["resize_evals"] = result["evals"]
        rounds.append(entry)

        if move < SETTLED:
            outcome = "settled"
            break
        sizing = resized

    return {
        "circuit": circuit_id,
        "targets": targets,
        "outcome": outcome,
        "met": outcome == "met",
        "rounds": rounds,
        "final_params": rounds[-1]["params"] if rounds else dict(params),
        "wall_s": round(time.time() - started, 3),
        # Said in the result: a loop that ran out of rounds has not
        # converged on anything, and must not be read as if it had.
        "coverage": (
            "Each round draws the sizing, extracts its wiring, measures the "
            "circuit with that wiring in the deck, and re-sizes against the "
            "same deck if a target is missed. The wiring is re-extracted "
            "every round because it is a consequence of the sizing. An "
            "outcome other than 'met' is not a design that meets the spec "
            "once it is drawn."
        ),
    }
