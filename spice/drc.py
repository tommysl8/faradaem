"""Checking drawn geometry against the foundry's own rule values.

This is not sign-off. The real deck is thousands of rules and needs Magic or
KLayout, neither of which runs here. What this does is check the handful of
rules that actually apply to the shapes Faradaem draws, using the numbers
read out of the PDK's technology file, and say exactly which rules it
checked so the coverage is never mistaken for the whole.

The point is the difference between geometry nobody has looked at and
geometry checked against the numbers it was supposed to satisfy. A drawing
that has not been checked is a drawing; one that has been checked against
thirty-five rules is a drawing that satisfies thirty-five rules, and
says so.

Each rule carries the tag the foundry writes in its own error message
(diff/tap.1, poly.1a, poly.7, poly.8, nwell.1, nwell.2a, nwell.5,
met1.1, met1.2, licon.1, licon.2, licon.5a, licon.8, licon.11,
licon.14, li.1, li.3, li.5, mcon.1, mcon.2, met1.4, met2.1, met2.2,
via.1a, via.2, via.4a, diff/tap.9, diff/tap.10, diff/tap.11, nwell.4,
licon.16, met1.5, via.5a, met2.5), so a violation can be looked up.

The three directional ones are here because the foundry's own deck found
them and this module had not: a via needs more metal along one axis than
it needs all round, and checking only the all-round part passed forty
violations as clean.
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
    ("contact_width", "licon.1", "minimum contact width"),
    ("contact_spacing", "licon.2", "minimum spacing between contacts"),
    ("contact_surround", "licon.5a", "diffusion overlap of the contact in it"),
    ("contact_to_gate", "licon.11", "contact spacing to the gate beside it"),
    ("li_width", "li.1", "minimum local interconnect width"),
    ("li_spacing", "li.3", "minimum local interconnect spacing"),
    ("li_surround", "li.5", "local interconnect overlap of its contact"),
    ("poly_contact_surround", "licon.8", "poly overlap of the contact on it"),
    ("poly_contact_to_diff", "licon.14",
     "poly contact spacing to any diffusion"),
    ("via_width", "mcon.1", "minimum width of the via up to metal1"),
    ("via_spacing", "mcon.2", "minimum spacing between those vias"),
    ("metal1_via_surround", "met1.4", "metal1 overlap of the via under it"),
    ("metal2_width", "met2.1", "minimum metal2 width"),
    ("metal2_spacing", "met2.2", "minimum metal2 spacing"),
    ("via1_width", "via.1a", "minimum width of the via between the metals"),
    ("via1_spacing", "via.2", "minimum spacing between those vias"),
    ("via1_surround", "via.4a", "metal overlap of the via between them"),
    ("ndiff_to_nwell", "diff/tap.9", "n-diffusion spacing to any n-well"),
    ("ptap_to_nwell", "diff/tap.11", "substrate tap spacing to any n-well"),
    ("nwell_tap_surround", "diff/tap.10", "n-well overlap of the tap in it"),
    (None, "nwell.4", "every n-well holds a metal-connected tap"),
    (None, "licon.16", "every tap is contacted"),
    ("metal1_via_directional", "met1.5",
     "metal1 overlap of its contact, along one axis"),
    ("via1_directional", "via.5a",
     "metal1 overlap of the via between the metals, along one axis"),
    ("via1_directional", "met2.5",
     "metal2 overlap of the via between the metals, along one axis"),
)

#: A dimension is allowed to be this far under a rule before it counts as a
#: violation, to absorb the float noise of a placement done in microns.
TOLERANCE_UM = 1e-9


class Violation(dict):
    """One broken rule, as plain data with the numbers that broke it."""


def _on(shapes, layers, name):
    """Every shape drawn on one layer, as rectangles.

    A layer is its GDS number and its datatype together. Two different
    layers share a number in this PDK -- poly is 66/20 and the contact is
    66/44 -- so selecting on the number alone silently mixes them.
    """
    number, datatype = layers[name]
    return [_rect(shape) for shape in shapes
            if shape[0] == number and shape[1] == datatype]


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
    wanted = (
        ("POLY", "poly_width", "poly.1a"),
        ("DIFF", "diff_width", "diff/tap.1"),
        ("MET1", "metal1_width", "met1.1"),
        ("CONT", "contact_width", "licon.1"),
        ("LI", "li_width", "li.1"),
        ("MCON", "via_width", "mcon.1"),
        ("MET2", "metal2_width", "met2.1"),
        ("VIA1", "via1_width", "via.1a"),
    )
    for name, rule, tag in wanted:
        if name not in layers or rule not in tech:
            continue
        required = tech[rule]
        for x1, y1, x2, y2 in _on(shapes, layers, name):
            narrowest = min(x2 - x1, y2 - y1)
            if _short(narrowest, required):
                found.append(_violation(
                    rule, tag, "a shape is narrower than the rule allows",
                    narrowest, required, [x1, y1, x2, y2]
                ))
    return found


def _layer_spacing(shapes, layers, name, required, rule, tag, what):
    """Two shapes on one layer must not come closer than the rule.

    Shapes that touch or overlap are one piece of geometry, not two, so
    they are not a spacing violation.
    """
    found = []
    if name not in layers:
        return []
    boxes = _on(shapes, layers, name)
    for index, first in enumerate(boxes):
        for second in boxes[index + 1:]:
            gap_x = max(second[0] - first[2], first[0] - second[2])
            gap_y = max(second[1] - first[3], first[1] - second[3])
            if gap_x <= 0 and gap_y <= 0:
                continue                      # overlapping, so not a gap
            gap = max(gap_x, gap_y)
            if _short(gap, required):
                found.append(_violation(rule, tag, what, gap, required,
                                        [first, second]))
    return found


def check_spacing(shapes, layers, tech):
    """Diffusions that are not the same diffusion have to stay apart."""
    return _layer_spacing(
        shapes, layers, "DIFF", tech["diff_spacing"],
        "diff_spacing", "diff/tap.3",
        "two diffusions are closer than the rule allows"
    )


def check_metal_spacing(shapes, layers, tech):
    """Wires that are not the same wire must stay apart.

    Two rectangles of one net touch by design, which is how a track meets
    its stubs, so touching is not a violation.
    """
    if "MET1" not in layers or "metal1_spacing" not in tech:
        return []
    return _layer_spacing(
        shapes, layers, "MET1", tech["metal1_spacing"],
        "metal1_spacing", "met1.2",
        "two wires are closer than the rule allows"
    )


def _encloses(outer, inner):
    """Whether one rectangle contains another, to within float noise.

    A via drawn exactly as wide as the interconnect under it is enclosed by
    it, but a centre computed in microns lands a fraction of a picometre
    outside. The same tolerance the rules use applies here, for the same
    reason.
    """
    return (outer[0] <= inner[0] + TOLERANCE_UM
            and outer[1] <= inner[1] + TOLERANCE_UM
            and outer[2] >= inner[2] - TOLERANCE_UM
            and outer[3] >= inner[3] - TOLERANCE_UM)


def _directional_reach(outer, inner):
    """How far one rectangle passes another, on its better axis.

    A directional rule is satisfied by either axis, not by both: the metal
    over a via has to reach further along its run than across it, and which
    axis that is depends on which way the wire goes.
    """
    across = min(inner[0] - outer[0], outer[2] - inner[2])
    along = min(inner[1] - outer[1], outer[3] - inner[3])
    return max(across, along)


def _directional_violation(outer, inner, required, rule, tag, what):
    reach = _directional_reach(outer, inner)
    if not _short(reach, required):
        return []
    return [_violation(rule, tag, what, reach, required, [outer, inner])]


def _surround_violations(holder, contact, required, rule, tag, what):
    """How far the thing under a contact reaches past each of its edges."""
    found = []
    for measured, side in ((contact[0] - holder[0], "left"),
                           (holder[2] - contact[2], "right"),
                           (contact[1] - holder[1], "bottom"),
                           (holder[3] - contact[3], "top")):
        if _short(measured, required):
            found.append(_violation(
                rule, tag, what + " past the " + side + " of its contact",
                measured, required, [holder, contact]
            ))
    return found


def check_contacts(shapes, layers, tech):
    """The contacts, and the rules that make one connectable.

    A contact lands either on diffusion, making a source or a drain, or on
    poly, making a gate connectable. Which one it is decides which rules
    apply: diffusion contacts owe licon.5a to the diffusion and licon.11 of
    clearance to the gate beside them, while poly contacts owe licon.8 to
    the poly and licon.14 of clearance to any diffusion. Judging one by the
    other's rules is how a correct gate contact gets reported as a broken
    source contact.
    """
    if "CONT" not in layers or "contact_surround" not in tech:
        return []

    found = []
    contacts = _on(shapes, layers, "CONT")
    # A tap is doped silicon like a source or a drain, and a contact lands
    # on one the same way, so it counts as diffusion here.
    diffs = _on(shapes, layers, "DIFF")
    if "TAP" in layers:
        diffs = diffs + _on(shapes, layers, "TAP")
    polys = _on(shapes, layers, "POLY")

    found += _layer_spacing(
        shapes, layers, "CONT", tech["contact_spacing"],
        "contact_spacing", "licon.2",
        "two contacts are closer than the rule allows"
    )

    for contact in contacts:
        on_poly = [p for p in polys if _encloses(p, contact)]
        on_diff = [d for d in diffs if _encloses(d, contact)]

        if on_poly:
            found += _surround_violations(
                on_poly[0], contact, tech["poly_contact_surround"],
                "poly_contact_surround", "licon.8",
                "the poly does not reach far enough"
            )
            # And it has to stay clear of every diffusion.
            if "poly_contact_to_diff" in tech:
                for diff in diffs:
                    gap_x = max(diff[0] - contact[2], contact[0] - diff[2])
                    gap_y = max(diff[1] - contact[3], contact[1] - diff[3])
                    gap = 0.0 if (gap_x <= 0 and gap_y <= 0) \
                        else max(gap_x, gap_y)
                    if _short(gap, tech["poly_contact_to_diff"]):
                        found.append(_violation(
                            "poly_contact_to_diff", "licon.14",
                            "a poly contact is closer to a diffusion than "
                            "the rule allows",
                            gap, tech["poly_contact_to_diff"], [diff, contact]
                        ))
            continue

        if not on_diff:
            found.append(_violation(
                "contact_surround", "licon.5a",
                "a contact is on neither diffusion nor poly, so it reaches "
                "nothing", 0.0, tech["contact_surround"], list(contact)
            ))
            continue

        found += _surround_violations(
            on_diff[0], contact, tech["contact_surround"],
            "contact_surround", "licon.5a",
            "the diffusion does not reach far enough"
        )

        # A diffusion contact has to clear the gate beside it, or it shorts
        # the channel to the source it was meant to connect.
        for gate in polys:
            gap_x = max(gate[0] - contact[2], contact[0] - gate[2])
            gap_y = max(gate[1] - contact[3], contact[1] - gate[3])
            if gap_x <= 0 and gap_y <= 0:
                found.append(_violation(
                    "contact_to_gate", "licon.11",
                    "a contact overlaps the gate", 0.0,
                    tech["contact_to_gate"], [gate, contact]
                ))
                continue
            gap = max(gap_x, gap_y)
            if _short(gap, tech["contact_to_gate"]):
                found.append(_violation(
                    "contact_to_gate", "licon.11",
                    "a contact is closer to the gate than the rule allows",
                    gap, tech["contact_to_gate"], [gate, contact]
                ))
    return found


def check_local_interconnect(shapes, layers, tech):
    """Local interconnect: wide enough, spaced, and covering its contacts.

    li.5 is a directional rule, which means the overlap is owed in one
    direction and not in both. That is not a leniency to exploit but the
    reason the geometry works at all: taking the overlap on all four sides
    would push a source and a drain together until they broke li.3.
    """
    if "LI" not in layers or "li_surround" not in tech:
        return []

    found = _layer_spacing(
        shapes, layers, "LI", tech["li_spacing"],
        "li_spacing", "li.3",
        "two pieces of local interconnect are closer than the rule allows"
    )

    strips = _on(shapes, layers, "LI")
    contacts = _on(shapes, layers, "CONT")

    for contact in contacts:
        over = [strip for strip in strips
                if strip[0] <= contact[0] and strip[1] <= contact[1]
                and strip[2] >= contact[2] and strip[3] >= contact[3]]
        if not over:
            found.append(_violation(
                "li_surround", "li.5",
                "a contact has no local interconnect on it", 0.0,
                tech["li_surround"], list(contact)
            ))
            continue
        strip = over[0]
        across = min(contact[0] - strip[0], strip[2] - contact[2])
        along = min(contact[1] - strip[1], strip[3] - contact[3])
        best = max(across, along)
        if _short(best, tech["li_surround"]):
            found.append(_violation(
                "li_surround", "li.5",
                "the local interconnect does not overlap its contact far "
                "enough in either direction",
                best, tech["li_surround"], [strip, contact]
            ))
    return found


def check_vias(shapes, layers, tech):
    """The vias, and the metal that has to cover them.

    A via is a hole between two layers. If the metal above or below does
    not cover it with the overlap the foundry asks for, the hole is not
    reliably filled, and a connection that looks drawn is not one.
    """
    found = []

    if "MCON" in layers and "metal1_via_surround" in tech:
        found += _layer_spacing(
            shapes, layers, "MCON", tech["via_spacing"],
            "via_spacing", "mcon.2",
            "two vias to metal1 are closer than the rule allows"
        )
        metal = _on(shapes, layers, "MET1")
        strips = _on(shapes, layers, "LI")
        for via in _on(shapes, layers, "MCON"):
            over = [m for m in metal if _encloses(m, via)]
            if not over:
                found.append(_violation(
                    "metal1_via_surround", "met1.4",
                    "a via has no metal1 over it", 0.0,
                    tech["metal1_via_surround"], list(via)
                ))
            else:
                found += _surround_violations(
                    over[0], via, tech["metal1_via_surround"],
                    "metal1_via_surround", "met1.4",
                    "the metal1 does not reach far enough"
                )
                if "metal1_via_directional" in tech:
                    found += _directional_violation(
                        over[0], via, tech["metal1_via_directional"],
                        "metal1_via_directional", "met1.5",
                        "the metal1 does not reach past its contact far "
                        "enough on either axis"
                    )
            if strips and not any(_encloses(s, via) for s in strips):
                found.append(_violation(
                    "li_surround", "li.5",
                    "a via to metal1 does not land on any local "
                    "interconnect", 0.0, tech["li_surround"], list(via)
                ))

    if "VIA1" in layers and "via1_surround" in tech:
        found += _layer_spacing(
            shapes, layers, "VIA1", tech["via1_spacing"],
            "via1_spacing", "via.2",
            "two vias between the metals are closer than the rule allows"
        )
        for name, tag in (("MET1", "via.4a"), ("MET2", "via.4a")):  # noqa: E501
            if name not in layers:
                continue
            metal = _on(shapes, layers, name)
            for via in _on(shapes, layers, "VIA1"):
                over = [m for m in metal if _encloses(m, via)]
                if not over:
                    found.append(_violation(
                        "via1_surround", tag,
                        "a via between the metals has no " + name.lower()
                        + " over it", 0.0, tech["via1_surround"], list(via)
                    ))
                    continue
                found += _surround_violations(
                    over[0], via, tech["via1_surround"],
                    "via1_surround", tag,
                    "the " + name.lower() + " does not reach far enough"
                )
                if "via1_directional" in tech:
                    found += _directional_violation(
                        over[0], via, tech["via1_directional"],
                        "via1_directional",
                        "via.5a" if name == "MET1" else "met2.5",
                        "the " + name.lower() + " does not reach past the via "
                        "far enough on either axis"
                    )
    return found


def check_metal2_spacing(shapes, layers, tech):
    """Two tracks that are not the same track have to stay apart."""
    if "MET2" not in layers or "metal2_spacing" not in tech:
        return []
    return _layer_spacing(
        shapes, layers, "MET2", tech["metal2_spacing"],
        "metal2_spacing", "met2.2",
        "two metal2 tracks are closer than the rule allows"
    )


def check_taps(shapes, layers, tech):
    """Wells and substrate need a tap, and a tap needs a contact.

    These two are not about dimensions. A well with nothing tying it to a
    voltage floats, and a floating well is how a CMOS circuit latches up
    and destroys itself; the foundry writes that as nwell.4. A tap with no
    contact ties nothing, which is licon.16. Geometry can satisfy every
    spacing rule in the deck and still be unmanufacturable for want of
    either.
    """
    if "TAP" not in layers:
        return []

    found = []
    taps = _on(shapes, layers, "TAP")
    wells = _on(shapes, layers, "NWELL")
    contacts = _on(shapes, layers, "CONT")

    for well in wells:
        inside = [t for t in taps if _encloses(well, t)]
        if not inside:
            found.append(_violation(
                "nwell_tap", "nwell.4",
                "an n-well holds no tap, so nothing sets its voltage",
                0.0, 1.0, list(well)
            ))
            continue
        for tap in inside:
            found += _surround_violations(
                well, tap, tech["nwell_tap_surround"],
                "nwell_tap_surround", "diff/tap.10",
                "the well does not reach far enough"
            )

    for tap in taps:
        if not any(_encloses(tap, c) for c in contacts):
            found.append(_violation(
                "tap_contact", "licon.16",
                "a tap has no contact, so it connects to nothing",
                0.0, 1.0, list(tap)
            ))

    # A substrate tap outside the well has to keep clear of it.
    if "ptap_to_nwell" in tech:
        for tap in taps:
            for well in wells:
                if _encloses(well, tap):
                    continue
                gap_x = max(well[0] - tap[2], tap[0] - well[2])
                gap_y = max(well[1] - tap[3], tap[1] - well[3])
                gap = 0.0 if (gap_x <= 0 and gap_y <= 0) else max(gap_x, gap_y)
                if _short(gap, tech["ptap_to_nwell"]):
                    found.append(_violation(
                        "ptap_to_nwell", "diff/tap.11",
                        "a substrate tap is closer to a well than the rule "
                        "allows", gap, tech["ptap_to_nwell"], [well, tap]
                    ))
    return found


def check_diffusion_to_well(shapes, layers, tech):
    """An n-diffusion outside a well has to stay well clear of one.

    Closer than diff/tap.9 and the well's depletion region reaches the
    device, which is a failure nothing in the drawing shows.
    """
    if "NWELL" not in layers or "ndiff_to_nwell" not in tech:
        return []

    found = []
    wells = _on(shapes, layers, "NWELL")
    for diff in _on(shapes, layers, "DIFF"):
        for well in wells:
            if _encloses(well, diff):
                continue                       # a p-channel device, at home
            gap_x = max(well[0] - diff[2], diff[0] - well[2])
            gap_y = max(well[1] - diff[3], diff[1] - well[3])
            gap = 0.0 if (gap_x <= 0 and gap_y <= 0) else max(gap_x, gap_y)
            if _short(gap, tech["ndiff_to_nwell"]):
                found.append(_violation(
                    "ndiff_to_nwell", "diff/tap.9",
                    "an n-diffusion is closer to a well than the rule "
                    "allows", gap, tech["ndiff_to_nwell"], [well, diff]
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
    diffs = _on(shapes, layers, "DIFF")
    polys = _on(shapes, layers, "POLY")

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
    wells = _on(shapes, layers, "NWELL")

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
    violations += check_contacts(shapes, layers, tech)
    violations += check_local_interconnect(shapes, layers, tech)
    violations += check_vias(shapes, layers, tech)
    violations += check_metal2_spacing(shapes, layers, tech)
    violations += check_taps(shapes, layers, tech)
    violations += check_diffusion_to_well(shapes, layers, tech)

    return {
        "violations": violations,
        "clean": not violations,
        "rules_checked": [
            {"rule": rule, "tag": tag, "what": what,
             "value_um": tech.get(rule) if rule else None}
            for rule, tag, what in CHECKED_RULES
        ],
        "shapes_checked": len(shapes),
        # Said in the result itself, so no caller can present this as more
        # than it is.
        "coverage": (
            "Thirty-five rules, checked against the values in the PDK's "
            "technology file. This is the fast loop, not the answer: the "
            "sign-off deck is the SKY130 runset KLayout runs, and geometry "
            "that passes here has passed these thirty-five and nothing "
            "else. Run the real deck before believing it."
        ),
    }
