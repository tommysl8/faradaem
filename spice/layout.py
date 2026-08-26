"""Floorplan and parasitics, read out of the PDK rather than invented.

What a schematic leaves out is that the circuit has to occupy silicon, and
that the wires joining its devices are themselves capacitors. This module
answers both questions to first order, and it answers them with the
foundry's own numbers: every dimension and every capacitance below is read
out of the SKY130 Magic technology file at run time, with the rule that
produced it named beside it. Nothing here is a remembered constant.

What this is:

    a single-finger floorplan, devices in a row at the minimum diffusion
    spacing, each drawn as a real stack: diffusion, the poly gate, the
    implant that declares the diffusion n-type or p-type, a column of
    contacts down the source and the drain, and the local interconnect
    those contacts land on. The p-channel devices are grouped into one
    n-well. Each net is routed as metal on its own track, and the
    interconnect capacitance is taken from that drawn metal using the
    layers' own capacitance per unit area and per unit edge.

What this is not, and the difference matters:

    a layout that has been extracted or verified. The placer is a row
    and the router is one track per net, neither of which a person would
    call placement or routing. Nothing here has confirmed that the drawing is the circuit it
    claims to be, which is what layout versus schematic is for, and the
    capacitance is what the drawn metal implies rather than what a field
    solver measured. The well and substrate taps are not drawn yet, and
    without them the geometry is not manufacturable however many rules it
    passes. Those need Magic or Netgen, which do not run on this machine.
    Everything above is labelled that way everywhere it appears.

The value of it is still real: it says how much silicon a sizing costs,
and it lets the same measurement run again with the interconnect loading
the circuit, which is the first honest answer to "does this still meet
spec once it is built".
"""

import os
import re

from . import gds, runner

#: The DRC section states its dimensions in nanometres. The scalefactor
#: lines in this file belong to the GDS output styles and do not apply
#: here, so the unit was confirmed against four independent rules whose
#: published SKY130 minimums are known: poly width 150 (0.15 um), licon
#: width 170 (0.17), diffusion overhang 250 (0.25), metal1 width 140
#: (0.14). All four agree at one unit to the nanometre.
DRC_UNIT_UM = 0.001

#: The rules a single-finger device footprint needs, by the tag the PDK
#: writes in its own error message. Keeping the tag means the number can
#: always be traced back to the line it came from.
RULE_TAGS = {
    "poly_width": r"width\s+allpoly\S*\s+(\d+)\s+\"poly width",
    "diff_overhang": r"overhang\s+\*ndiff\S*\s+\S+\s+(\d+)\s+\"N-Diffusion overhang",
    "diff_spacing": r"spacing\s+alldifflv\S*\s+\S+\s+(\d+)\s+touching_illegal",
    "contact_width": r"width\s+ndc/li\s+(\d+)\s+\"N-diffusion contact",
    "metal1_width": r"width\s+\*m1\S*\s+(\d+)\s+\"Metal1 width",
    # The rule that says a gate must not stop at the edge of its diffusion.
    "poly_endcap": r"overhang\s+\*poly\s+allfetsstd\S*\s+(\d+)\s+\"poly overhang",
    # Diffusion cannot be drawn thinner than this anywhere.
    "diff_width": r"width\s+\*ndiff\S*[^\n]*\n\s*(\d+)\s+\"Diffusion width",
    # An n-well has to surround the p-diffusion it holds, and two wells
    # have to stay apart unless they are the same well.
    "nwell_surround": r"surround\s+\*pdiff\S*[^\n]*\s+(\d+)\s+absence_illegal",
    "nwell_width": r"width\s+allnwell\s+(\d+)\s+\"N-well width",
    "nwell_spacing": r"spacing\s+allnwell\s+allnwell\s+(\d+)\s+touching_ok",
    "metal1_spacing": r"spacing\s+allm1\S*\s+\S+\s+(\d+)\s+touching_ok\s+\"Metal1 spacing",
    # A contact has a width, a spacing, and has to be surrounded by the
    # diffusion it lands on and cleared of the gate beside it.
    "contact_spacing": r"spacing\s+allndiffcont\s+allndiffcont\s+(\d+)\s+touching_ok",
    "contact_surround": r"surround\s+ndc/a\s+\S+\s+(\d+)\s+absence_illegal",
    "contact_to_gate": r"spacing\s+ndc,pdc\s+nfet,\S+\s+(\d+)\s+touching_illegal",
    # Local interconnect: what a contact lands on before metal.
    "li_width": r"width\s+\*li\s+(\d+)\s+\"Local interconnect width",
    "li_spacing": r"spacing\s+\*locali,rli\s+\S+\s+(\d+)\s+touching_ok",
    "li_surround": r"surround\s+ndc/li,[^\n]*\n\s*\*li,rli,coreli\s+(\d+)\s+directional",
    # The implant that declares a diffusion n-type or p-type. Magic derives
    # it by growing the diffusion, and states the amount in its own output
    # style, so that is where it is read from.
    "implant_surround": r"layer\s+PSDM\s+basePSDM,extendPSDM\s*\n\s*grow\s+(\d+)",
    # A contact on poly, which is the only way to reach a gate.
    "poly_contact_surround": r"surround\s+pc/a\s+\*poly,\S+\s+(\d+)\s+absence_illegal",
    "poly_contact_to_diff": r"spacing\s+pc\s+alldiff\s+(\d+)\s+touching_illegal",
    "npc_surround": r"layer\s+NPC\s+pc\s*\n(?:[^\n]*\n)*?\s*grow\s+(\d+)",
    # The via from local interconnect up to metal1.
    "via_width": r"width\s+mcon/m1\s+(\d+)\s+\"mcon\.width",
    "via_spacing": r"spacing\s+mcon/m1\s+\S+\s+(\d+)\s+touching_ok",
    "metal1_via_surround": r"surround\s+mcon/m1\s+\*met1\s+(\d+)\s+absence_illegal",
    # The second metal layer, and the via between the two.
    "metal2_width": r"width\s+allm2,m2fill\s+(\d+)\s+\"Metal2 width",
    "metal2_spacing": r"spacing\s+allm2\s+\S+\s+(\d+)\s+touching_ok\s+\"Metal2 spacing",
    # Magic states a cut layer as a grid: border, size, spacing. Reading it
    # here is how the drawn cut ends up the size the foundry makes.
    "via1_surround": r"layer\s+VIA1\s+via1\s*\n\s*squares-grid\s+(\d+)\s+\d+\s+\d+",
    "via1_width": r"layer\s+VIA1\s+via1\s*\n\s*squares-grid\s+\d+\s+(\d+)\s+\d+",
    "via1_spacing": r"layer\s+VIA1\s+via1\s*\n\s*squares-grid\s+\d+\s+\d+\s+(\d+)",
    # Taps, and the room a well needs around everything near it.
    "ndiff_to_nwell": r"spacing\s+\*ndiff,\*ndiode,nfet\s+allnwell\s+(\d+)\s+touching_illegal",
    "ptap_to_nwell": r"spacing\s+\*psd\s+allnwell\s+(\d+)\s+touching_illegal",
    "nwell_tap_surround": r"surround\s+\*nsd\s+allnwell\s+(\d+)\s+absence_illegal",
    # A via needs more metal over it along one axis than it needs all
    # round. Magic states the extra as a separate `directional` line, and
    # the sign-off deck fails geometry that has only the all-round part.
    "metal1_via_directional": r"surround\s+mcon/m1\s+\*met1\s+(\d+)\s+directional",
    "via1_directional_extra": r"surround\s+v1/m1\s+\*m1,rm1\s+(\d+)\s+directional",
}

