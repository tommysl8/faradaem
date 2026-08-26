"""What each circuit is made of, at the level of devices and nodes.

This module knows transistors and the nets between them and nothing about
how any of it is measured. The three SKY130 amplifiers each have a core
here -- the devices that make them that amplifier and not another one --
written so a testbench can instantiate several copies of one driven
differently, which is what the rejection deck does.

It also reads connectivity back out of a netlist. That is deliberate: the
list of which device pins share a net is not kept beside the circuit where
it could drift, it is read from the lines that get simulated.
"""

import math

from .errors import NoFloorplanError
from .runner import _fmt  # the shared netlist number formatter


#: Op-amp macromodel: the resistor that sets the open-loop pole with Cp.
MACROMODEL_RP = 1000.0


#: The SKY130 device V0.2 puts on the page.  It is a subcircuit, so it is
#: instantiated with an X prefix; a plain M line will not resolve.
NFET_MODEL = "sky130_fd_pr__nfet_01v8"


#: Two-stage op-amp fixed context. The supply and common mode are properties
#: of the 1.8 V process the devices come from, and the bias reference pair is
#: sized once: every current in the amplifier is a mirror ratio against W8.
OPAMP_VDD = 1.8


OPAMP_VCM = 0.9


OPAMP_W8 = 5e-6


OPAMP_W5 = 5e-6


#: How close to a rail the servoed output may sit before the operating point
#: is reported as broken rather than measured.
OPAMP_RAIL_MARGIN = 0.2


#: The widest a single SKY130 01v8 device may be. Both flavours accept
#: 100 um and refuse 101, verified against the model library rather than
#: read off a datasheet. A wider transistor is built from fingers, which
#: these netlists do not emit, so this is the honest ceiling for a
#: declared width and no parameter may claim more.
SKY130_MAX_WIDTH_M = 1e-4


#: Presentation-layer sanity bands for the common-source bias.  These are not
#: device parameters and nothing is computed from them: they only decide which
#: caution the readout shows beside numbers ngspice already produced.
CS_TRIODE_VDS = 0.15


CS_WEAK_MARGIN = 0.10


def _microns(metres, label):
    """Format a length for a SKY130 device line, which takes microns.

    Rounded at picometre resolution so 1.5e-7 m prints as 0.15 rather than as
    the 0.15000000000000002 that the bare multiply produces.
    """
    return _fmt(round(float(metres) * 1e6, 6), label)


def _twopole_stages(prefix, sense_node, out_node, params):
    """One two-pole op-amp: an inverting gain stage then two buffered poles.

    Each pole is buffered so the next cannot load it. That is what keeps the
    two poles independent, and it is why the closed form is exact rather than
    approximate.
    """
    first = params["gbw"] / params["a0"]
    c1 = 1.0 / (2.0 * math.pi * MACROMODEL_RP * first)
    c2 = 1.0 / (2.0 * math.pi * MACROMODEL_RP * params["fp2"])
    rp = _fmt(MACROMODEL_RP, "macromodel_rp")

    return [
        "E" + prefix + "1 " + prefix + "a 0 0 " + sense_node + " "
        + _fmt(params["a0"], "a0"),
        "R" + prefix + "1 " + prefix + "a " + prefix + "b " + rp,
        "C" + prefix + "1 " + prefix + "b 0 " + _fmt(c1, "c1"),
        "E" + prefix + "2 " + prefix + "c 0 " + prefix + "b 0 1",
        "R" + prefix + "2 " + prefix + "c " + prefix + "d " + rp,
        "C" + prefix + "2 " + prefix + "d 0 " + _fmt(c2, "c2"),
        "E" + prefix + "3 " + out_node + " 0 " + prefix + "d 0 1",
    ]


