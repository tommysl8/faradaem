"""Layout versus schematic, run by KLayout's own engine.

spice/lvs.py walks the drawn rectangles itself and compares transistor
connectivity. It is honest about what it is: pins compared by position,
sizes not compared, passives not compared. This module is the other half
of the promise made when the sign-off deck was installed: stop writing
EDA, start calling it.

KLayout's LayoutToNetlist is handed the same shapes the GDS is written
from and told only what the layers mean -- which diffusion is n-type,
what a contact joins, what the resistor marker marks, what a square
micron of plate is worth. It finds the devices itself, measures their
widths, lengths, resistances and capacitances from the geometry, and its
NetlistComparer matches the result against the circuit by topology, the
way a sign-off LVS does, not by the order devices were placed.

What this is not: the PDK's own LVS runset, which recognises the full
SKY130 device zoo. This declares the five device kinds Faradaem draws.
The declaration is the trust boundary and it is stated in the result.
"""

import os
import tempfile

from . import layout

try:
    import klayout.db as kdb
except ImportError:                                        # pragma: no cover
    kdb = None

#: Below this overlap area, in square microns, crossing metals are wiring
#: and not a capacitor. The largest wiring crossing is a via pad on a
#: track, a tenth of a square micron; the smallest drawn capacitor the
#: form allows is hundreds. The gap between is four orders of magnitude.
PLATE_MIN_UM2 = 1.0

#: Relative tolerance when comparing an extracted value to the netlist's.
#: The drawn resistor is quantised to the manufacturing grid, so it can
#: differ from the asked-for value by a part in a few thousand.
VALUE_TOLERANCE = 0.02


class KlvsError(RuntimeError):
    """Raised when the comparison cannot run at all -- never a silent pass."""


def available():
    return kdb is not None


def _build_layout(shapes, layers, name):
    """The shapes as an in-memory KLayout database, no file involved."""
    ly = kdb.Layout()
    ly.dbu = 0.001
    top = ly.create_cell(name)

    def snap(value):
        return int(round(value / ly.dbu))

    pair_to_index = {}
    for layer_name, pair in layers.items():
        pair_to_index[pair] = ly.layer(pair[0], pair[1])

    for shape in shapes:
        index = pair_to_index.get((shape[0], shape[1]))
        if index is None:
            continue
        top.shapes(index).insert(kdb.Box(snap(shape[2]), snap(shape[3]),
                                         snap(shape[4]), snap(shape[5])))
    return ly, top