#: Capacitance per unit area and per unit edge, in attofarads. Magic writes
#: them as aF/um2 and aF/um respectively.
CAP_PATTERNS = {
    "metal1_area": r"defaultareacap\s+allm1\s+metal1\s+([\d.]+)",
    "metal1_edge": r"defaultsidewall\s+allm1\s+metal1\s+([\d.]+)",
    "li_area": r"defaultareacap\s+allli\s+locali\s+([\d.]+)",
    "li_edge": r"defaultsidewall\s+allli\s+locali\s+([\d.]+)",
    "poly_area": r"defaultareacap\s+\*poly\s+active\s+([\d.]+)",
}

_ATTO = 1e-18

#: The GDS layer and datatype of each drawn layer, by the name the Magic
#: technology file gives it in its gdsii output style. Read, never recalled:
#: a wrong number here produces a file that looks right and means nothing.
GDS_LAYER_NAMES = ("DIFF", "POLY", "NWELL", "LI", "MET1",
                   "CONT", "MCON", "NSDM", "PSDM", "TAP", "NPC",
                   "MET2", "VIA1")


class LayoutDataError(RuntimeError):
    """Raised when the technology file cannot answer what was asked of it."""


def tech_path():
    """The Magic technology file inside the installed PDK."""
    return os.path.join(runner.pdk_root(), "sky130A", "libs.tech", "magic",
                        "sky130A.tech")


def tech_available():
    """True when the technology file is there to be read."""
    return os.path.isfile(tech_path())


def _read_tech():
    path = tech_path()
    if not os.path.isfile(path):
        raise LayoutDataError(
            "The SKY130 technology file is not at " + path + ". The floorplan "
            "reads every dimension out of it, so without it there is nothing "
            "to compute from. Install the PDK and set PDK_ROOT."
        )
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _first(text, pattern, label):
    found = re.search(pattern, text, re.M)
    if not found:
        raise LayoutDataError(
            "The technology file has no rule matching " + repr(label) + ". "
            "Faradaem will not guess a dimension the foundry did not state."
        )
    return found.group(1)


def tech_constants():
    """Every number this module uses, in microns and farads, from the PDK.

    Returned as a plain dict so a caller can show its work: each value is
    traceable to one line of the technology file.
    """
    text = _read_tech()

    rules = {}
    for name, pattern in RULE_TAGS.items():
        rules[name] = int(_first(text, pattern, name)) * DRC_UNIT_UM

    caps = {}
    for name, pattern in CAP_PATTERNS.items():
        caps[name] = float(_first(text, pattern, name)) * _ATTO

    rules.update(caps)

    # via.5a and met2.5 are stated in the technology file as the extra
    # beyond via.4a, not as totals, so the total a checker actually needs
    # is assembled here rather than remembered anywhere.
    if "via1_surround" in rules and "via1_directional_extra" in rules:
        rules["via1_directional"] = (rules["via1_surround"]
                                     + rules["via1_directional_extra"])
    return rules