def _opamp_core(params, tag="", inverting="inn", non_inverting="inp",
                supply="vdd"):
    """The two-stage amplifier itself: bias, devices, compensation, load.

    Everything that makes it an amplifier and nothing about how it is being
    measured. tag prefixes every internal node and device name, so a deck
    can hold several copies driven differently without them touching. The
    gates and the rail are arguments for the same reason: one copy sees a
    differential drive, another a common-mode one, another a moving supply.

    M1's gate is the inverting input, the diode side of the mirror.
    """
    nf = " " + NFET_MODEL + " "
    pf = " sky130_fd_pr__pfet_01v8 "
    length = "L=" + _microns(params["l"], "l")

    def node(name):
        return tag + name

    def nfet(name, drain, gate, source, width):
        return ("XM" + tag + name + " " + drain + " " + gate + " " + source
                + " 0" + nf + "W=" + _microns(width, "w") + " " + length)

    def pfet(name, drain, gate, source, width):
        return ("XM" + tag + name + " " + drain + " " + gate + " " + source
                + " " + supply + pf + "W=" + _microns(width, "w") + " " + length)

    return [
        "Ib" + tag + " " + supply + " " + node("nbias") + " DC "
        + _fmt(params["ibias"], "ibias"),
        nfet("8", node("nbias"), node("nbias"), "0", OPAMP_W8),
        nfet("1", node("d1"), inverting, node("tail"), params["wpair"]),
        nfet("2", node("d2"), non_inverting, node("tail"), params["wpair"]),
        pfet("3", node("d1"), node("d1"), supply, params["wload"]),
        pfet("4", node("d2"), node("d1"), supply, params["wload"]),
        nfet("5", node("tail"), node("nbias"), "0", OPAMP_W5),
        pfet("6", node("out"), node("d2"), supply, params["w6"]),
        nfet("7", node("out"), node("nbias"), "0", params["w7"]),
        "Rz" + tag + " " + node("d2") + " " + node("zx") + " "
        + _fmt(params["rz"], "rz"),
        "Cc" + tag + " " + node("zx") + " " + node("out") + " "
        + _fmt(params["cc"], "cc"),
        "CL" + tag + " " + node("out") + " 0 " + _fmt(params["cl"], "cl"),
    ]


def _ota_core(params, tag="", inverting="inn", non_inverting="inp",
              supply="vdd"):
    """The five-transistor OTA itself. One stage, so the output is M2's
    drain and M2's gate is the inverting input, the non-diode side."""
    nf = " " + NFET_MODEL + " "
    pf = " sky130_fd_pr__pfet_01v8 "
    length = "L=" + _microns(params["l"], "l")

    def node(name):
        return tag + name

    def nfet(name, drain, gate, source, width):
        return ("XM" + tag + name + " " + drain + " " + gate + " " + source
                + " 0" + nf + "W=" + _microns(width, "w") + " " + length)

    def pfet(name, drain, gate, source, width):
        return ("XM" + tag + name + " " + drain + " " + gate + " " + source
                + " " + supply + pf + "W=" + _microns(width, "w") + " " + length)

    return [
        "Ib" + tag + " " + supply + " " + node("nbias") + " DC "
        + _fmt(params["ibias"], "ibias"),
        nfet("8", node("nbias"), node("nbias"), "0", OPAMP_W8),
        nfet("1", node("d1"), non_inverting, node("tail"), params["wpair"]),
        nfet("2", node("out"), inverting, node("tail"), params["wpair"]),
        pfet("3", node("d1"), node("d1"), supply, params["wload"]),
        pfet("4", node("out"), node("d1"), supply, params["wload"]),
        nfet("5", node("tail"), node("nbias"), "0", OPAMP_W5),
        "CL" + tag + " " + node("out") + " 0 " + _fmt(params["cl"], "cl"),
    ]


#: The folded cascode's fixed devices, the ones the form does not expose.
#: The tail is twice the reference so each input device carries it once,
#: and the folding sources carry twice again so the cascode branch has a
#: current of its own to work with.
FC_W_BIAS = 5e-6


FC_W_TAIL = 10e-6


FC_W_MIRROR = 10e-6


FC_W_PDIODE = 40e-6


#: The n-channel cascodes. Swept against 10, 20 and 30 microns at three
#: channel lengths: the width of these two moves the gain by seven decibels
#: and nothing else in the topology comes close. Longer channels did not
#: help and cost bandwidth, which is why the default length stays at the
#: minimum the form offers rather than being raised for gain.
FC_W_NCASC = 30e-6


#: The two cascode gate references. A finished design generates these on
#: chip from the same reference current; here they are ideal sources, which
#: is honest as far as it goes and is said plainly in the manual.
FC_VPCASC = 0.60


FC_VNCASC = 0.70


