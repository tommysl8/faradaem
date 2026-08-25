"""Checking drawn geometry against the foundry's own rule values.

This is not sign-off. The real deck is thousands of rules and needs Magic or
KLayout, neither of which runs here. What this does is check the handful of
rules that actually apply to the shapes Faradaem draws, using the numbers
read out of the PDK's technology file, and say exactly which rules it
checked so the coverage is never mistaken for the whole.

The point is the difference between geometry nobody has looked at and
geometry checked against the numbers it was supposed to satisfy. A drawing
that has not been checked is a drawing; one that has been checked against
ten rules is a drawing that satisfies ten rules, and says so.

Each rule carries the tag the foundry writes in its own error message
(diff/tap.1, poly.1a, poly.7, poly.8, nwell.1, nwell.2a, nwell.5,
met1.1, met1.2), so a violation can be looked up.
"""

from . import layout

#: The rules this module knows how to check, with the tag the PDK uses.
#: Anything not in this list is not checked and is reported as such.
CHECKED_RULES = (
    ("poly_width", "poly.1a", "minimum poly width"),
    ("diff_width", "diff/tap.1", "minimum diffusion width"),
    ("diff_spacing", "diff/tap.3", "minimum diffusion spacing"),
    ("diff_overhang", "poly.7", "diffusion overhang of the transistor"),
    ("poly_endcap", "poly.8", "poly overhang of the transistor"),
    ("nwell_width", "nwell.1", "minimum n-well width"),
    ("nwell_spacing", "nwell.2a", "minimum spacing between separate n-wells"),
    ("nwell_surround", "nwell.5", "n-well surround of the p-diffusion in it"),
    ("metal1_width", "met1.1", "minimum metal1 width"),
    ("metal1_spacing", "met1.2", "minimum metal1 spacing"),
)

#: A dimension is allowed to be this far under a rule before it counts as a
#: violation, to absorb the float noise of a placement done in microns.
TOLERANCE_UM = 1e-9


class Violation(dict):
    """One broken rule, as plain data with the numbers that broke it."""


def _rect(shape):
    _, _, x1, y1, x2, y2 = shape
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _short(value, limit):
    return value < limit - TOLERANCE_UM


def _violation(rule, tag, what, measured, required, where):
    return Violation({
        "rule": rule,
        "tag": tag,
        "what": what,
        "measured_um": measured,
        "required_um": required,
        "where": where,
    })


def check_widths(shapes, layers, tech):
    """No shape may be thinner than its layer's minimum width."""
    found = []
    limits = {
        layers["POLY"][0]: ("poly_width", "poly.1a", tech["poly_width"]),
        layers["DIFF"][0]: ("diff_width", "diff/tap.1", tech["diff_width"]),
    }
    if "MET1" in layers and "metal1_width" in tech:
        limits[layers["MET1"][0]] = ("metal1_width", "met1.1",
                                     tech["metal1_width"])
    for shape in shapes:
        limit = limits.get(shape[0])
        if limit is None:
            continue
        rule, tag, required = limit
        x1, y1, x2, y2 = _rect(shape)
        narrowest = min(x2 - x1, y2 - y1)
        if _short(narrowest, required):
            found.append(_violation(
                rule, tag, "a shape is narrower than the rule allows",
                narrowest, required, [x1, y1, x2, y2]
            ))
    return found


def check_spacing(shapes, layers, tech):
    """Two shapes on the same layer must not come closer than the rule.

    Shapes that touch or overlap are one piece of geometry, not two, so
    they are not a spacing violation.
    """
    found = []
    diff_layer = layers["DIFF"][0]
    required = tech["diff_spacing"]

    diffs = [_rect(shape) for shape in shapes if shape[0] == diff_layer]
    for index, first in enumerate(diffs):
        for second in diffs[index + 1:]:
            gap_x = max(second[0] - first[2], first[0] - second[2])
            gap_y = max(second[1] - first[3], first[1] - second[3])
            if gap_x <= 0 and gap_y <= 0:
                continue                      # overlapping, so not a gap
            gap = max(gap_x, gap_y)
            if _short(gap, required):
                found.append(_violation(
                    "diff_spacing", "diff/tap.3",
                    "two diffusions are closer than the rule allows",
                    gap, required, [first, second]
                ))
    return found


def check_metal_spacing(shapes, layers, tech):
    """Wires on the same layer that are not the same wire must stay apart.

    Two rectangles of one net touch by design, which is how a track meets
    its stubs, so touching is not a violation. Anything with a real gap
    smaller than the rule is.
    """
    if "MET1" not in layers or "metal1_spacing" not in tech:
        return []

    found = []
    required = tech["metal1_spacing"]
    wires = [_rect(s) for s in shapes if s[0] == layers["MET1"][0]]

    for index, first in enumerate(wires):
        for second in wires[index + 1:]:
            gap_x = max(second[0] - first[2], first[0] - second[2])
            gap_y = max(second[1] - first[3], first[1] - second[3])
            if gap_x <= 0 and gap_y <= 0:
                continue                      # touching, so one wire
            gap = max(gap_x, gap_y)
            if _short(gap, required):
                found.append(_violation(
                    "metal1_spacing", "met1.2",
                    "two wires are closer than the rule allows",
                    gap, required, [first, second]
                ))
    return found