def gds_layers():
    """Layer and datatype numbers for the drawn layers, from the PDK.

    The technology file states them in its gdsii output style as calma
    records, one per layer block.
    """
    text = _read_tech()
    try:
        section = text[text.index("style gdsii"):]
    except ValueError:
        raise LayoutDataError(
            "The technology file has no gdsii output style, so the layer "
            "numbers cannot be read from it."
        ) from None

    layers = {}
    for name in GDS_LAYER_NAMES:
        pattern = (r"^\s*layer\s+" + name + r"\b[^\n]*\n"
                   r"(?:(?!^\s*layer\s)[^\n]*\n)*?\s*calma\s+(\d+)\s+(\d+)")
        found = re.search(pattern, section, re.M)
        if not found:
            raise LayoutDataError(
                "The technology file states no GDS number for layer "
                + repr(name) + "."
            )
        layers[name] = (int(found.group(1)), int(found.group(2)))
    return layers


def group_gap(tech):
    """How far an n-channel device has to sit from the p-channel group.

    Not the diffusion spacing. The p-channel devices are in an n-well, the
    well reaches nwell.5 past them, and an n-diffusion has to stay
    diff/tap.9 clear of any n-well. Those two in a row are what separates
    the groups, and they come to about twice the plain diffusion spacing.
    """
    return tech["nwell_surround"] + tech["ndiff_to_nwell"]


def tap_height(tech):
    """A tap has to be tall enough to hold the contact it must have."""
    return tech["contact_width"] + 2.0 * tech["contact_surround"]


def contact_row(y_bottom, x1, x2, tech):
    """A row of contacts along a tap, as many as legally fit."""
    size = tech["contact_width"]
    gap = tech["contact_spacing"]
    usable = (x2 - x1) - 2.0 * tech["contact_surround"]
    if usable < size - 1e-9:
        return []
    count = max(int((usable + gap + 1e-9) / (size + gap)), 1)
    span = count * size + (count - 1) * gap
    start = x1 + ((x2 - x1) - span) / 2.0
    return [(start + index * (size + gap), y_bottom,
             start + index * (size + gap) + size, y_bottom + size)
            for index in range(count)]


def gate_contact_offset(tech):
    """How far above the diffusion a gate contact has to sit.

    Two rules compete. licon.14 keeps a poly contact clear of any
    diffusion. The one that actually binds is li.3: the interconnect over
    the source already reaches li.5 above the top contact, which itself
    sits licon.5a inside the diffusion, and the gate's own interconnect
    reaches li.5 below its contact. Stack those and the gate contact has to
    start further up than licon.14 alone would ask.
    """
    from_li = (tech["li_surround"] - tech["contact_surround"]
               + tech["li_spacing"] + tech["li_surround"])
    return max(tech["poly_contact_to_diff"], from_li)


def gate_stack_height(tech):
    """How far the whole gate terminal reaches above the diffusion."""
    return (gate_contact_offset(tech) + tech["contact_width"]
            + max(tech["li_surround"], tech["npc_surround"]))


def via_pad(tech):
    """How wide metal has to be where a via lands on it.

    Metal1 carries two different vias: mcon down to the local interconnect,
    which owes met1.4 on every side, and via1 up to metal2, which owes
    via.4a. The two do not ask for the same thing, and metal drawn to
    satisfy the smaller of them fails the larger. The pad is whichever is
    wider, which is why a track cannot simply be drawn at minimum width.
    """
    for_mcon = tech["via_width"] + 2.0 * tech["metal1_via_surround"]
    for_via1 = tech["via1_width"] + 2.0 * tech["via1_surround"]
    return max(for_mcon, for_via1)


def terminal_pitch_minimum(tech):
    """The narrowest device that can still carry its own three connections.

    Source, gate and drain each need a via pad, and the pads have to keep
    met1.2 apart. A device narrower than this cannot be wired on one metal
    layer however legal its transistor is, so the footprint takes this as a
    floor rather than drawing something unroutable.
    """
    edge = tech["contact_surround"] + tech["contact_width"] / 2.0
    return 2.0 * (edge + via_pad(tech) + tech["metal1_spacing"])


def terminal_boxes(device, tech):
    """Where each terminal of a device is, as a rectangle in microns.

    device_shapes draws the local interconnect at exactly these places, and
    the router aims at them. Computing it once here is what keeps the
    drawing and the routing from disagreeing.
    """
    x1 = device["x"]
    x2 = device["x"] + device["width"]
    y1 = device["y"]
    y2 = device["y"] + device["height"]

    size = tech["contact_width"]
    inset = tech["contact_surround"]
    reach = tech["li_surround"]

    boxes = {}
    column = contact_column(x1 + inset, y1, y2, tech)
    if column:
        boxes["source"] = (x1 + inset, column[0][1] - reach,
                           x1 + inset + size, column[-1][3] + reach)
        left = x2 - inset - size
        boxes["drain"] = (left, column[0][1] - reach,
                          left + size, column[-1][3] + reach)

    overhang = (device["width"] - device["gate_length"]) / 2.0
    gate_mid = x1 + overhang + device["gate_length"] / 2.0
    contact_y1 = y2 + gate_contact_offset(tech)
    boxes["gate"] = (gate_mid - size / 2.0, contact_y1 - reach,
                     gate_mid + size / 2.0, contact_y1 + size + reach)
    return boxes