def _folded_cascode_core(params, tag="", inverting="inn", non_inverting="inp",
                         supply="vdd"):
    """The folded cascode itself: one stage, and most of the gain of two.

    The input pair pulls current out of the folding sources rather than
    into a load, which is where the name comes from. What is left after the
    pair has taken its share flows through the cascodes, and the cascodes
    are what make the output resistance -- and so the gain -- large without
    a second stage to compensate.

    M2's gate is the inverting input: its drain is folded through M7 to the
    output, and that path inverts once.
    """
    nf = " " + NFET_MODEL + " "
    pf = " sky130_fd_pr__pfet_01v8 "
    length = "L=" + _microns(params["l"], "l")

    def node(name):
        return tag + name

    def nfet(name, drain, gate, source, width):
        return ("XM" + tag + name + " " + drain + " " + gate + " " + source
                + " 0" + nf + "W=" + _microns(width, "w") + " " + length)

    def pfet(name, drain, gate, source, width):
        return ("XM" + tag + name + " " + drain + " " + gate + " " + source
                + " " + supply + pf + "W=" + _microns(width, "w") + " " + length)

    return [
        "Ib" + tag + " " + supply + " " + node("nbias") + " DC "
        + _fmt(params["ibias"], "ibias"),
        # the reference, the tail, and the pair that folds into the sources
        nfet("8", node("nbias"), node("nbias"), "0", FC_W_BIAS),
        nfet("5", node("tail"), node("nbias"), "0", FC_W_TAIL),
        nfet("1", node("fold1"), non_inverting, node("tail"), params["wpair"]),
        nfet("2", node("fold2"), inverting, node("tail"), params["wpair"]),
        # the sources it folds into, and the mirror that biases them
        pfet("3", node("fold1"), node("pbias"), supply, params["wfold"]),
        pfet("4", node("fold2"), node("pbias"), supply, params["wfold"]),
        pfet("13", node("pbias"), node("pbias"), supply, FC_W_PDIODE),
        nfet("14", node("pbias"), node("nbias"), "0", FC_W_BIAS),
        # the cascodes, which are the whole point
        pfet("6", node("casc1"), node("pcasc"), node("fold1"), params["wcasc"]),
        pfet("7", node("out"), node("pcasc"), node("fold2"), params["wcasc"]),
        nfet("9", node("casc1"), node("ncasc"), node("mir1"), FC_W_NCASC),
        nfet("10", node("out"), node("ncasc"), node("mir2"), FC_W_NCASC),
        nfet("11", node("mir1"), node("casc1"), "0", FC_W_MIRROR),
        nfet("12", node("mir2"), node("casc1"), "0", FC_W_MIRROR),
        # the two cascode gate references
        "Vpc" + tag + " " + node("pcasc") + " 0 DC " + _fmt(FC_VPCASC, "vpc"),
        "Vnc" + tag + " " + node("ncasc") + " 0 DC " + _fmt(FC_VNCASC, "vnc"),
        "CL" + tag + " " + node("out") + " 0 " + _fmt(params["cl"], "cl"),
    ]


def folded_cascode_devices(params):
    """The fourteen transistors, with their types.

    n-channel first and p-channel after, so the five p-channel devices
    share one n-well: two wells that are not the same well have to stay
    1.27 microns apart, which a row alternating types cannot manage.
    """
    return [
        ("M8", FC_W_BIAS, params["l"], "nfet"),
        ("M5", FC_W_TAIL, params["l"], "nfet"),
        ("M1", params["wpair"], params["l"], "nfet"),
        ("M2", params["wpair"], params["l"], "nfet"),
        ("M14", FC_W_BIAS, params["l"], "nfet"),
        ("M9", FC_W_NCASC, params["l"], "nfet"),
        ("M10", FC_W_NCASC, params["l"], "nfet"),
        ("M11", FC_W_MIRROR, params["l"], "nfet"),
        ("M12", FC_W_MIRROR, params["l"], "nfet"),
        ("M3", params["wfold"], params["l"], "pfet"),
        ("M4", params["wfold"], params["l"], "pfet"),
        ("M13", FC_W_PDIODE, params["l"], "pfet"),
        ("M6", params["wcasc"], params["l"], "pfet"),
        ("M7", params["wcasc"], params["l"], "pfet"),
    ]


def opamp_devices(params):
    """The eight transistors of the two-stage op-amp, with their types.

    M3, M4 and M6 are the PMOS: the mirror load and the second stage driver.
    The rest are NMOS. The floorplan groups them by type, because every PMOS
    needs an n-well and wells that are not the same well have to stay 1.27
    microns apart, which a row alternating types cannot do.
    """
    return [
        ("M8", OPAMP_W8, params["l"], "nfet"),
        ("M1", params["wpair"], params["l"], "nfet"),
        ("M2", params["wpair"], params["l"], "nfet"),
        ("M5", OPAMP_W5, params["l"], "nfet"),
        ("M7", params["w7"], params["l"], "nfet"),
        ("M3", params["wload"], params["l"], "pfet"),
        ("M4", params["wload"], params["l"], "pfet"),
        ("M6", params["w6"], params["l"], "pfet"),
    ]


