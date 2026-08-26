"""Blame assignment: which knob moves the number that is failing.

"Phase margin is short. What do I turn?" is the question every junior
engineer carries to a senior one. The senior's answer is a gradient held
in their head. This module measures that gradient instead: perturb each
tunable parameter a small step in both directions, re-simulate, and
report how much each watched metric moved per natural unit of the knob.

Honesty notes, because a sensitivity is easy to oversell:

- These are finite differences at THIS sizing, measured with a +/-5%
  step, two simulations per knob. They are local slopes, not a model of
  the circuit; a step twice as far may behave differently.
- Every number is the difference of two ngspice results. Nothing is
  fitted, extrapolated, or estimated.
- The step is relative in the parameter and the slope is reported per
  natural unit (per micron, per picofarad, per microamp), so the numbers
  read the way an engineer turns the knob.
"""

from . import charact, circuits

#: Relative perturbation per side. Small enough to be local, large enough
#: that the difference clears wrdata's printed precision.
STEP = 0.05


def _natural_unit(param):
    """The unit a slope is reported against, from the parameter's own."""
    unit = (param.get("unit") or "").strip()
    return unit if unit else "unit"


def sensitivities(circuit_id, params, targets=None,
                  on_progress=None, should_stop=None):
    """Measure d(metric)/d(knob) for every tunable, two sims per knob.

    Returns the centre measurement, the per-knob slopes, and the binding
    goal at this sizing, so the caller can sort by what matters. targets
    overrides the registry's default goal targets, the same way the
    design panel's inputs do.
    """
    circuit = circuits.get_circuit(circuit_id)
    design = circuit.get("design") or {}
    tunable = list(design.get("tunable") or [])
    if not tunable:
        raise ValueError("Circuit " + repr(circuit_id) + " declares no "
                         "tunable parameters, so there is nothing to blame.")

    values = circuits.defaults(circuit_id)
    values.update(params or {})
    specs = {p["key"]: p for p in circuit["params"]}

    def tell(text):
        if on_progress is not None:
            on_progress(text)

    tell("measuring the centre")
    centre = circuits.simulate(circuit_id, dict(values))
    margins = charact.margins_of(circuit, centre, targets)
    watched = [m["key"] for m in margins] or [
        key for key, value in centre.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)]

    knobs = []
    for name in tunable:
        if should_stop is not None and should_stop():
            break
        spec = specs.get(name, {})
        base = values[name]
        step = abs(base) * STEP
        if step == 0.0:
            continue
        lo_value, hi_value = base - step, base + step
        # Stay inside the declared box; a slope measured outside the range
        # the optimizer may use would blame a move nobody can make.
        if "min" in spec:
            lo_value = max(lo_value, spec["min"])
        if "max" in spec:
            hi_value = min(hi_value, spec["max"])
        if hi_value <= lo_value:
            continue

        tell("perturbing " + name)
        lo = circuits.simulate(circuit_id, dict(values, **{name: lo_value}))
        hi = circuits.simulate(circuit_id, dict(values, **{name: hi_value}))

        slopes = {}
        for key in watched:
            a, b = lo.get(key), hi.get(key)
            if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
                continue
            slopes[key] = (b - a) / (hi_value - lo_value)

        knobs.append({
            "param": name,
            "label": spec.get("label", name),
            "unit": _natural_unit(spec),
            "at": base,
            "step_lo": lo_value,
            "step_hi": hi_value,
            "slopes": slopes,
        })

    return {
        "circuit": circuit_id,
        "centre": {key: centre[key] for key in watched if key in centre},
        "margins": margins,
        "knobs": knobs,
        "method": ("central difference, +/-%d%% per knob, two ngspice runs "
                   "per knob, at this sizing only" % round(STEP * 100)),
        "sims": 1 + 2 * len(knobs),
    }


def sentence(found, present):
    """One sentence a senior engineer would say, built from the numbers.

    present(value, key) formats a metric value the way the page does; the
    slopes are shown per natural unit of the knob. Returns None when no
    goal is declared, because without a target nothing is binding.
    """
    margins = found["margins"]
    if not margins:
        return None
    binding = next((m for m in margins if m.get("binding")), None)
    if binding is None:
        return None

    movers = []
    for knob in found["knobs"]:
        slope = knob["slopes"].get(binding["key"])
        if slope is not None:
            movers.append((abs(slope), slope, knob))
    if not movers:
        return None
    movers.sort(reverse=True, key=lambda item: item[0])

    state = "binding" if binding["met"] else "failing"
    parts = ["%s is %s." % (binding["label"], state)]
    for _, slope, knob in movers[:2]:
        parts.append("It moves %s per %s of %s." % (
            present(slope, binding["key"]), knob["unit"], knob["label"]))
    return " ".join(parts)
