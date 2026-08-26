"""Feasibility triage: is this spec even reachable, and what binds it.

The first question in any design meeting is whether the spec is possible
in this process, and it is usually argued rather than measured. Triage
answers the cheap half by measurement: one simulation at the current
sizing gives every goal its margin and names the one that binds. The
experiment's preflight prover taught the house this move; here it is as
a user-facing feature.

The sweep is the second half, and it is labelled carefully: it
sweeps ONE declared knob (the bias current) across its registry range and
reports what power, bandwidth and margin each point measured. That is a
slice along one axis, not a Pareto front -- a real front would need an
optimization per point. A slice is still the honest version of the plot
engineers sketch on whiteboards: where the bandwidth-per-watt wall is,
along the knob that moves power most.
"""

from . import charact, circuits

#: Points per sweep: enough to draw the bend, cheap enough to
#: wait for. One simulation each.
SWEEP_POINTS = 8


def verdict(circuit_id, params, targets=None):
    """One simulation, every margin, the binding goal, a plain sentence."""
    circuit = circuits.get_circuit(circuit_id)
    values = circuits.defaults(circuit_id)
    values.update(params or {})

    measured = circuits.simulate(circuit_id, dict(values))
    margins = charact.margins_of(circuit, measured, targets)
    if not margins:
        return {"circuit": circuit_id, "margins": [],
                "feasible_here": None,
                "sentence": "This circuit declares no design goals, so "
                            "there is nothing to triage."}

    met = all(m["met"] for m in margins)
    binding = next(m for m in margins if m.get("binding"))
    if met:
        sentence = ("Every target holds at this sizing. The tightest is "
                    "%s at %.1f%% margin." % (binding["label"],
                                              binding["margin"] * 100.0))
    else:
        missed = [m for m in margins if not m["met"]]
        sentence = ("%s is infeasible at this sizing, short by %.1f%%."
                    % (binding["label"], -binding["margin"] * 100.0))
        if len(missed) > 1:
            sentence += (" %d of %d targets are missed."
                         % (len(missed), len(margins)))

    return {"circuit": circuit_id, "margins": margins,
            "feasible_here": met, "binding": binding["key"],
            "sentence": sentence, "sims": 1}


def sweep_knob(circuit_id):
    """The declared sweep knob, or None. Registry-driven on purpose."""
    design = circuits.get_circuit(circuit_id).get("design") or {}
    return design.get("sweep")


def sweep(circuit_id, params, on_progress=None, should_stop=None):
    """Sweep the declared knob across its registry range, log-spaced.

    Every point is one real simulation at this sizing with only the knob
    moved. Points where the circuit cannot be measured are reported with
    their error: a bias that breaks the amplifier is part of the picture.
    """
    knob = sweep_knob(circuit_id)
    if not knob:
        raise ValueError("Circuit " + repr(circuit_id) + " declares no "
                         "sweep knob.")
    circuit = circuits.get_circuit(circuit_id)
    spec = next(p for p in circuit["params"] if p["key"] == knob)
    lo, hi = spec["min"], spec["max"]

    values = circuits.defaults(circuit_id)
    values.update(params or {})

    points = []
    ratio = (hi / lo) ** (1.0 / (SWEEP_POINTS - 1))
    for index in range(SWEEP_POINTS):
        if should_stop is not None and should_stop():
            break
        at = lo * (ratio ** index)
        if on_progress is not None:
            on_progress("point %d of %d" % (index + 1, SWEEP_POINTS))
        row = {"value": at}
        try:
            measured = circuits.simulate(circuit_id, dict(values,
                                                          **{knob: at}))
            row["measured"] = {
                key: value for key, value in measured.items()
                if isinstance(value, (int, float))
                and not isinstance(value, bool)}
            row["error"] = None
        except Exception as exc:  # noqa: BLE001 - a broken bias is a finding
            row["measured"] = None
            row["error"] = str(exc).splitlines()[0]
        points.append(row)

    return {
        "circuit": circuit_id,
        "knob": knob,
        "knob_label": spec.get("label", knob),
        "knob_unit": spec.get("unit", ""),
        "at": values[knob],
        "points": points,
        "method": ("one-knob slice along %s from %g to %g, log-spaced, "
                   "one ngspice run per point; not a Pareto front"
                   % (knob, lo, hi)),
        "sims": len(points),
    }