def ota_devices(params):
    """The six transistors of the OTA, NMOS first then the mirror load."""
    return [
        ("M8", OPAMP_W8, params["l"], "nfet"),
        ("M1", params["wpair"], params["l"], "nfet"),
        ("M2", params["wpair"], params["l"], "nfet"),
        ("M5", OPAMP_W5, params["l"], "nfet"),
        ("M3", params["wload"], params["l"], "pfet"),
        ("M4", params["wload"], params["l"], "pfet"),
    ]


#: Which devices each internal net has to reach. The supply and ground rails
#: are left out: they are drawn as planes, not as runs between two devices,
#: and treating them as point to point wires would overstate them.
#: The device-level description of each circuit that has one, so the
#: connectivity can be read off the same lines that get simulated instead
#: of being kept in a second list beside them.
CORES = {
    "opamp_two_stage": _opamp_core,
    "ota_5t": _ota_core,
    "folded_cascode": _folded_cascode_core,
}


#: The terminals of a MOSFET, in the order SPICE writes them on the line.
TERMINAL_ORDER = ("drain", "gate", "source", "bulk")


def parse_devices(lines):
    """Every transistor on these netlist lines, with the net on each pin.

    A SKY130 device is a subcircuit, so it is an X line, and the four nodes
    between the name and the model are its drain, gate, source and bulk.
    Reading them here means the layout and the checker are working from the
    same statement of the circuit that ngspice is.
    """
    devices = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 6 or not parts[0].upper().startswith("X"):
            continue
        model = parts[5]
        if "fet" not in model:
            continue
        name = parts[0][1:]                    # XM1 -> M1
        devices[name] = {
            "name": name,
            "model": model,
            "kind": "pfet" if "pfet" in model else "nfet",
            "terminals": dict(zip(TERMINAL_ORDER, parts[1:5])),
        }
    return devices


def nets_from_devices(devices):
    """Which terminals each net reaches, from the parsed devices.

    The result is keyed by net and lists (device, terminal) pairs, which is
    what a router needs to know: a net does not reach a device, it reaches
    one particular pin of it.
    """
    nets = {}
    for device in devices.values():
        for terminal, net in device["terminals"].items():
            nets.setdefault(net, []).append((device["name"], terminal))
    return {net: sorted(pins) for net, pins in nets.items()}


def circuit_devices(circuit_id, params):
    """The transistors of a circuit, read off its own netlist."""
    core = CORES.get(circuit_id)
    if core is None:
        raise NoFloorplanError(
            "The circuit " + repr(circuit_id) + " has no device-level "
            "description to read a layout from."
        )
    return parse_devices(core(dict(params)))


#: What a device letter means, for the parts of a circuit that are not
#: transistors. These are read from the netlist the same way the devices
#: are, so nothing has to be kept in step by hand.
ELEMENT_KINDS = {
    "R": "resistor", "C": "capacitor", "L": "inductor",
    "I": "current source", "V": "voltage source", "E": "controlled source",
}


def parse_elements(lines):
    """Everything on these lines that is not a transistor.

    A layout draws transistors, wells and wires. Anything else the netlist
    holds -- a compensation capacitor, a nulling resistor, a bias current
    -- is in the circuit and not in the drawing, and a comparison that
    does not say so is answering a narrower question than it appears to.
    """
    found = []
    for line in lines:
        parts = line.split()
        if not parts or parts[0].upper().startswith("X"):
            continue
        letter = parts[0][0].upper()
        if letter not in ELEMENT_KINDS:
            continue
        found.append({
            "name": parts[0],
            "kind": ELEMENT_KINDS[letter],
            "nodes": parts[1:3],
        })
    return found


def circuit_elements(circuit_id, params):
    """The parts of a circuit that no layout here draws."""
    core = CORES.get(circuit_id)
    if core is None:
        raise NoFloorplanError(
            "The circuit " + repr(circuit_id) + " has no device-level "
            "description to read a layout from."
        )
    return parse_elements(core(dict(params)))


def circuit_nets(circuit_id, params):
    """Which terminals each net of a circuit reaches."""
    return nets_from_devices(circuit_devices(circuit_id, params))