def device_shapes(device, layers, tech):
    """One device as drawn, and the three terminals a wire can land on.

    Returns (shapes, terminals). A terminal is the rectangle of local
    interconnect a router should aim at, which is what makes the drawing
    connectable rather than merely recognisable.

    The gate is the interesting one. Poly cannot be contacted over the
    channel, so the gate stripe runs up past the diffusion into a wider
    landing pad, and the contact lands there. That pad, and the clearance
    under it, is why a real cell is taller than its transistor.
    """
    x1 = device["x"]
    x2 = device["x"] + device["width"]
    y1 = device["y"]
    y2 = device["y"] + device["height"]

    shapes = [(layers["DIFF"][0], layers["DIFF"][1], x1, y1, x2, y2)]
    terminals = {}

    overhang = (device["width"] - device["gate_length"]) / 2.0
    gate_x1 = x1 + overhang
    gate_x2 = gate_x1 + device["gate_length"]
    gate_mid = (gate_x1 + gate_x2) / 2.0

    if "CONT" not in layers or "LI" not in layers:
        endcap = tech["poly_endcap"]
        shapes.append((layers["POLY"][0], layers["POLY"][1],
                       gate_x1, y1 - endcap, gate_x2, y2 + endcap))
        return shapes, terminals

    cont = layers["CONT"]
    li = layers["LI"]
    size = tech["contact_width"]
    inset = tech["contact_surround"]
    boxes = terminal_boxes(device, tech)

    # Source and drain: a column of contacts each, with li over it. The
    # overlap is taken along the column only, because li.5 is directional
    # and taking it on all four sides would push the two together until
    # they broke li.3 at minimum gate length.
    for name, left in (("source", x1 + inset),
                       ("drain", x2 - inset - size)):
        column = contact_column(left, y1, y2, tech)
        if not column or name not in boxes:
            continue
        for box in column:
            shapes.append((cont[0], cont[1], box[0], box[1], box[2], box[3]))
        strip = boxes[name]
        shapes.append((li[0], li[1]) + strip)
        terminals[name] = strip

    # The gate: a stripe up out of the channel into a pad, and a contact
    # on the pad rather than over the channel, which is not allowed.
    pad_surround = tech["poly_contact_surround"]
    contact_y1 = y2 + gate_contact_offset(tech)
    contact_y2 = contact_y1 + size
    pad_x1 = gate_mid - size / 2.0 - pad_surround
    pad_x2 = gate_mid + size / 2.0 + pad_surround
    pad_y1 = contact_y1 - pad_surround

    endcap = tech["poly_endcap"]
    shapes.append((layers["POLY"][0], layers["POLY"][1],
                   gate_x1, y1 - endcap, gate_x2, pad_y1))
    shapes.append((layers["POLY"][0], layers["POLY"][1],
                   pad_x1, pad_y1, pad_x2, contact_y2 + pad_surround))
    shapes.append((cont[0], cont[1], gate_mid - size / 2.0, contact_y1,
                   gate_mid + size / 2.0, contact_y2))

    shapes.append((li[0], li[1]) + boxes["gate"])
    terminals["gate"] = boxes["gate"]

    # NPC: the cut the foundry needs around a contact on poly. Magic grows
    # it from the contact, so that is what happens here.
    if "NPC" in layers:
        grow = tech["npc_surround"]
        shapes.append((layers["NPC"][0], layers["NPC"][1],
                       gate_mid - size / 2.0 - grow, contact_y1 - grow,
                       gate_mid + size / 2.0 + grow, contact_y2 + grow))

    return shapes, terminals


def floorplan_shapes(plan, layers, tech):
    """The floorplan as rectangles on real layers, in microns.

    Each device is drawn by device_shapes, which also reports where its
    terminals are. Around them go the n-well the p-channel devices share
    and the implants that say which diffusion is which.
    """
    shapes = []
    for well in plan.get("wells", []):
        shapes.append((layers["NWELL"][0], layers["NWELL"][1],
                       well["x1"], well["y1"], well["x2"], well["y2"]))

    for implant in plan.get("implants", []):
        if implant["layer"] not in layers:
            continue
        number = layers[implant["layer"]]
        shapes.append((number[0], number[1], implant["x1"], implant["y1"],
                       implant["x2"], implant["y2"]))

    for device in plan["devices"]:
        drawn, terminals = device_shapes(device, layers, tech)
        shapes.extend(drawn)
        device["terminals"] = terminals

    for tap in plan.get("taps", []):
        shapes.extend(tap_shapes(tap, layers, tech))
        tap["terminal"] = tap_terminal(tap, tech)

    return shapes


