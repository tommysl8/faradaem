"""Layout versus schematic: is the drawing the circuit it claims to be.

Every other check in this project asks whether the geometry is legal. None
of them asks whether it is *right*. A layout can satisfy every spacing and
width rule in the deck while connecting a gate to the wrong net, and
nothing drawn would look wrong. That is the one way this tool could be
wrong rather than merely incomplete, which is why it is worth doing.

The method is the standard one. Walk the drawn shapes and work out what is
electrically joined to what, using only the geometry: two rectangles on one
conducting layer that overlap are the same conductor, and the layers are
joined where, and only where, a contact or a via says so. Recognise a
transistor wherever poly crosses diffusion, and take its source and drain
from the diffusion either side of that crossing. Then compare the nets that
fall out against the nets in the netlist that ngspice actually simulated.

What this is:

    a connectivity comparison. It answers "is every terminal on the net
    the schematic puts it on", and it answers it from the drawing.

What this is not:

    a full layout versus schematic. A real one matches the two circuits by
    graph isomorphism, so it can tell you that the drawing is a correct
    circuit even when the devices are named differently. This one pairs
    devices by their position in the row, because the same program placed
    them, and compares from there. It also does not check device sizes
    against the schematic's W and L. Both are named in the result.
"""

from . import layout

#: The layers current flows along. Anything else in the drawing is an
#: implant, a well boundary or a cut, none of which conduct on their own.
CONDUCTORS = ("DIFF", "TAP", "POLY", "LI", "MET1", "MET2")

#: Which cut joins which two layers. A cut that overlaps both makes them
#: one conductor, and nothing else does.
CUTS = (
    ("CONT", ("DIFF", "TAP", "POLY"), ("LI",)),
    ("MCON", ("LI",), ("MET1",)),
    ("VIA1", ("MET1",), ("MET2",)),
)

#: The node every substrate tap is tied to. A chip has one substrate.
SUBSTRATE = "substrate"

TOLERANCE_UM = 1e-9


class LvsError(RuntimeError):
    """Raised when the comparison cannot be carried out at all."""


def _rect(shape):
    _, _, x1, y1, x2, y2 = shape
    return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)


def _on(shapes, layers, name):
    if name not in layers:
        return []
    number, datatype = layers[name]
    return [_rect(s) for s in shapes
            if s[0] == number and s[1] == datatype]


def _overlaps(first, second):
    """True when two rectangles share area, not merely an edge.

    Touching edges are drawn deliberately in this layout -- a track meets
    its own stub that way -- so touching counts as joined.
    """
    return (first[0] <= second[2] + TOLERANCE_UM
            and second[0] <= first[2] + TOLERANCE_UM
            and first[1] <= second[3] + TOLERANCE_UM
            and second[1] <= first[3] + TOLERANCE_UM)


class Union:
    """Disjoint sets, which is all a net extractor really is."""

    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:               # path compression
            self.parent[item], item = root, self.parent[item]
        return root

    def join(self, first, second):
        one, two = self.find(first), self.find(second)
        if one != two:
            self.parent[one] = two

    def groups(self):
        found = {}
        for item in self.parent:
            found.setdefault(self.find(item), []).append(item)
        return found


def split_diffusion(diff, gates):
    """Cut a diffusion into the regions a gate separates.

    This is the whole reason a transistor is a transistor: the channel
    under the gate is not a conductor until the gate says so, and the
    diffusion either side of it is two different nets. A extractor that
    treats a crossed diffusion as one shape reports every device as a
    short from source to drain.
    """
    crossing = sorted(
        (g for g in gates if _overlaps(diff, g) and g[0] > diff[0] - TOLERANCE_UM
         and g[2] < diff[2] + TOLERANCE_UM),
        key=lambda g: g[0]
    )
    if not crossing:
        return [tuple(diff)], []

    regions = []
    edge = diff[0]
    for gate in crossing:
        regions.append((edge, diff[1], gate[0], diff[3]))
        edge = gate[2]
    regions.append((edge, diff[1], diff[2], diff[3]))
    return regions, crossing