def extract_netlist(shapes, layers, tech, name):
    """KLayout's extraction over Faradaem's shapes.

    Returns the LayoutToNetlist object; its netlist holds the devices the
    engine found from the geometry alone, sizes measured, connectivity
    traced through the contacts and vias.
    """
    if kdb is None:
        raise KlvsError(
            "KLayout's Python package is not installed, so no extraction "
            "ran. This is a refusal, not a pass."
        )

    ly, top = _build_layout(shapes, layers, name)
    index = {n: ly.layer(*layers[n]) for n in layers}

    l2n = kdb.LayoutToNetlist(kdb.RecursiveShapeIterator(ly, top, []))

    def polygons(layer_name):
        return l2n.make_polygon_layer(index[layer_name], layer_name)

    diff = polygons("DIFF")
    poly = polygons("POLY")
    nsdm = polygons("NSDM")
    psdm = polygons("PSDM")
    nwell = polygons("NWELL")
    li = polygons("LI")
    met1 = polygons("MET1")
    met2 = polygons("MET2")
    cont = polygons("CONT")
    mcon = polygons("MCON")
    via1 = polygons("VIA1")
    tap = polygons("TAP")
    polyres = polygons("POLYRES")

    # What the layers mean. This block is the entire trust boundary: the
    # engine below it takes no further instruction from Faradaem.
    ndiff = diff & nsdm
    pdiff = diff & psdm
    ngate = poly & ndiff
    pgate = poly & pdiff
    nsd = ndiff - ngate
    psd = pdiff - pgate
    res_body = poly & polyres
    poly_con = poly - res_body

    # The substrate is not drawn, but the p-taps stand in it, so it exists
    # here as everything the well is not.
    bounds = top.bbox().enlarged(kdb.Vector(5000, 5000))
    psub = kdb.Region(bounds) - nwell
    ntap = tap & nwell
    ptap = tap - nwell

    # A capacitor is metals overlapping on purpose, and purpose has a
    # size: the largest wiring crossing is a via pad, a tenth of a square
    # micron, and the smallest drawn plate is hundreds.
    dbu_area = int(PLATE_MIN_UM2 / (ly.dbu * ly.dbu))
    # The two-argument with_area is the exact-match form; the range form
    # takes the inverse flag as well, and an open top end means "at least".
    plate = (met1 & met2).with_area(dbu_area, None, False)
    cap_p1 = met2 & plate.sized(1)
    cap_p2 = met1 & plate.sized(1)

    for region, label in ((ndiff, "ndiff"), (pdiff, "pdiff"),
                          (ngate, "ngate"), (pgate, "pgate"),
                          (nsd, "nsd"), (psd, "psd"),
                          (res_body, "res_body"), (poly_con, "poly_con"),
                          (psub, "psub"), (ntap, "ntap"), (ptap, "ptap"),
                          (cap_p1, "cap_p1"), (cap_p2, "cap_p2")):
        l2n.register(region, label)

    l2n.extract_devices(kdb.DeviceExtractorMOS4Transistor("nfet"),
                        {"SD": nsd, "G": ngate, "W": psub})
    l2n.extract_devices(kdb.DeviceExtractorMOS4Transistor("pfet"),
                        {"SD": psd, "G": pgate, "W": nwell})
    l2n.extract_devices(kdb.DeviceExtractorResistor(
        "res", tech["poly_sheet_res"]),
        {"R": res_body, "C": poly_con})
    l2n.extract_devices(kdb.DeviceExtractorCapacitor(
        "cap", tech["plate_cap"]),
        {"P1": cap_p1, "P2": cap_p2})

    for region in (nsd, psd, poly_con, ngate, pgate, li, met1, met2,
                   ntap, ptap, nwell, psub, cap_p1, cap_p2):
        l2n.connect(region)

    # A gate's stripe continues into its contacted landing pad.
    l2n.connect(ngate, poly_con)
    l2n.connect(pgate, poly_con)

    # And the layers join exactly where a cut joins them.
    for below, cut in ((nsd, cont), (psd, cont), (ntap, cont), (ptap, cont),
                       (poly_con, cont)):
        l2n.connect(below, cut)
    l2n.connect(cont, li)
    l2n.connect(li, mcon)
    l2n.connect(mcon, met1)
    l2n.connect(met1, via1)
    l2n.connect(via1, met2)
    l2n.connect(cap_p1, met2)
    l2n.connect(cap_p2, met1)

    # The body a tap ties: the well, or the substrate it stands in.
    l2n.connect(ntap, nwell)
    l2n.connect(ptap, psub)

    l2n.extract_netlist()
    return l2n


def cell_netlist(circuit_id, params):
    """The circuit as a flat comparison netlist: devices, nothing else.

    Transistors as M lines so the reader builds four-terminal MOS devices,
    the drawn passives as R and C lines. The external elements -- bias,
    load, references -- are not devices of the cell and are left out, the
    same way the drawing leaves them out.
    """
    from . import circuits

    lines = ["* " + circuit_id + " as drawn, for comparison"]
    for name, device in sorted(circuits.circuit_devices(
            circuit_id, params).items()):
        pins = device["terminals"]
        w_l = ""
        # The sizes, off the same netlist line the simulator saw.
        for token in circuits.CORES[circuit_id](dict(params)):
            parts = token.split()
            if parts and parts[0] == "X" + name:
                # The X line writes bare microns, the SKY130 subcircuit
                # convention. A plain M line is read in metres, so the
                # suffix goes on here or the reader believes in
                # ten-metre transistors.
                w_l = " ".join(part + "U" for part in parts[6:8])
                break
        lines.append("M" + name + " " + pins["drain"] + " " + pins["gate"]
                     + " " + pins["source"] + " " + pins["bulk"] + " "
                     + device["kind"] + " " + w_l)

    for item in circuits.drawable_passives(circuit_id, params):
        letter = "R" if item["kind"] == "resistor" else "C"
        lines.append(letter + item["name"] + " " + item["nodes"][0] + " "
                     + item["nodes"][1] + " " + repr(item["value"]))

    return "\n".join(lines) + "\n"