def tap_terminal(tap, tech):
    """The local interconnect on a tap, which is what ties it to a rail."""
    row = contact_row(tap["y1"] + tech["contact_surround"],
                      tap["x1"], tap["x2"], tech)
    if not row:
        return None
    reach = tech["li_surround"]
    return (row[0][0] - reach, row[0][1],
            row[-1][2] + reach, row[-1][3])


def tap_shapes(tap, layers, tech):
    """A tap as drawn: the doped strip, its implant, and its contacts.

    A tap that is not contacted is not a tap, which the foundry states as
    licon.16, so the contacts are not optional decoration.
    """
    if "TAP" not in layers:
        return []
    number = layers["TAP"]
    shapes = [(number[0], number[1],
               tap["x1"], tap["y1"], tap["x2"], tap["y2"])]

    if "CONT" not in layers or "LI" not in layers:
        return shapes

    row = contact_row(tap["y1"] + tech["contact_surround"],
                      tap["x1"], tap["x2"], tech)
    for box in row:
        shapes.append((layers["CONT"][0], layers["CONT"][1],
                       box[0], box[1], box[2], box[3]))
    strip = tap_terminal(tap, tech)
    if strip is not None:
        shapes.append((layers["LI"][0], layers["LI"][1]) + strip)
    return shapes


def floorplan_gds(plan, name="FARADAEM_FLOORPLAN", when=None):
    """The floorplan as a GDSII stream, ready to open in a layout tool."""
    return gds.library(name, name,
                       floorplan_shapes(plan, gds_layers(), tech_constants()),
                       when=when)


def source_drain_overhang(tech):
    """How much diffusion a source or a drain actually needs.

    poly.7 says how far the diffusion must extend past the gate. It is not
    the whole answer, because that diffusion has to be contacted, and the
    contact has its own three rules: it must clear the gate (licon.11), it
    has its own width (licon.1), and the diffusion has to surround it
    (licon.5a). Those three in a row come to more than poly.7 alone, so a
    device drawn to poly.7 is a device nothing can connect to.

    The larger of the two is the honest number.
    """
    contactable = (tech["contact_to_gate"] + tech["contact_width"]
                   + tech["contact_surround"])
    return max(tech["diff_overhang"], contactable)


def contact_pitch_minimum(tech):
    """The least device width that still leaves room for one contact."""
    return tech["contact_width"] + 2.0 * tech["contact_surround"]


def contact_column(x_left, y1, y2, tech):
    """A row of contacts up one source or drain, as many as legally fit.

    One contact carries limited current, so real layouts fill the diffusion
    with as many as the spacing rule allows. The count comes from the rules
    rather than from a preference: each contact is licon.1 wide, neighbours
    stay licon.2 apart, and the diffusion has to surround the outermost by
    licon.5a at each end.
    """
    size = tech["contact_width"]
    gap = tech["contact_spacing"]
    usable = (y2 - y1) - 2.0 * tech["contact_surround"]
    if usable < size - 1e-9:
        return []

    count = int((usable + gap + 1e-9) / (size + gap))
    count = max(count, 1)

    span = count * size + (count - 1) * gap
    start = y1 + ((y2 - y1) - span) / 2.0
    return [(x_left, start + index * (size + gap),
             x_left + size, start + index * (size + gap) + size)
            for index in range(count)]


def device_footprint(width_m, length_m, tech):
    """One single-finger transistor, in microns.

    Along the channel the cell is the gate plus the diffusion that has to
    overhang it on both sides, which is what the poly.7 rule sets. Across
    the channel it is the drawn device width. Fingers are not folded here:
    a wide device is drawn wide, which is the pessimistic reading and the
    honest one for a first-order area.
    """
    width_um = width_m * 1e6
    length_um = length_m * 1e6
    overhang = source_drain_overhang(tech)
    return {
        "along": max(length_um + 2.0 * overhang,
                     terminal_pitch_minimum(tech)),
        "across": max(width_um, contact_pitch_minimum(tech)),
        "gate_length": length_um,
        "device_width": width_um,
        "overhang": overhang,
    }