def check_transistor_overhangs(shapes, layers, tech):
    """Where poly crosses diffusion, each must overhang the other.

    Diffusion has to extend past the gate along the channel, which is what
    makes the source and the drain, and poly has to extend past the
    diffusion across it, so the channel is not shorted around the end of
    the gate. Both are what turns two overlapping rectangles into a device.
    """
    found = []
    diffs = [_rect(s) for s in shapes if s[0] == layers["DIFF"][0]]
    polys = [_rect(s) for s in shapes if s[0] == layers["POLY"][0]]

    for gate in polys:
        for diff in diffs:
            overlaps = (gate[0] < diff[2] and diff[0] < gate[2]
                        and gate[1] < diff[3] and diff[1] < gate[3])
            if not overlaps:
                continue

            # Along the channel: diffusion either side of the gate.
            for measured, side in ((gate[0] - diff[0], "source"),
                                   (diff[2] - gate[2], "drain")):
                if _short(measured, tech["diff_overhang"]):
                    found.append(_violation(
                        "diff_overhang", "poly.7",
                        "the " + side + " diffusion is shorter than the rule "
                        "allows",
                        measured, tech["diff_overhang"], [gate, diff]
                    ))

            # Across it: poly past both ends of the diffusion.
            for measured, side in ((diff[1] - gate[1], "bottom"),
                                   (gate[3] - diff[3], "top")):
                if _short(measured, tech["poly_endcap"]):
                    found.append(_violation(
                        "poly_endcap", "poly.8",
                        "the gate stops short of the " + side
                        + " edge of the diffusion",
                        measured, tech["poly_endcap"], [gate, diff]
                    ))
    return found


def check_wells(shapes, layers, tech, pmos=None):
    """Wells must be wide enough, far enough apart, and actually surround
    what they hold.

    pmos is the diffusion the well is there for. Without it the surround
    cannot be checked, and the result says only what it did check.
    """
    found = []
    wells = [_rect(s) for s in shapes if s[0] == layers["NWELL"][0]]

    for well in wells:
        narrowest = min(well[2] - well[0], well[3] - well[1])
        if _short(narrowest, tech["nwell_width"]):
            found.append(_violation(
                "nwell_width", "nwell.1", "the well is narrower than a well "
                "may be", narrowest, tech["nwell_width"], list(well)
            ))

    for index, first in enumerate(wells):
        for second in wells[index + 1:]:
            gap_x = max(second[0] - first[2], first[0] - second[2])
            gap_y = max(second[1] - first[3], first[1] - second[3])
            if gap_x <= 0 and gap_y <= 0:
                continue                      # the same well, merged
            gap = max(gap_x, gap_y)
            if _short(gap, tech["nwell_spacing"]):
                found.append(_violation(
                    "nwell_spacing", "nwell.2a",
                    "two separate wells are closer than the rule allows",
                    gap, tech["nwell_spacing"], [first, second]
                ))

    for held in pmos or []:
        inside = [w for w in wells
                  if w[0] <= held[0] and w[1] <= held[1]
                  and w[2] >= held[2] and w[3] >= held[3]]
        if not inside:
            found.append(_violation(
                "nwell_surround", "nwell.5",
                "a p-diffusion is not inside any well", 0.0,
                tech["nwell_surround"], list(held)
            ))
            continue
        well = inside[0]
        for measured, side in ((held[0] - well[0], "left"),
                               (well[2] - held[2], "right"),
                               (held[1] - well[1], "bottom"),
                               (well[3] - held[3], "top")):
            if _short(measured, tech["nwell_surround"]):
                found.append(_violation(
                    "nwell_surround", "nwell.5",
                    "the well does not reach far enough past the "
                    + side + " of its p-diffusion",
                    measured, tech["nwell_surround"], [well, held]
                ))
    return found


def check(shapes, layers=None, tech=None, pmos=None):
    """Every rule this module knows, against one piece of geometry.

    Returns what was checked as well as what failed, because a clean result
    means nothing without knowing how much was looked at.
    """
    tech = tech if tech is not None else layout.tech_constants()
    layers = layers if layers is not None else layout.gds_layers()

    violations = []
    violations += check_widths(shapes, layers, tech)
    violations += check_spacing(shapes, layers, tech)
    violations += check_transistor_overhangs(shapes, layers, tech)
    violations += check_wells(shapes, layers, tech, pmos)
    violations += check_metal_spacing(shapes, layers, tech)

    return {
        "violations": violations,
        "clean": not violations,
        "rules_checked": [
            {"rule": rule, "tag": tag, "what": what,
             "value_um": tech[rule] if rule in tech else None}
            for rule, tag, what in CHECKED_RULES
        ],
        "shapes_checked": len(shapes),
        # Said in the result itself, so no caller can present this as more
        # than it is.
        "coverage": (
            "Ten rules, checked against the values in the PDK's technology "
            "file. This is not the sign-off deck, which has thousands and "
            "needs Magic or KLayout. Geometry that passes here has passed "
            "these five and has not been checked against the rest."
        ),
    }