def extract(shapes, layers):
    """What the drawing connects, worked out from the drawing alone.

    Returns the conductors, the nets they fall into, and every transistor
    found where poly crosses diffusion.
    """
    pieces = {}
    for name in CONDUCTORS:
        pieces[name] = _on(shapes, layers, name)

    gates = pieces["POLY"]
    devices = []
    regions = []                                # (id, rect) for diffusion
    for index, diff in enumerate(pieces["DIFF"]):
        parts, crossing = split_diffusion(diff, gates)
        ids = []
        for part_index, part in enumerate(parts):
            key = ("DIFF", index, part_index)
            regions.append((key, part))
            ids.append(key)
        for gate_index, gate in enumerate(crossing):
            devices.append({
                "gate_shape": gate,
                "diffusion": diff,
                "source": ids[gate_index],
                "drain": ids[gate_index + 1],
                "gate": ("POLY", gates.index(gate)),
                "x": gate[0],
            })

    union = Union()
    named = list(regions)
    for name in CONDUCTORS:
        if name == "DIFF":
            continue
        for index, rect in enumerate(pieces[name]):
            named.append(((name, index), rect))

    lookup = dict(named)
    for key, _ in named:
        union.find(key)

    # Same layer, overlapping, therefore the same conductor.
    by_layer = {}
    for key, rect in named:
        by_layer.setdefault(key[0], []).append((key, rect))
    for group in by_layer.values():
        for position, (key, rect) in enumerate(group):
            for other_key, other in group[position + 1:]:
                if _overlaps(rect, other):
                    union.join(key, other_key)

    # And joined across layers only where a cut says so.
    for cut_layer, below, above in CUTS:
        for cut in _on(shapes, layers, cut_layer):
            touched = [key for key, rect in named
                       if key[0] in below + above and _overlaps(cut, rect)]
            for other in touched[1:]:
                union.join(touched[0], other)

    # A well is a conductor too, and its tap is what sets its voltage.
    for index, well in enumerate(_on(shapes, layers, "NWELL")):
        key = ("NWELL", index)
        union.find(key)
        lookup[key] = well
        for other, rect in named:
            if other[0] == "TAP" and _overlaps(well, rect):
                union.join(key, other)

    # Every substrate tap is the one substrate.
    union.find(SUBSTRATE)
    wells = _on(shapes, layers, "NWELL")
    for key, rect in named:
        if key[0] != "TAP":
            continue
        inside = any(w[0] <= rect[0] and w[1] <= rect[1]
                     and w[2] >= rect[2] and w[3] >= rect[3] for w in wells)
        if not inside:
            union.join(SUBSTRATE, key)

    return {
        "union": union,
        "shapes": lookup,
        "devices": sorted(devices, key=lambda item: item["x"]),
        "nets": union.groups(),
    }


def _bulk_of(extracted, device, layers, shapes):
    """Which body the device sits in: a well, or the substrate."""
    union = extracted["union"]
    diff = device["diffusion"]
    for index, well in enumerate(_on(shapes, layers, "NWELL")):
        if (well[0] <= diff[0] and well[1] <= diff[1]
                and well[2] >= diff[2] and well[3] >= diff[3]):
            return union.find(("NWELL", index))
    return union.find(SUBSTRATE)


def _try_bind(mapping, reverse, node, net):
    """Pair one drawn conductor with one netlist net, if that is possible.

    A conductor is one net and a net is one conductor, so a pairing that
    would give either of them a second partner is a contradiction, not a
    preference.
    """
    if mapping.get(node, net) != net:
        return False
    if reverse.get(net, node) != node:
        return False
    mapping[node] = net
    reverse[net] = node
    return True


def _solve(pins):
    """Pair every drawn conductor with the netlist net it should be.

    A gate and a bulk name their net outright. A source and a drain do not:
    a MOSFET is symmetric, so which diffusion is called which is a naming
    choice, and both orientations are correct circuits. Choosing wrongly
    for one device can make the next device look broken, so the two
    orientations are searched rather than guessed, and the search backs out
    of a choice that cannot be completed.

    Returns the pairing, its inverse, and the pins that could not be paired
    at all, which are the real mismatches.
    """
    mapping, reverse = {}, {}
    conflicts = []
    flips = {}

    for name, wanted, drawn_pins in pins:
        for terminal in ("gate", "bulk"):
            if not _try_bind(mapping, reverse, drawn_pins[terminal],
                             wanted["terminals"][terminal]):
                conflicts.append((name + "." + terminal,
                                  wanted["terminals"][terminal]))

    def search(index, mapping, reverse, turned):
        if index == len(pins):
            return mapping, reverse, turned
        name, wanted, drawn_pins = pins[index]
        for flipped in (False, True):
            attempt, back = dict(mapping), dict(reverse)
            first = wanted["terminals"]["drain" if flipped else "source"]
            second = wanted["terminals"]["source" if flipped else "drain"]
            if (_try_bind(attempt, back, drawn_pins["source"], first)
                    and _try_bind(attempt, back, drawn_pins["drain"], second)):
                onward = dict(turned)
                onward[name] = flipped
                found = search(index + 1, attempt, back, onward)
                if found is not None:
                    return found
        return None

    found = search(0, mapping, reverse, {})
    if found is not None:
        return found[0], found[1], conflicts, found[2]

    # No orientation of the whole row works, so the layout and the netlist
    # genuinely disagree. Pair what can be paired, in order, and let the
    # comparison below say where it broke.
    for name, wanted, drawn_pins in pins:
        for terminal in ("source", "drain"):
            if not _try_bind(mapping, reverse, drawn_pins[terminal],
                             wanted["terminals"][terminal]):
                conflicts.append((name + "." + terminal,
                                  wanted["terminals"][terminal]))
    return mapping, reverse, conflicts, flips