def floorplan(devices, tech):
    """Place devices in one row at the minimum diffusion spacing.

    devices is a list of (name, width_m, length_m). The result carries every
    placed rectangle so the drawing and the area agree by construction: the
    picture is the thing that was measured, not an illustration of it.
    """
    if not devices:
        raise LayoutDataError("A floorplan needs at least one device.")

    placed = []
    cursor = 0.0
    for entry in devices:
        name, width_m, length_m = entry[0], entry[1], entry[2]
        kind = entry[3] if len(entry) > 3 else "nfet"
        cell = device_footprint(width_m, length_m, tech)
        placed.append({
            "name": name,
            "kind": kind,
            "x": cursor,
            "y": 0.0,
            "width": cell["along"],
            "height": cell["across"],
            "gate_length": cell["gate_length"],
            "device_width": cell["device_width"],
        })
        placed[-1]["terminals"] = terminal_boxes(placed[-1], tech)
        cursor += cell["along"] + tech["diff_spacing"]

    # Where the row changes from n-channel to p-channel, the plain
    # diffusion spacing is not enough: the well has to fit between them.
    for index in range(1, len(placed)):
        if placed[index - 1]["kind"] == "nfet" and placed[index]["kind"] == "pfet":
            extra = group_gap(tech) - tech["diff_spacing"]
            if extra > 0:
                for item in placed[index:]:
                    item["x"] += extra
                    item["terminals"] = terminal_boxes(item, tech)
                cursor += extra
            break

    span = cursor - tech["diff_spacing"]
    tallest = max(item["height"] for item in placed)

    # Every well needs a tap, or it floats, and a floating well is how a
    # CMOS circuit latches up. The substrate needs one for the same reason.
    # They sit in a row below the devices, contacted, so they can be tied.
    # The taps sit above the row, past the gate stack, and not below it.
    # Below, a wire from a tap to its track would have to run the whole
    # height of the row, and a metal1 wire that long picks up every via it
    # passes on the way, shorting the tap into net after net.
    taps = []
    height = tap_height(tech)
    above = max(item["y"] + item["height"] for item in placed)         + gate_stack_height(tech) + tech["diff_spacing"]
    for kind, members in (("ntap", [i for i in placed if i["kind"] == "pfet"]),
                          ("ptap", [i for i in placed if i["kind"] == "nfet"])):
        if not members:
            continue
        left = min(item["x"] for item in members)
        right = max(item["x"] + item["width"] for item in members)
        # The tap needs a stretch of its own to be wired at. Between two
        # device pins there is only ever half their pitch, which is less
        # than a via pad and its spacing, so the tap reaches out past its
        # group instead -- away from the other group, so the well and the
        # substrate tap do not close on each other.
        reach = 1.5 * via_pad(tech) + tech["metal1_spacing"]
        if kind == "ntap":
            right += reach
            landing = right - via_pad(tech) / 2.0
        else:
            left -= reach
            landing = left + via_pad(tech) / 2.0
        bottom = above
        taps.append({
            "landing": landing,
            "kind": kind,
            "implant": "NSDM" if kind == "ntap" else "PSDM",
            "x1": left, "y1": bottom, "x2": right, "y2": bottom + height,
            "serves": [item["name"] for item in members],
        })
        taps[-1]["terminal"] = tap_terminal(taps[-1], tech)

    wells = []
    pmos = [item for item in placed if item["kind"] == "pfet"]
    if pmos:
        # The well has to hold the p-channel devices and the n-tap that
        # biases it, and reach past both.
        held = [(i["x"], i["y"], i["x"] + i["width"], i["y"] + i["height"])
                for i in pmos]
        held += [(t["x1"], t["y1"], t["x2"], t["y2"])
                 for t in taps if t["kind"] == "ntap"]
        margin = max(tech["nwell_surround"], tech["nwell_tap_surround"])
        left = min(box[0] for box in held) - margin
        right = max(box[2] for box in held) + margin
        bottom = min(box[1] for box in held) - margin
        top = max(box[3] for box in held) + margin
        if right - left < tech["nwell_width"]:
            centre = (left + right) / 2.0
            left = centre - tech["nwell_width"] / 2.0
            right = centre + tech["nwell_width"] / 2.0
        if top - bottom < tech["nwell_width"]:
            centre = (bottom + top) / 2.0
            bottom = centre - tech["nwell_width"] / 2.0
            top = centre + tech["nwell_width"] / 2.0
        wells.append({"x1": left, "y1": bottom, "x2": right, "y2": top,
                      "holds": [item["name"] for item in pmos]})

    # The implant that says what type each diffusion is. Without it the
    # drawing does not distinguish an n-channel device from a p-channel one,
    # and the two are not the same device. Magic derives the implant by
    # growing the diffusion, so that is what happens here, with the amount
    # read out of its own output style.
    implants = []
    grow = tech.get("implant_surround")
    groups = [("nfet", "NSDM"), ("pfet", "PSDM")]
    boxes = {}
    for kind, layer in (groups if grow is not None else []):
        members = [item for item in placed if item["kind"] == kind]
        if not members:
            continue
        covered = [(i["x"], i["y"], i["x"] + i["width"],
                    i["y"] + i["height"]) for i in members]
        # A tap is doped the same way as the devices it biases against, so
        # it sits under the same implant.
        covered += [(t["x1"], t["y1"], t["x2"], t["y2"]) for t in taps
                    if t["implant"] == layer]
        boxes[kind] = {
            "layer": layer,
            "kind": kind,
            "x1": min(box[0] for box in covered) - grow,
            "y1": min(box[1] for box in covered) - grow,
            "x2": max(box[2] for box in covered) + grow,
            "y2": max(box[3] for box in covered) + grow,
            "holds": [item["name"] for item in members],
        }

    # Grown far enough, the two implants would overlap, and a diffusion
    # cannot be both n-type and p-type. They meet at a boundary instead,
    # which is what Magic's own and-not does. The devices are ordered
    # n-channel first for exactly this reason, so there is one boundary.
    if "nfet" in boxes and "pfet" in boxes:
        n_edge = max(item["x"] + item["width"]
                     for item in placed if item["kind"] == "nfet")
        p_edge = min(item["x"] for item in placed if item["kind"] == "pfet")
        middle = (n_edge + p_edge) / 2.0
        boxes["nfet"]["x2"] = min(boxes["nfet"]["x2"], middle)
        boxes["pfet"]["x1"] = max(boxes["pfet"]["x1"], middle)

    implants = [boxes[kind] for kind, _ in groups if kind in boxes]

    return {
        "devices": placed,
        "wells": wells,
        "taps": taps,
        "implants": implants,
        "width_um": span,
        "height_um": tallest,
        "area_um2": span * tallest,
        "active_area_um2": sum(item["width"] * item["height"] for item in placed),
        "spacing_um": tech["diff_spacing"],
    }