def _read_spice(text):
    """The comparison netlist, read by KLayout's own SPICE reader."""
    handle, path = tempfile.mkstemp(suffix=".cir", prefix="faradaem-klvs-")
    os.close(handle)
    try:
        with open(path, "w", encoding="ascii") as stream:
            stream.write(text)
        netlist = kdb.Netlist()
        netlist.read(path, kdb.NetlistSpiceReader())
        return netlist
    finally:
        os.remove(path)


class _Log(kdb.GenericNetlistCompareLogger if kdb else object):
    """The comparer's own account of a mismatch, kept as plain lines."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def log_entry(self, severity, message):        # noqa: N802 - KLayout API
        self.lines.append(str(message))


def _tolerant(netlist):
    """Loosen the value comparison to the drawing quantisation.

    A resistor snapped to the manufacturing grid is a fraction of a
    percent off the asked-for ohms; that is drawing, not a mismatch.
    """
    for cls_name in ("res", "cap", "RES", "CAP", "nfet", "pfet"):
        cls = netlist.device_class_by_name(cls_name)
        if cls is None:
            continue
        for parameter in cls.parameter_definitions():
            if parameter.name in ("R", "C", "W", "L"):
                cls.equal_parameters = kdb.EqualDeviceParameters(
                    parameter.id(), 0.0, VALUE_TOLERANCE)


def compare(circuit_id, params, shapes=None, layers=None, tech=None):
    """Extract the drawn netlist and compare it against the circuit.

    Returns the verdict, the engine's log lines, and both netlists as
    text, so a mismatch can be read rather than taken on faith.
    """
    from . import circuits

    tech = tech if tech is not None else layout.tech_constants()
    layers = layers if layers is not None else layout.gds_layers()
    if shapes is None:
        shapes = circuits.layout_shapes(circuit_id, params)

    name = circuit_id.upper()[:30]
    l2n = extract_netlist(shapes, layers, tech, name)
    extracted = l2n.netlist()

    # Wiring crossings extract as sub-femtofarad capacitors between nets.
    # They are parasitics, not devices, and the comparer is told to ignore
    # anything smaller than the smallest capacitor the form can ask for.
    schematic = _read_spice(cell_netlist(circuit_id, params))

    for netlist in (extracted, schematic):
        _tolerant(netlist)

    logger = _Log()
    comparer = kdb.NetlistComparer(logger)
    comparer.max_resistance = 1.0e-3        # short drawn wiring resistors
    comparer.min_capacitance = 4.9e-14      # below the form's smallest cap

    # The reader upcases model names and calls the top circuit .TOP;
    # the extractor keeps the names it was given. Bind both, so the
    # comparison is about topology and values, never about spelling.
    for mine, theirs in (("nfet", "NFET"), ("pfet", "PFET"),
                         ("res", "RES"), ("cap", "CAP")):
        left = extracted.device_class_by_name(mine)
        right = schematic.device_class_by_name(theirs)
        if left is not None and right is not None:
            comparer.same_device_classes(left, right)

    mine_top = extracted.circuit_by_name(name)
    theirs_top = (schematic.circuit_by_name(".TOP")
                  or next(schematic.each_circuit(), None))
    if mine_top is not None and theirs_top is not None:
        comparer.same_circuits(mine_top, theirs_top)

    match = comparer.compare(extracted, schematic)

    return {
        "match": bool(match),
        "log": logger.lines[-40:],
        "extracted": extracted.to_s(),
        "schematic": cell_netlist(circuit_id, params),
        "engine": "KLayout " + kdb.__version__
                  if hasattr(kdb, "__version__") else "KLayout",
        "coverage": (
            "KLayout's LayoutToNetlist and NetlistComparer over the same "
            "shapes the GDS is written from: devices recognised from the "
            "geometry, widths, lengths, resistances and capacitances "
            "measured from it, and the result matched against the circuit "
            "by topology. Faradaem declares what the layers mean and "
            "nothing else. This is not the PDK's own LVS runset, which "
            "recognises the full SKY130 device zoo rather than the five "
            "kinds drawn here."
        ),
    }


# ---------------------------------------------------------------------------
# what the drawn wires cost in ohms
# ---------------------------------------------------------------------------

try:
    import klayout.pex as kpex
except ImportError:                                        # pragma: no cover
    kpex = None


def _squares(box_um, port_a, port_b, dbu=0.001):
    """Squares of sheet between two points of one rectangle.

    KLayout's square-counting extractor does the measuring; the caller
    multiplies by whichever sheet the rectangle is drawn on.
    """
    extractor = kpex.RExtractor.square_counting_extractor(dbu)

    def snap(value):
        return int(round(value / dbu))

    polygon = kdb.Polygon(kdb.Box(snap(box_um[0]), snap(box_um[1]),
                                  snap(box_um[2]), snap(box_um[3])))
    ports = [kdb.Point(snap(port_a[0]), snap(port_a[1])),
             kdb.Point(snap(port_b[0]), snap(port_b[1]))]
    network = extractor.extract(polygon, ports, [])
    network.simplify()
    for element in network.each_element():
        text = element.to_s()
        return float(text.split()[-1])
    return 0.0


def routing_resistance(routed, tech):
    """Each net's worst pin-to-pin resistance, measured off the drawing.

    The squares come from KLayout's extractor over the drawn rectangles;
    the sheet values and the per-cut via resistances come from the PDK.
    The path priced is the routing itself -- landing pad to landing pad
    through metal1, the track, and the cuts -- not the device underneath.

    On a straight track the worst pair of pins is the outermost pair, so
    that is the pair reported, with the parts it is made of.
    """
    if kpex is None:
        raise KlvsError(
            "KLayout's Python package is not installed, so no resistance "
            "was extracted. This is a refusal, not a pass."
        )

    per_cut = 2.0 * (tech["mcon_res"] + tech["via1_res"])
    found = {}
    for net, item in routed.items():
        stubs = item.get("stubs", [])
        if len(stubs) < 2:
            continue
        ordered = sorted(stubs, key=lambda stub: stub["x1"])
        far_left, far_right = ordered[0], ordered[-1]

        track = item["span"]
        track_y = (track["y1"] + track["y2"]) / 2.0
        stub_squares = 0.0
        for stub in (far_left, far_right):
            middle = (stub["x1"] + stub["x2"]) / 2.0
            stub_squares += _squares(
                (stub["x1"], stub["y1"], stub["x2"], stub["y2"]),
                (middle, stub["y1"] + 0.02), (middle, track_y))

        left_x = (far_left["x1"] + far_left["x2"]) / 2.0
        right_x = (far_right["x1"] + far_right["x2"]) / 2.0
        track_squares = _squares(
            (track["x1"], track["y1"], track["x2"], track["y2"]),
            (left_x, track_y), (right_x, track_y))

        found[net] = {
            "worst_ohms": (stub_squares * tech["metal1_sheet_res"]
                           + track_squares * tech["metal2_sheet_res"]
                           + per_cut),
            "stub_squares": stub_squares,
            "track_squares": track_squares,
            "vias_ohms": per_cut,
        }
    return found