def compare(shapes, layers, schematic, order, undrawn=None):
    """Compare the drawn connectivity against the simulated netlist.

    schematic is the parsed netlist: device name to its four terminal nets.
    order is the device names in the order they were placed, which is how a
    drawn device is paired with a schematic one.

    undrawn is everything in the circuit that is not a transistor and is
    therefore not in the drawing at all: a compensation capacitor, a
    nulling resistor, a bias current. Those are reported rather than
    ignored, because a match across the transistors is a narrower claim
    than it sounds when two of the missing parts are what makes the
    amplifier stable.

    A MOSFET's source and drain are physically interchangeable, so a swap
    between them is not a mismatch and is not reported as one.
    """
    extracted = extract(shapes, layers)
    drawn = extracted["devices"]
    union = extracted["union"]

    problems = []
    if len(drawn) != len(order):
        problems.append({
            "kind": "device_count",
            "what": ("the drawing holds " + str(len(drawn)) + " transistors "
                     "and the netlist holds " + str(len(order))),
        })

    matched = 0

    # Pair each drawn node with the netlist net it should be. A gate and a
    # bulk say which net they are on unambiguously. A source and a drain do
    # not: a MOSFET is symmetric, so which diffusion carries which name is
    # a naming choice, and the two orientations are equally correct. So the
    # certain pins are matched first, and each device's diffusion pair is
    # then oriented to agree with what is already known.
    pins = []
    for position, device in enumerate(drawn):
        if position >= len(order):
            break
        name = order[position]
        wanted = schematic.get(name)
        if wanted is None:
            problems.append({
                "kind": "unknown_device",
                "what": "the netlist has no device called " + name,
            })
            continue
        matched += 1
        pins.append((name, wanted, {
            "source": union.find(device["source"]),
            "drain": union.find(device["drain"]),
            "gate": union.find(device["gate"]),
            "bulk": _bulk_of(extracted, device, layers, shapes),
        }))

    mapping, reverse, conflicts, flips = _solve(pins)

    # Now every pin can be checked: it is on the net the schematic says, or
    # it is not.
    layout_pins = {}
    schematic_pins = {}
    other = {"source": "drain", "drain": "source"}
    for name, wanted, drawn_pins in pins:
        turned = flips.get(name, False)
        for terminal, node in drawn_pins.items():
            # If the search had to turn this device round, the pin drawn on
            # the left is the one the netlist calls the drain. Reporting it
            # by its drawn name would blame the reader for a symmetry.
            label = other[terminal] if turned and terminal in other else terminal
            layout_pins.setdefault(node, set()).add((name, label))
            schematic_pins.setdefault(
                wanted["terminals"][terminal], set()).add((name, terminal))

    for node, on_node in sorted(layout_pins.items(), key=lambda kv: str(kv[0])):
        net = mapping.get(node)
        if net is None:
            continue
        should = schematic_pins.get(net, set())
        for pin in sorted(on_node - should):
            problems.append({
                "kind": "shorted",
                "what": (_pin(pin) + " is joined to " + net + " in the layout "
                         "and is on " + _net_of(pin, schematic)
                         + " in the schematic"),
            })
        for pin in sorted(should - on_node):
            problems.append({
                "kind": "not_connected",
                "what": (_pin(pin) + " is on " + net + " in the schematic and "
                         "is not joined to it in the layout"),
            })

    for where, net in conflicts:
        problems.append({
            "kind": "shorted",
            "what": (where + " is on " + net + " in the schematic, and in "
                     "the layout that conductor already carries another net"),
        })

    missing = list(undrawn or [])
    return {
        "match": not problems,
        # Separate from `match` on purpose. The connectivity of what was
        # drawn either agrees with the netlist or it does not; what was
        # never drawn is a different fact, and burying it in one boolean
        # would hide whichever of the two the reader cared about.
        "undrawn": missing,
        "drawn_devices_only": bool(missing),
        "problems": problems,
        "devices_drawn": len(drawn),
        "devices_expected": len(order),
        "devices_compared": matched,
        "nets_drawn": len(layout_pins),
        "nets_expected": len(schematic_pins),
        "coverage": (
            "Connectivity only. Every terminal of every transistor found in "
            "the drawing was traced through the contacts and vias that join "
            "the layers, and compared against the netlist that was "
            "simulated. Devices are paired by their position in the row "
            "rather than matched by graph isomorphism, and their widths and "
            "lengths are not compared. Only transistors are compared: "
            "anything else the circuit holds is listed under undrawn and is "
            "not in the layout at all. This is not the sign-off LVS."
        ),
    }


def _pin(pin):
    return pin[0] + "." + pin[1]


def _net_of(pin, schematic):
    device = schematic.get(pin[0])
    if device is None:
        return "nothing"
    return device["terminals"].get(pin[1], "nothing")


def run(shapes, layers, schematic, order):
    """compare, with the layers and technology resolved from the PDK."""
    if not shapes:
        raise LvsError("There is no geometry to compare against anything.")
    return compare(shapes, layers or layout.gds_layers(), schematic, order)