def clear_landing(strip, occupied, pad, tech):
    """Where along a tap a wire can come down without hitting a pin.

    A tap spans its whole group, and the pins of that group stand at
    known places along it. Landing anywhere is not safe: the middle of a
    three-device group is exactly where the middle device's gate is. This
    picks the point furthest from any of them.
    """
    low = strip[0] + pad / 2.0
    high = strip[2] - pad / 2.0
    if high <= low:
        return (strip[0] + strip[2]) / 2.0
    if not occupied:
        return (low + high) / 2.0

    candidates = [low, high]
    for first, second in zip(occupied, occupied[1:]):
        middle = (first + second) / 2.0
        if low <= middle <= high:
            candidates.append(middle)

    def clearance(value):
        return min(abs(value - other) for other in occupied)

    return max(candidates, key=clearance)


def via1_reach(tech):
    """How far metal has to pass a via1 along its long axis.

    via.4a is what the via needs on every side. via.5a and met2.5 ask for
    more than that along one axis, and the technology file states the
    difference rather than the total, so it is added here rather than
    remembered.
    """
    return tech["via1_surround"] + tech.get("via1_directional_extra", 0.0)


def routing_floor(plan, tech):
    """The lowest metal2 track can sit above everything already drawn."""
    top = plan["height_um"] + gate_stack_height(tech)
    for tap in plan.get("taps", []):
        top = max(top, tap["y2"])
    return top + tech["metal2_spacing"]


def route(plan, nets, tech):
    """Wire the circuit: metal1 up from each pin, metal2 across between them.

    nets maps a net to the (device, terminal) pairs it reaches, which is
    what a net actually is. Bulk pins are not routed here: a bulk connects
    through the well or substrate tap, not through a wire.

    The layers have directions, and that is the point. Metal1 runs only
    vertically, from a pin up to its net's track; metal2 runs only
    horizontally, along the track. Two wires on different layers cross
    without touching, so the only place a net joins another layer is where
    a via says so. On one layer this cannot be done: every stub would cross
    every track beneath it.
    """
    index = {item["name"]: item for item in plan["devices"]}
    pad = via_pad(tech)
    # A track thinner than a via pad cannot enclose the via that joins it
    # to metal1, so the track takes the pad as its floor.
    m2_width = max(tech["metal2_width"], pad)
    pitch = m2_width + tech["metal2_spacing"]

    routed = {}
    track = routing_floor(plan, tech)

    # Every x a device pin already stands at. A tap runs the width of its
    # group, so left to itself it would land on top of one of them.
    occupied = sorted({
        (box[0] + box[2]) / 2.0
        for item in plan["devices"]
        for box in item.get("terminals", {}).values()
    })

    for net in sorted(nets):
        landings = []
        bulks = set()
        for device_name, terminal in nets[net]:
            if terminal == "bulk":
                bulks.add(device_name)
                continue                       # a tap carries it, not a wire
            item = index.get(device_name)
            if item is None:
                continue
            box = item.get("terminals", {}).get(terminal)
            if box is not None:
                landings.append((device_name, terminal, box))

        # A device's body is held at a voltage by its well or substrate
        # tap, so the tap belongs on whichever net that is. Without this
        # the taps are drawn and connected to nothing.
        for tap in plan.get("taps", []):
            if tap.get("terminal") is None or not (bulks & set(tap["serves"])):
                continue
            strip = tap["terminal"]
            middle = tap.get("landing")
            if middle is None:
                middle = clear_landing(strip, occupied, pad, tech)
            middle = min(max(middle, strip[0] + pad / 2.0),
                         strip[2] - pad / 2.0)
            cut = tech["via_width"]
            landings.append((tap["kind"], "tap",
                             (middle - cut / 2.0, strip[1],
                              middle + cut / 2.0, strip[3])))

        if len(landings) < 2:
            continue                           # nothing to join

        stubs, vias, contacts = [], [], []
        for device_name, terminal, box in landings:
            middle = (box[0] + box[2]) / 2.0
            # Metal1 from the pin up to the track.
            # The metal has to reach past the via at the bottom too, which
            # a pin only as tall as its own via does not do by itself. It
            # is the directional rule that binds here, not the all-round
            # one: met1.5 asks for more along one axis, and this is the
            # axis a vertical stub can give it on.
            below = max(tech["metal1_via_surround"],
                        tech.get("metal1_via_directional", 0.0))
            foot = min(box[1], box[3] - tech["via_width"] - below)
            stubs.append({
                "x1": middle - pad / 2.0, "y1": foot,
                "x2": middle + pad / 2.0, "y2": track + m2_width,
                "length_um": track + m2_width - foot,
                "device": device_name, "terminal": terminal,
            })
            # The via down onto the local interconnect at the pin, and the
            # one up to metal2 at the track.
            cut = tech["via_width"]
            contacts.append({
                "x1": middle - cut / 2.0, "y1": box[3] - cut,
                "x2": middle + cut / 2.0, "y2": box[3],
            })
            cut1 = tech["via1_width"]
            vias.append({
                "x1": middle - cut1 / 2.0, "y1": track + (m2_width - cut1) / 2.0,
                "x2": middle + cut1 / 2.0, "y2": track + (m2_width + cut1) / 2.0,
            })
            # via.5a: metal1 owes the via more above it than beside it, and
            # a stub that stops level with its track gives only the beside.
            stubs[-1]["y2"] = max(
                stubs[-1]["y2"],
                track + (m2_width + cut1) / 2.0 + via1_reach(tech))
            stubs[-1]["length_um"] = stubs[-1]["y2"] - stubs[-1]["y1"]

        # met2.5: the track owes its end vias more metal beyond them than a
        # pad's worth. Along the track the inner vias have the whole run;
        # it is the two on the ends that decide how far it reaches.
        middles = [(box[0] + box[2]) / 2.0 for _, _, box in landings]
        left, right = min(middles), max(middles)
        overhang = tech["via1_width"] / 2.0 + via1_reach(tech)
        span = {
            "x1": left - overhang, "y1": track,
            "x2": right + overhang, "y2": track + m2_width,
            "length_um": (right - left) + 2.0 * overhang,
        }

        routed[net] = {
            "track": track,
            "span": span,
            "stubs": stubs,
            "vias": vias,
            "contacts": contacts,
            "length_um": span["length_um"] + sum(s["length_um"] for s in stubs),
            "devices": sorted({name for name, _, _ in landings}),
            "pins": [(name, terminal) for name, terminal, _ in landings],
        }
        track += pitch

    return routed


