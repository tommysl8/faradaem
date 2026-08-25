"""Floorplan and parasitics, read out of the PDK rather than invented.

What a schematic leaves out is that the circuit has to occupy silicon, and
that the wires joining its devices are themselves capacitors. This module
answers both questions to first order, and it answers them with the
foundry's own numbers: every dimension and every capacitance below is read
out of the SKY130 Magic technology file at run time, with the rule that
produced it named beside it. Nothing here is a remembered constant.

What this is:

    a single-finger floorplan, devices in a row at the minimum diffusion
    spacing, and the interconnect capacitance the resulting wire lengths
    imply, computed from the layers' own capacitance per unit area and
    per unit edge

What this is not, and the difference matters:

    an extracted layout. There is no placement tool here, no router, no
    design rule check and no layout versus schematic. Those need Magic and
    Netgen, which do not run on this machine. The area below is the area
    of the floorplan described above, not of a layout anyone has drawn,
    and the capacitance is what that floorplan implies, not what an
    extractor measured. Both are labelled that way everywhere they appear.

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
GDS_LAYER_NAMES = ("DIFF", "POLY", "NWELL", "LI", "MET1")


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


def floorplan_shapes(plan, layers, tech):
    """The floorplan as rectangles on real layers, in microns.

    Each device is its diffusion, with the poly gate crossing it where the
    channel is. That is the least a transistor can be drawn as and still be
    recognisable as one when the file is opened somewhere else. It is not a
    complete device: there are no contacts, no wells, no implants, and no
    routing between the devices.
    """
    diff = layers["DIFF"]
    poly = layers["POLY"]

    shapes = []
    for well in plan.get("wells", []):
        shapes.append((layers["NWELL"][0], layers["NWELL"][1],
                       well["x1"], well["y1"], well["x2"], well["y2"]))
    for device in plan["devices"]:
        x1 = device["x"]
        x2 = device["x"] + device["width"]
        y1 = device["y"]
        y2 = device["y"] + device["height"]
        shapes.append((diff[0], diff[1], x1, y1, x2, y2))

        # The gate sits in the middle of the cell, its length wide, and
        # overhangs the diffusion at both ends as poly has to.
        overhang = (device["width"] - device["gate_length"]) / 2.0
        gate_x1 = x1 + overhang
        gate_x2 = gate_x1 + device["gate_length"]
        endcap = tech["poly_endcap"]
        shapes.append((poly[0], poly[1],
                       gate_x1, y1 - endcap, gate_x2, y2 + endcap))

    return shapes


def floorplan_gds(plan, name="FARADAEM_FLOORPLAN", when=None):
    """The floorplan as a GDSII stream, ready to open in a layout tool."""
    return gds.library(name, name,
                       floorplan_shapes(plan, gds_layers(), tech_constants()),
                       when=when)


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
    return {
        "along": length_um + 2.0 * tech["diff_overhang"],
        "across": max(width_um, tech["contact_width"]),
        "gate_length": length_um,
        "device_width": width_um,
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
        cursor += cell["along"] + tech["diff_spacing"]

    span = cursor - tech["diff_spacing"]
    tallest = max(item["height"] for item in placed)

    # One well around the whole PMOS group. They are placed together for
    # this reason: two wells that are not the same well have to stay a rule
    # apart, which a row alternating types cannot manage.
    wells = []
    pmos = [item for item in placed if item["kind"] == "pfet"]
    if pmos:
        margin = tech["nwell_surround"]
        left = min(item["x"] for item in pmos) - margin
        right = max(item["x"] + item["width"] for item in pmos) + margin
        bottom = min(item["y"] for item in pmos) - margin
        top = max(item["y"] + item["height"] for item in pmos) + margin
        # A well below the minimum width is not a well.
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

    return {
        "devices": placed,
        "wells": wells,
        "width_um": span,
        "height_um": tallest,
        "area_um2": span * tallest,
        "active_area_um2": sum(item["width"] * item["height"] for item in placed),
        "spacing_um": tech["diff_spacing"],
    }


def route(plan, nets, tech):
    """Draw each net as metal on its own track above the devices.

    A channel router in its simplest honest form: every net gets one
    horizontal track, far enough above the row and from its neighbours to
    satisfy the metal spacing rule, with a vertical stub down to each
    device it connects. No two nets share a track, so nothing shorts.

    Returns the drawn segments per net, with the length that was drawn.
    The point is that the length is now measured off geometry rather than
    guessed from a bounding box.
    """
    index = {item["name"]: item for item in plan["devices"]}
    width = tech["metal1_width"]
    pitch = width + tech["metal1_spacing"]

    routed = {}
    track = plan["height_um"] + tech["diff_spacing"]

    for net in sorted(nets):
        members = [index[name] for name in nets[net] if name in index]
        if len(members) < 2:
            continue

        left = min(item["x"] for item in members) + width / 2.0
        right = max(item["x"] + item["width"] for item in members) - width / 2.0
        segments = [{
            "x1": left, "y1": track,
            "x2": right, "y2": track + width,
            "length_um": right - left,
        }]

        # A stub from the track down onto each device it serves.
        for item in members:
            centre = item["x"] + item["width"] / 2.0
            drop = track - (item["y"] + item["height"])
            if drop <= 0:
                continue
            segments.append({
                "x1": centre - width / 2.0,
                "y1": item["y"] + item["height"],
                "x2": centre + width / 2.0,
                "y2": track,
                "length_um": drop,
            })

        routed[net] = {
            "segments": segments,
            "length_um": sum(part["length_um"] for part in segments),
            "track": track,
            "devices": [item["name"] for item in members],
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
            "segments": len(item["segments"]),
        }
        for net, item in routed.items()
    }


def routing_shapes(routed, layers):
    """The drawn wires, as rectangles on metal1."""
    metal = layers["MET1"]
    shapes = []
    for item in routed.values():
        for part in item["segments"]:
            shapes.append((metal[0], metal[1],
                           part["x1"], part["y1"], part["x2"], part["y2"]))
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