def routed_parasitics(routed, tech):
    """Capacitance per net, from the metal that was actually drawn."""
    return {
        net: {
            "length_um": item["length_um"],
            "capacitance_f": wire_capacitance(item["length_um"], tech),
            "devices": item["devices"],
            "segments": 1 + len(item["stubs"]),
        }
        for net, item in routed.items()
    }


def routing_shapes(routed, layers):
    """The drawn wires and vias, on their real layers."""
    shapes = []
    for item in routed.values():
        span = item["span"]
        shapes.append((layers["MET2"][0], layers["MET2"][1],
                       span["x1"], span["y1"], span["x2"], span["y2"]))
        for stub in item["stubs"]:
            shapes.append((layers["MET1"][0], layers["MET1"][1],
                           stub["x1"], stub["y1"], stub["x2"], stub["y2"]))
        for via in item["vias"]:
            shapes.append((layers["VIA1"][0], layers["VIA1"][1],
                           via["x1"], via["y1"], via["x2"], via["y2"]))
        for cut in item["contacts"]:
            shapes.append((layers["MCON"][0], layers["MCON"][1],
                           cut["x1"], cut["y1"], cut["x2"], cut["y2"]))
    return shapes


def wire_capacitance(length_um, tech, layer="metal1"):
    """A metal run of the minimum width, in farads.

    Two contributions, both from the PDK: the plate capacitance under the
    wire, and the fringe capacitance off its two long edges.
    """
    area_key = layer + "_area" if layer + "_area" in tech else "metal1_area"
    edge_key = layer + "_edge" if layer + "_edge" in tech else "metal1_edge"
    width = tech["metal1_width"]
    return length_um * width * tech[area_key] + 2.0 * length_um * tech[edge_key]


def net_parasitics(plan, nets, tech):
    """Capacitance for each net, from how far its devices sit apart.

    A net's wire is taken as the Manhattan run across the devices it joins,
    which for a row is the distance between the outermost of them plus the
    height it has to climb. It is a first-order length for a first-order
    placement, and it is the length actually drawn in the floorplan.
    """
    result = {}
    index = {item["name"]: item for item in plan["devices"]}

    for net, members in nets.items():
        present = [index[name] for name in members if name in index]
        if len(present) < 2:
            continue
        left = min(item["x"] for item in present)
        right = max(item["x"] + item["width"] for item in present)
        run = (right - left) + plan["height_um"]
        result[net] = {
            "length_um": run,
            "capacitance_f": wire_capacitance(run, tech),
            "devices": [item["name"] for item in present],
        }
    return result


def parasitic_transform(parasitics):
    """A netlist edit that hangs each net's wire capacitance on that net.

    The same hook the corner suite uses: the circuit builders stay ignorant
    of it, and what runs is the same deck with the interconnect added.
    """
    additions = []
    for net, item in sorted(parasitics.items()):
        if item["capacitance_f"] <= 0:
            continue
        additions.append(
            "Cpar_%s %s 0 %.6g" % (net, net, item["capacitance_f"])
        )
    if not additions:
        return lambda netlist: netlist

    block = "\n".join(additions)

    def transform(netlist):
        marker = "\n.control"
        if marker not in netlist:
            raise LayoutDataError(
                "The netlist has no control block to insert parasitics before."
            )
        return netlist.replace(marker, "\n" + block + marker, 1)

    return transform
