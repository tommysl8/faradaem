"""The Faradaem circuit catalogue.

One dict describes every circuit the app can simulate.  ``spice.runner`` owns
the mechanics -- finding ngspice, running a netlist, parsing output, measuring a
Bode response -- and this module owns topology and composition: what a circuit
is made of, how its sweep is framed, what is measured, and what closed-form
value that measurement should agree with.

Adding a circuit means adding one CIRCUITS entry.  It should never mean adding
a route, a branch in the server, or a bespoke response shape.

Each entry carries:
    id, name        identity, shown in the UI
    analysis        "dc" or "ac"
    caption         the datasheet figure caption
    params          ordered param specs the UI renders a form from
    build           params (plus sweep args for ac) -> netlist text
    centre          ac only: params -> the frequency to frame the sweep on
    measure         raw results -> the response dict
    checks          closed-form values the measurement is compared against
    readout         how the UI lays the numbers out

A check is a *check*.  ngspice produces the measurement; the formula only says
what we expected, and disagreement is reported, never silently reconciled.
"""

from __future__ import annotations

import base64
import math
import os
import tempfile

from . import layout, runner
from .runner import _fmt  # the shared netlist number formatter

#: Sweeps span this many decades either side of the circuit's centre frequency.
DECADES_EACH_SIDE = 3

#: Samples per decade for every AC sweep.
POINTS_PER_DECADE = 20

#: What ngspice can meaningfully sweep.
FREQ_MIN = 0.01
FREQ_MAX = 1e10

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


class UnknownCircuitError(KeyError):
    """Raised when a circuit id is not in the catalogue."""


class CircuitInputError(ValueError):
    """A parameter combination the circuit cannot be run at. Maps to HTTP 400.

    Every value was individually inside its declared range, but together they
    ask for something the simulator cannot deliver. That is a bad request, not
    a server fault, and the message has to say which way to move.
    """


class BiasError(CircuitInputError):
    """Raised when a bias leaves nothing measurable.

    The run itself succeeded; there was simply nothing in it to measure.
    """


# ---------------------------------------------------------------------------
# small builders for the catalogue entries
# ---------------------------------------------------------------------------


def param(key, label, unit, default, minimum, maximum):
    """One user-editable component value."""
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "default": default,
        "min": minimum,
        "max": maximum,
    }


def check(key, label, measured, unit, tolerance, formula):
    """A closed-form expectation for one measured quantity."""
    return {
        "key": key,
        "label": label,
        "measured": measured,
        "unit": unit,
        "tolerance": tolerance,
        "formula": formula,
    }


def metric(key, label, fmt, unit=None, check_key=None):
    """One number in a result strip."""
    return {"key": key, "label": label, "format": fmt, "unit": unit, "check": check_key}


def preset(label, **params):
    """A worked example: a named set of parameter values worth one click."""
    return {"label": label, "params": params}


def goal(key, label, op, unit, default):
    """One target the design iterator can be asked to hit.

    key names a measured value from this circuit's readout; op is ">=" or
    "<="; default is what the form suggests before the user edits it.
    """
    return {"key": key, "label": label, "op": op, "unit": unit, "default": default}


def sweep_range(centre_hz, decades_each_side=None):
    """Frame a sweep symmetrically around a centre frequency, in decades.

    Most circuits use the shared three-decade half-width. A circuit that has
    to show two widely separated features at once, like an op-amp's flat
    open-loop gain and its crossover, may declare a wider one.
    """
    if not math.isfinite(centre_hz) or centre_hz <= 0.0:
        raise CircuitInputError(
            "These values put the circuit's centre frequency at "
            + repr(centre_hz) + ", which cannot be swept. Change a component "
            "value and run again."
        )

    if decades_each_side is None:
        decades_each_side = DECADES_EACH_SIDE
    span = 10.0 ** decades_each_side
    fstart = min(max(centre_hz / span, FREQ_MIN), FREQ_MAX)
    fstop = min(max(centre_hz * span, FREQ_MIN), FREQ_MAX)

    if fstop <= fstart:
        raise CircuitInputError(
            "These values put the circuit's centre frequency at "
            + ("%g" % centre_hz) + " Hz, outside the sweepable range "
            + ("%g" % FREQ_MIN) + " Hz to " + ("%g" % FREQ_MAX) + " Hz. Change a "
            "component value and run again."
        )

    return fstart, fstop


def ac_control_block(fstart, fstop, out_path, op_prints=None, outputs=None):
    """The .control block every AC circuit shares.

    out_path is written with forward slashes: a backslash is escape-prone
    inside an ngspice control block.

    op_prints names vectors to report from a DC operating point taken ahead
    of the sweep.  Those values land on stdout while the sweep still goes to
    the data file, so one invocation yields both the bias and the response.
    """
    lines = [".control"]

    if op_prints:
        lines.append("op")
        lines.append("print " + " ".join(op_prints))

    lines.extend([
        "ac dec " + _fmt(POINTS_PER_DECADE, "points_per_decade")
        + " " + _fmt(fstart, "fstart") + " " + _fmt(fstop, "fstop"),
    ])

    # One sweep can write several files. A circuit that measures two
    # responses gets them on the same frequency grid, by construction.
    for path, vector in (outputs or [(out_path, "v(out)")]):
        lines.append("wrdata " + str(path).replace("\\", "/") + " " + vector)

    lines.extend([
        "quit",
        ".endc",
        ".end",
    ])

    return lines


def _netlist(title, devices, fstart, fstop, out_path, op_prints=None, outputs=None):
    return "\n".join(
        [title] + devices
        + ac_control_block(fstart, fstop, out_path, op_prints, outputs)
    ) + "\n"


def _microns(metres, label):
    """Format a length for a SKY130 device line, which takes microns.

    Rounded at picometre resolution so 1.5e-7 m prints as 0.15 rather than as
    the 0.15000000000000002 that the bare multiply produces.
    """
    return _fmt(round(float(metres) * 1e6, 6), label)


# ---------------------------------------------------------------------------
# closed-form helpers, used by checks and by sweep framing
# ---------------------------------------------------------------------------


def rc_corner(params):
    """1/(2*pi*R*C), the corner of either RC filter."""
    return 1.0 / (2.0 * math.pi * params["r"] * params["c"])


def rlc_centre(params):
    """1/(2*pi*sqrt(L*C)), the resonant frequency of the series RLC."""
    return 1.0 / (2.0 * math.pi * math.sqrt(params["l"] * params["c"]))


def rlc_q(params):
    """(1/R)*sqrt(L/C), the quality factor of the series RLC."""
    return math.sqrt(params["l"] / params["c"]) / params["r"]


def amp_noise_gain(params):
    """1 + Rf/Rin: what the feedback loop divides the open-loop gain by."""
    return 1.0 + params["rf"] / params["rin"]


def amp_midband_db(params):
    """20*log10(Rf/Rin), the ideal inverting gain magnitude in dB."""
    return 20.0 * math.log10(params["rf"] / params["rin"])


def amp_bandwidth(params):
    """GBW/N, the closed-loop corner."""
    return params["gbw"] / amp_noise_gain(params)


def twopole_beta(params):
    """Rin/(Rin+Rf): the fraction of the output that returns to the input."""
    return params["rin"] / (params["rin"] + params["rf"])


def twopole_poles(params):
    """The two loop poles in rad/s. The first is set by GBW and A0."""
    return (
        2.0 * math.pi * params["gbw"] / params["a0"],
        2.0 * math.pi * params["fp2"],
    )


def twopole_crossover(params):
    """Where the loop gain passes 0 dB, in Hz.

    Setting |T| = 1 on a two-pole loop gives a quadratic in omega squared, so
    this is an exact closed form rather than a numerical search:

        T0^2 = (1 + x/w1^2)(1 + x/w2^2),   x = omega^2
    """
    w1, w2 = twopole_poles(params)
    loop_dc = params["a0"] * twopole_beta(params)

    if loop_dc <= 1.0:
        raise CircuitInputError(
            "The loop gain starts at " + ("%.4g" % loop_dc) + ", which is already "
            "below 1, so it never crosses 0 dB and has no phase margin. Raise A0, "
            "or lower Rf relative to Rin, and run again."
        )

    a = 1.0 / (w1 * w1 * w2 * w2)
    b = 1.0 / (w1 * w1) + 1.0 / (w2 * w2)
    c = 1.0 - loop_dc * loop_dc
    x = (-b + math.sqrt(b * b - 4.0 * a * c)) / (2.0 * a)

    return math.sqrt(x) / (2.0 * math.pi)


def twopole_loop_db(params):
    """The flat loop gain below the first pole, in dB: 20*log10(A0*beta)."""
    return 20.0 * math.log10(params["a0"] * twopole_beta(params))


def twopole_frame(params):
    """Centre the sweep so it spans both the first pole and the crossover.

    A stability plot is only readable if you can see the loop gain flat below
    the dominant pole and then watch it cross zero. Centring on the geometric
    mean of the two puts both inside the same three-decade window; centring on
    crossover alone starts the sweep above the first pole, where the loop gain
    has already rolled off and no longer reads as its DC value.
    """
    first = params["gbw"] / params["a0"]
    return math.sqrt(first * twopole_crossover(params))


def twopole_phase_margin(params):
    """180 degrees minus the phase each pole has contributed at crossover."""
    w1, w2 = twopole_poles(params)
    wc = 2.0 * math.pi * twopole_crossover(params)
    return (
        180.0
        - math.degrees(math.atan(wc / w1))
        - math.degrees(math.atan(wc / w2))
    )


def opamp_seed(targets, params):
    """A launch point for spec-first generation, scaled from the hand design.

    Two rules, both ratios taken from the verified Balanced sizing, and both
    heuristics: the compensation capacitor tracks the load it must drive, and
    the bias current tracks the power budget (the topology draws about twelve
    times Ibias from the supply at the shipped mirror ratios, measured
    2026-08-24, with headroom left under the budget). Nothing from here is
    ever reported; the seed is simulated like any candidate, and the iterator
    does the real work when the seed falls short. Values outside a declared
    parameter range are clamped by the design layer.
    """
    seeded = dict(params)
    seeded["cc"] = 0.5 * params["cl"]
    seeded["ibias"] = targets["power"] / 12.0
    seeded["rz"] = 2000.0
    seeded["wpair"] = 1e-5
    seeded["w6"] = 4e-5
    return seeded


def ota_seed(targets, params):
    """A launch point for the OTA, scaled from its verified base sizing.

    Bias tracks the power budget (the topology draws about twice Ibias from
    the supply: the reference branch and the tail, measured 2026-08-24, with
    headroom left under the budget). A gain target above what the 0.5 um
    sizing delivers reaches for the longer channel. Heuristics only; nothing
    from here is reported, and the iterator does the real work.
    """
    seeded = dict(params)
    seeded["ibias"] = targets["power"] / 5.0
    seeded["l"] = 1e-6 if targets["loop_gain_db"] > 40.0 else 5e-7
    seeded["wpair"] = 1e-5
    seeded["wload"] = 1e-5
    return seeded


def opamp_frame(params):
    """A fixed frame: 3.16 Hz to 316 MHz with the four-decade half-width.

    The open-loop plot has to show the flat gain below the dominant pole and
    the crossover on one axis. Both stay inside this window across the whole
    declared parameter space, so the frame does not chase the parameters.
    """
    return 31622.776601683792


def cs_amp_pole(params):
    """1/(2*pi*RD*CL): where the output pole would sit if the device were ideal.

    Used only to frame the sweep.  It is deliberately NOT offered as an
    analytic check: the real pole is set by RD in parallel with the device
    output resistance, which lands it some tens of percent higher.
    """
    return 1.0 / (2.0 * math.pi * params["rd"] * params["cl"])


def amp_gain_bw(params):
    """Closed-loop gain times closed-loop bandwidth.

    Exactly (Rf/Rin) * GBW / N, which is GBW scaled by Rf/(Rf+Rin) -- NOT GBW
    itself.  At the default 1k/10k that is 0.909*GBW, and reporting plain GBW
    here would show a permanent 9 percent "mismatch" that is arithmetic, not a
    fault in the circuit.
    """
    return (params["rf"] / params["rin"]) * params["gbw"] / amp_noise_gain(params)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def build_divider(params):
    return runner.build_divider_netlist(params["vdd"], params["r1"], params["r2"])


def build_rc_lowpass(params, fstart, fstop, out_path):
    return runner.build_rc_lowpass_netlist(
        params["r"], params["c"], fstart, fstop, POINTS_PER_DECADE, out_path
    )


def build_rc_highpass(params, fstart, fstop, out_path):
    """Series C from the source, shunt R to ground; output across R."""
    return _netlist(
        "* Faradaem RC high-pass",
        [
            "V1 in 0 AC 1",
            "C1 in out " + _fmt(params["c"], "c"),
            "R1 out 0 " + _fmt(params["r"], "r"),
        ],
        fstart, fstop, out_path,
    )


def build_rlc_bandpass(params, fstart, fstop, out_path):
    """Series L, C and R around one loop; output taken across R."""
    return _netlist(
        "* Faradaem series RLC band-pass",
        [
            "V1 in 0 AC 1",
            "L1 in nlc " + _fmt(params["l"], "l"),
            "C1 nlc out " + _fmt(params["c"], "c"),
            "R1 out 0 " + _fmt(params["r"], "r"),
        ],
        fstart, fstop, out_path,
    )


def build_inverting_amp(params, fstart, fstop, out_path):
    """Inverting amplifier around a single-pole op-amp macromodel.

    E1 is the open-loop gain stage, inverting because it senses (0 - v(vm)).
    Rp and Cp put one pole at GBW/A0, and Eb buffers that node so the feedback
    network cannot load the pole.
    """
    open_loop_pole = params["gbw"] / params["a0"]
    pole_capacitance = 1.0 / (2.0 * math.pi * MACROMODEL_RP * open_loop_pole)

    return _netlist(
        "* Faradaem inverting amplifier, single-pole op-amp macromodel",
        [
            "V1 in 0 AC 1",
            "Rin in vm " + _fmt(params["rin"], "rin"),
            "Rf vm out " + _fmt(params["rf"], "rf"),
            "E1 p1 0 0 vm " + _fmt(params["a0"], "a0"),
            "Rp p1 p2 " + _fmt(MACROMODEL_RP, "macromodel_rp"),
            "Cp p2 0 " + _fmt(pole_capacitance, "pole_capacitance"),
            "Eb out 0 p2 0 1",
        ],
        fstart, fstop, out_path,
    )


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


def build_twopole_amp(params, fstart, fstop, out_paths):
    """Two-pole op-amp: the closed loop and the loop gain, in one sweep.

    Two electrically separate networks share only ground, so superposition
    keeps them independent and one AC analysis measures both on the same
    frequency grid.

    The second network is the same amplifier with the loop broken at the
    inverting input. That input draws no current, so breaking there loads
    nothing and the return ratio is exact rather than probe-corrected.

    Eloop emits the return ratio directly, so the sign convention lives in
    the netlist where it can be read instead of in Python where it would
    have to be remembered.
    """
    closed_path, loop_path = out_paths

    devices = [
        "* closed loop: gain and bandwidth",
        "V1 in 0 AC 1",
        "Rin in vm " + _fmt(params["rin"], "rin"),
        "Rf vm out " + _fmt(params["rf"], "rf"),
    ]
    devices += _twopole_stages("p", "vm", "out", params)
    devices += [
        "* the same amplifier, loop broken at the inverting input",
        "Vinj lx 0 AC 1",
    ]
    devices += _twopole_stages("q", "lx", "lo", params)
    devices += [
        "Rfl lo lr " + _fmt(params["rf"], "rf"),
        "Rinl lr 0 " + _fmt(params["rin"], "rin"),
        "Eloop lg 0 0 lr 1",
    ]

    return _netlist(
        "* Faradaem two-pole op-amp, closed loop and loop gain",
        devices, fstart, fstop, None,
        outputs=[(closed_path, "v(out)"), (loop_path, "v(lg)")],
    )


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


def build_opamp_two_stage(params, fstart, fstop, out_paths):
    """SKY130 two-stage Miller op-amp, measured open loop.

    The amplifier: NMOS pair (M1, M2) under a PMOS mirror (M3 diode, M4),
    NMOS tail M5, then a PMOS common-source second stage M6 with NMOS sink
    M7. Cc with its zero-nulling resistor Rz compensates across stage two.
    Ibias into diode M8 sets every current by mirror ratio.

    M1 carries the diode side of the mirror, which makes its gate the
    inverting input: one inversion through the mirror, one more in stage 2.

    The measurement is the classic DC servo. Lfb closes the loop at DC only,
    so the operating point is a unity buffer and always defined. The AC
    drive reaches the inverting gate through Cin from a zero-ohm source, so
    at any swept frequency the source impedance holds that node and the
    fed-back signal divides to nothing: the loop is open by impedance.
    Driving the inverting input gives -a(s), and Einv flips the sign in the
    netlist, so the written vector is +a(s) and the phase starts at zero.
    """
    loop_path = out_paths[0]

    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
        "Vcm inp 0 DC " + _fmt(OPAMP_VCM, "vcm"),
    ] + _opamp_core(params) + [
        "Lfb out inn 1e9",
        "Vs ac 0 DC 0 AC 1",
        "Cin ac inn 1e9",
        "Einv lg 0 0 out 1",
        # A high-gain servo has one intended solution and railed ones. The
        # nodeset is a hint to the solver, never a result.
        ".nodeset v(out)=" + _fmt(OPAMP_VCM, "vcm")
        + " v(inn)=" + _fmt(OPAMP_VCM, "vcm") + " v(d2)=1.1",
    ]

    return _netlist(
        "* Faradaem SKY130 two-stage op-amp, open-loop response",
        devices, fstart, fstop, None,
        op_prints=["v(out)", "i(vdd)"],
        outputs=[(loop_path, "v(lg)")],
    )


def build_ota_5t(params, fstart, fstop, out_paths):
    """SKY130 five-transistor OTA, measured open loop.

    One stage: NMOS pair (M1, M2) under a PMOS mirror (M3 diode, M4), NMOS
    tail M5, bias through diode M8. The output is M2's drain. With a single
    inversion in the loop, the inverting input is M2's gate, the non-diode
    side, so the DC servo feeds back there; the same L and C servo as the
    two-stage op-amp, with Einv restoring the sign of the written vector.
    """
    loop_path = out_paths[0]

    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
        "Vcm inp 0 DC " + _fmt(OPAMP_VCM, "vcm"),
    ] + _ota_core(params) + [
        "Lfb out inn 1e9",
        "Vs ac 0 DC 0 AC 1",
        "Cin ac inn 1e9",
        "Einv lg 0 0 out 1",
        ".nodeset v(out)=" + _fmt(OPAMP_VCM, "vcm")
        + " v(inn)=" + _fmt(OPAMP_VCM, "vcm"),
    ]

    return _netlist(
        "* Faradaem SKY130 5T OTA, open-loop response",
        devices, fstart, fstop, None,
        op_prints=["v(out)", "i(vdd)"],
        outputs=[(loop_path, "v(lg)")],
    )


# ---------------------------------------------------------------------------
# the step response: what an amplifier does when it is not being polite
# ---------------------------------------------------------------------------
#
# A sweep measures an amplifier held to small signals, where it behaves like
# the linear model. A step asks a different question: how fast can the thing
# actually move, and does it stop cleanly when it gets there. Those are the
# numbers a datasheet calls slew rate and settling time, and neither is
# visible in a Bode plot.
#
# The testbench is the amplifier as a unity-gain buffer, which is a real
# closed loop and needs no servo, driven by a step big enough to steer the
# input pair completely. The step comes back down inside the same run,
# because the two edges need not match.

#: The step is differential around the common mode, this many volts tall.
#: Large enough to fully steer the pair, small enough to stay off the rails.
STEP_VOLTS = 0.3

#: The source edge, far faster than any amplifier here, so what the output
#: does is the amplifier's own limit and not the stimulus.
STEP_EDGE_S = 1e-9

#: Points across one half of the window. Enough that the 10 to 90 band is
#: resolved by hundreds of samples rather than a handful.
STEP_POINTS = 5000

#: However fast the amplifier is, look at it for at least this long. Low
#: enough that a fast amplifier's edge is a visible slope rather than a
#: vertical line, high enough to hold the settling tail that follows it.
STEP_WINDOW_FLOOR_S = 2.5e-7

#: Slewing takes step/rate seconds; allow this many of those for the settling
#: that follows before the other edge arrives.
STEP_WINDOW_SLEWS = 6.0


def _step_window(slew_estimate):
    """Half the run: long enough for the slew plus its settling tail."""
    if slew_estimate <= 0:
        return STEP_WINDOW_FLOOR_S
    slew_time = STEP_VOLTS / slew_estimate
    return max(STEP_WINDOW_FLOOR_S, STEP_WINDOW_SLEWS * slew_time)


def opamp_step_window(params):
    """A two-stage amplifier slews at the tail current over Cc.

    The mirror sets the tail from the bias current, and the compensation
    capacitor is what that current has to charge. This is an estimate used
    only to frame the run; the rate that gets reported is measured.
    """
    tail = params["ibias"] * OPAMP_W5 / OPAMP_W8
    return _step_window(tail / params["cc"])


def ota_step_window(params):
    """A single-stage OTA slews at the tail current over the load."""
    tail = params["ibias"] * OPAMP_W5 / OPAMP_W8
    return _step_window(tail / params["cl"])


def step_control_block(window, out_path, op_prints=None):
    """The .control block for a two-edge step response.

    One run holds both edges: the rise at one window, the fall at two, and a
    third window of tail so the falling edge has the same room to settle.
    """
    lines = [".control"]
    if op_prints:
        lines.append("op")
        lines.append("print " + " ".join(op_prints))
    lines.extend([
        "tran " + _fmt(window / STEP_POINTS, "timestep")
        + " " + _fmt(3.0 * window, "stop"),
        "wrdata " + str(out_path).replace("\\", "/") + " v(out)",
        "quit",
        ".endc",
        ".end",
    ])
    return lines


def _pulse_source(window):
    """A step up at one window and back down at two, edges far faster than
    anything the amplifier can do."""
    return ("Vin inp 0 PULSE(" + _fmt(OPAMP_VCM - STEP_VOLTS / 2.0, "low")
            + " " + _fmt(OPAMP_VCM + STEP_VOLTS / 2.0, "high")
            + " " + _fmt(window, "delay")
            + " " + _fmt(STEP_EDGE_S, "rise")
            + " " + _fmt(STEP_EDGE_S, "fall")
            + " " + _fmt(window, "width")
            + " " + _fmt(4.0 * window, "period") + ")")


def build_opamp_step(params, window, out_paths):
    """The two-stage op-amp as a unity buffer, hit with a step.

    Same devices as the open-loop testbench, wired differently: the output
    goes straight back to M1's gate, which is the inverting input, so the
    loop is closed for real. Nothing here is servoed and nothing is
    fictional; what the output does is what the amplifier does.
    """
    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
        _pulse_source(window),
        # The output feeds the inverting gate directly: a real closed loop.
    ] + _opamp_core(params, inverting="out") + [
        ".nodeset v(out)=" + _fmt(OPAMP_VCM - STEP_VOLTS / 2.0, "start")
        + " v(d2)=1.1",
    ]

    return "\n".join(
        ["* Faradaem SKY130 two-stage op-amp, unity buffer step response"]
        + devices
        + step_control_block(window, out_paths[0], op_prints=["v(out)"])
    ) + "\n"


def build_ota_step(params, window, out_paths):
    """The five-transistor OTA as a unity buffer, hit with a step.

    M2's gate is the inverting input on this topology, the non-diode side,
    so that is where the output feeds back.
    """
    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
        _pulse_source(window),
        # M2's gate is the inverting input here, and it takes the feedback.
    ] + _ota_core(params, inverting="out") + [
        ".nodeset v(out)=" + _fmt(OPAMP_VCM - STEP_VOLTS / 2.0, "start"),
    ]

    return "\n".join(
        ["* Faradaem SKY130 5T OTA, unity buffer step response"]
        + devices
        + step_control_block(window, out_paths[0], op_prints=["v(out)"])
    ) + "\n"


def measure_step_response(points, params, window):
    """Slew rate, settling and overshoot, with the rails checked.

    A buffer that has hit a rail is not slewing, it is stuck, and the
    numbers that come off that waveform would be fiction. Say so instead.
    """
    highest = max(value for _, value in points)
    lowest = min(value for _, value in points)
    if highest > OPAMP_VDD - OPAMP_RAIL_MARGIN or lowest < OPAMP_RAIL_MARGIN:
        raise BiasError(
            "The buffer output reached " + ("%.3f" % lowest) + " V to "
            + ("%.3f" % highest) + " V against a " + ("%.2f" % OPAMP_VDD)
            + " V supply, so it is clipping rather than settling. Lower the "
            "bias current or widen the output devices and run again."
        )

    measured = runner.measure_step(points, window, 2.0 * window, window)
    measured["window"] = window
    measured["rise_at"] = window
    return measured


# ---------------------------------------------------------------------------
# rejection and range: what a sweep of the signal path cannot answer
# ---------------------------------------------------------------------------
#
# An AC analysis superposes every source in the deck at once, so one copy of
# an amplifier cannot be asked three questions. It can be asked one question
# three times, in three identical copies, which is what this does: a
# differential drive for the gain, a common-mode drive for CMRR, a moving
# supply for PSRR, and a fourth copy wired as a real buffer and swept from
# rail to rail for the range. One process, one library load, four answers.

#: Rejection is quoted where it is flat, below the dominant pole.
REJECTION_FSTART = 10.0
REJECTION_FSTOP = 1e6
REJECTION_POINTS_PER_DECADE = 10

#: The transfer curve is swept in steps this size across the whole supply.
SWEEP_STEP_V = 0.01

#: Below this the deck did not bias and its numbers would be noise.
MIN_USABLE_GAIN_DB = 10.0


def datasheet_control_block(out_paths):
    """One DC sweep and one AC sweep, four files, in that order."""
    dc_path, dm_path, cm_path, ps_path = out_paths

    def wr(path, vector):
        return "wrdata " + str(path).replace("\\", "/") + " " + vector

    return [
        ".control",
        "dc Vbin 0 " + _fmt(OPAMP_VDD, "vdd") + " " + _fmt(SWEEP_STEP_V, "step"),
        wr(dc_path, "v(bout)"),
        "ac dec " + _fmt(REJECTION_POINTS_PER_DECADE, "points")
        + " " + _fmt(REJECTION_FSTART, "fstart")
        + " " + _fmt(REJECTION_FSTOP, "fstop"),
        wr(dm_path, "v(aout)"),
        wr(cm_path, "v(cout)"),
        wr(ps_path, "v(sout)"),
        "quit",
        ".endc",
        ".end",
    ]


def _rejection_instances(core, params):
    """The four copies, each with one stimulus, sharing nothing but ground.

    a  differential, through the same DC servo the open-loop sweep uses
    c  common mode, both gates driven together
    s  supply, its own rail carrying the signal
    b  a real unity buffer, for the DC sweep
    """
    lines = []

    lines += core(params, tag="a", inverting="ainn", non_inverting="ainp")
    lines += [
        "Vacm ainp 0 DC " + _fmt(OPAMP_VCM, "vcm"),
        "Lfba aout ainn 1e9",
        "Vsa asrc 0 DC 0 AC 1",
        "Cina asrc ainn 1e9",
    ]

    lines += core(params, tag="c", inverting="cinn", non_inverting="ccm")
    lines += [
        "Vccm ccm 0 DC " + _fmt(OPAMP_VCM, "vcm") + " AC 1",
        "Lfbc cout cinn 1e9",
        # Both gates have to move together, so the inverting one is tied to
        # the driven node for the signal while the servo still sets its DC.
        "Ccinj ccm cinn 1e9",
    ]

    lines += core(params, tag="s", inverting="sinn", non_inverting="sinp",
                  supply="svdd")
    lines += [
        "Vsvdd svdd 0 DC " + _fmt(OPAMP_VDD, "vdd") + " AC 1",
        "Vscm sinp 0 DC " + _fmt(OPAMP_VCM, "vcm"),
        "Lfbs sout sinn 1e9",
        # The servo inductor is an open circuit above DC, which would leave
        # this gate floating and the answer meaningless. Hold it still for
        # the signal and leave the DC servo alone.
        "Cgnds sinn 0 1e9",
    ]

    lines += core(params, tag="b", inverting="bout", non_inverting="binp")
    lines += ["Vbin binp 0 DC " + _fmt(OPAMP_VCM, "vcm")]

    lines += [
        ".nodeset v(aout)=" + _fmt(OPAMP_VCM, "vcm")
        + " v(ainn)=" + _fmt(OPAMP_VCM, "vcm"),
        ".nodeset v(cout)=" + _fmt(OPAMP_VCM, "vcm")
        + " v(cinn)=" + _fmt(OPAMP_VCM, "vcm"),
        ".nodeset v(sout)=" + _fmt(OPAMP_VCM, "vcm")
        + " v(sinn)=" + _fmt(OPAMP_VCM, "vcm"),
    ]
    return lines


def build_opamp_datasheet(params, out_paths):
    """Four two-stage op-amps in one deck: rejection and range together."""
    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
    ] + _rejection_instances(_opamp_core, params)

    return "\n".join(
        ["* Faradaem SKY130 two-stage op-amp, rejection and range"]
        + devices + datasheet_control_block(out_paths)
    ) + "\n"


def build_ota_datasheet(params, out_paths):
    """Four OTAs in one deck: rejection and range together."""
    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
    ] + _rejection_instances(_ota_core, params)

    return "\n".join(
        ["* Faradaem SKY130 5T OTA, rejection and range"]
        + devices + datasheet_control_block(out_paths)
    ) + "\n"


def measure_datasheet(bodes, transfer, params):
    """CMRR, PSRR and the range, with the deck checked for having biased."""
    differential, common, supply = bodes

    gain_db = differential["mag_db"][0]
    if gain_db < MIN_USABLE_GAIN_DB:
        raise BiasError(
            "The amplifier measured " + ("%.1f" % gain_db) + " dB of gain in "
            "this testbench, too little to reject anything, so its rejection "
            "figures would be noise. Check the bias current and the device "
            "widths, then run again."
        )

    measured = {
        "gain_db": gain_db,
        "cmrr_db": runner.rejection_db(differential, common),
        "psrr_db": runner.rejection_db(differential, supply),
        # The rail the range is measured against travels with the numbers,
        # so nothing downstream has to keep its own copy of it.
        "supply": OPAMP_VDD,
    }
    measured.update(runner.measure_follower_range(transfer))
    return measured


def build_nfet_cs_amp(params, fstart, fstop, out_path):
    """SKY130 NFET common-source amplifier.

    Vg carries the DC gate bias and the 1 V AC excitation at once, so no
    coupling capacitor is needed.  Source and body both sit at ground, RD
    loads the drain from VDD, and CL sets the output pole.  W and L arrive in
    metres like every other length in the catalogue and are converted here,
    because the SKY130 subcircuits take microns.
    """
    return _netlist(
        "* Faradaem SKY130 NFET common-source amplifier",
        [
            ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
            "Vdd vdd 0 DC " + _fmt(params["vdd"], "vdd"),
            "Vg g 0 DC " + _fmt(params["vgs"], "vgs") + " AC 1",
            "XM1 out g 0 0 " + NFET_MODEL
            + " W=" + _microns(params["w"], "w")
            + " L=" + _microns(params["l"], "l"),
            "RD vdd out " + _fmt(params["rd"], "rd"),
            "CL out 0 " + _fmt(params["cl"], "cl"),
        ],
        fstart, fstop, out_path,
        op_prints=["v(out)", "i(vdd)"],
    )


# ---------------------------------------------------------------------------
# measurements: raw simulator output -> response dict
# ---------------------------------------------------------------------------


def measure_divider(stdout, params):
    return {"vout": runner.parse_vout(stdout)}


def _with_curves(bode, measured):
    """Every AC response carries its curves plus whatever was measured."""
    response = {
        "freq": bode["freq"],
        "mag_db": bode["mag_db"],
        "phase_deg": bode["phase_deg"],
    }
    response.update(measured)
    return response


def measure_rc_lowpass(bode, params, stdout=None):
    return _with_curves(bode, runner.measure_lowpass(bode))


def measure_rc_highpass(bode, params, stdout=None):
    return _with_curves(bode, runner.measure_highpass(bode))


def measure_rlc_bandpass(bode, params, stdout=None):
    return _with_curves(bode, runner.measure_bandpass(bode))


def measure_inverting_amp(bode, params, stdout=None):
    return _with_curves(bode, runner.measure_closedloop(bode))


def measure_twopole_amp(bodes, params, stdout=None):
    """Phase margin from the loop gain, gain and bandwidth from the closed loop.

    The plotted curve is the loop gain, because that is where the headline
    number lives. A phase margin read off a closed loop plot would be a
    number with no line under it.
    """
    closed, loop = bodes

    measured = dict(runner.measure_loop(loop))
    closed_loop = runner.measure_closedloop(closed)
    measured["midband_db"] = closed_loop["midband_db"]
    measured["f3db"] = closed_loop["f3db"]

    return _with_curves(loop, measured)


def measure_opamp_two_stage(bodes, params, stdout=None):
    """Open-loop gain, unity-gain bandwidth and phase margin, plus power.

    The operating point is checked first: an output stuck near a rail means
    the sizing does not bias, and the sweep of a railed amplifier would be a
    measurement of nothing.
    """
    operating = runner.parse_op_values(stdout or "", ("v(out)", "i(vdd)"))
    out_dc = operating["v(out)"]
    supply_current = abs(operating["i(vdd)"])

    if not OPAMP_RAIL_MARGIN < out_dc < OPAMP_VDD - OPAMP_RAIL_MARGIN:
        raise CircuitInputError(
            "The output settled at " + ("%.3f" % out_dc) + " V, against a "
            + ("%.1f" % OPAMP_VDD) + " V supply, so the amplifier does not "
            "bias at these sizes. Rebalance the second stage: W6 sets its "
            "pull-up and W7 its pull-down current, and they have to agree."
        )

    loop = bodes[0]
    try:
        measured = dict(runner.measure_loop(loop))
    except runner.NgspiceParseError as exc:
        raise CircuitInputError(
            "The open-loop gain never crosses 0 dB inside the sweep, so there "
            "is no unity-gain bandwidth or phase margin to measure. Raise the "
            "bias current or widen the input pair, and run again."
        ) from exc

    measured["power"] = OPAMP_VDD * supply_current
    measured["out_dc"] = out_dc
    return _with_curves(loop, measured)


def measure_ota_5t(bodes, params, stdout=None):
    """The OTA's open-loop line: gain, bandwidth, margin, power.

    Same discipline as the two-stage: the operating point is checked first,
    because a railed output means the sizing does not bias and the sweep of
    a railed stage measures nothing.
    """
    operating = runner.parse_op_values(stdout or "", ("v(out)", "i(vdd)"))
    out_dc = operating["v(out)"]
    supply_current = abs(operating["i(vdd)"])

    if not OPAMP_RAIL_MARGIN < out_dc < OPAMP_VDD - OPAMP_RAIL_MARGIN:
        raise CircuitInputError(
            "The output settled at " + ("%.3f" % out_dc) + " V, against a "
            + ("%.1f" % OPAMP_VDD) + " V supply, so the OTA does not bias at "
            "these sizes. Rebalance the pair and mirror widths so the branch "
            "currents agree, and run again."
        )

    loop = bodes[0]
    try:
        measured = dict(runner.measure_loop(loop))
    except runner.NgspiceParseError as exc:
        raise CircuitInputError(
            "The open-loop gain never crosses 0 dB inside the sweep, so there "
            "is no unity-gain bandwidth or phase margin to measure. Raise the "
            "bias current or widen the input pair, and run again."
        ) from exc

    measured["power"] = OPAMP_VDD * supply_current
    measured["out_dc"] = out_dc
    return _with_curves(loop, measured)


def cs_amp_bias_note(vds, vdd):
    """Return a caution about a working but poor bias, or None.

    Both bands are read off numbers ngspice produced.  A bias this far off
    still yields a real measurement, so it is reported with a warning rather
    than refused: seeing what a bottomed-out stage does is part of the point.
    """
    if vds < CS_TRIODE_VDS:
        return (
            "The drain has bottomed out at " + ("%.3f" % vds) + " V, so the "
            "device is in triode and the gain is well below what this stage "
            "can do. Lower Vgs or RD and run again."
        )
    if vds > vdd - CS_WEAK_MARGIN:
        return (
            "The drain is sitting at " + ("%.3f" % vds) + " V, almost at VDD, "
            "so the device is barely conducting. Raise Vgs and run again."
        )
    return None


def measure_nfet_cs_amp(bode, params, stdout=None):
    """Measure the common-source stage, bias first.

    The operating point is read before the response, because a device that is
    off produces a flat, tiny transfer with no -3 dB crossing anywhere in the
    sweep.  That has to surface as a bias problem naming the measured bias,
    not as a bracketing failure the user cannot act on.
    """
    operating = runner.parse_op_values(stdout or "", ("v(out)", "i(vdd)"))
    vds = operating["v(out)"]
    # Vdd sources current into the node it drives, so its branch current is
    # the negative of what the stage draws.
    drain_current = abs(operating["i(vdd)"])

    try:
        measured = runner.measure_closedloop(bode)
    except runner.NgspiceParseError as exc:
        raise BiasError(
            "The device is not amplifying at this bias, so there is no "
            "bandwidth to measure. ngspice settled at Vds = " + ("%.3f" % vds)
            + " V drawing " + ("%.4g" % (drain_current * 1e6)) + " uA. Raise "
            "Vgs above the threshold, around 0.5 V for this device, and run again."
        ) from exc

    response = _with_curves(bode, measured)
    response["drain_voltage"] = vds
    response["drain_current"] = drain_current
    response["power"] = params["vdd"] * drain_current

    note = cs_amp_bias_note(vds, params["vdd"])
    if note:
        response["note"] = note

    return response


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# the floorplan: what the sizing costs in silicon, and what its wires load it
# with
# ---------------------------------------------------------------------------
#
# Everything above this line treats a circuit as a schematic, where a wire is
# free and a transistor takes no room. Neither is true. These describe each
# amplifier as a row of devices so the area can be computed from the PDK's own
# rules, and so the interconnect those positions imply can be hung back on the
# nets and measured against.
#
# It is a floorplan, not a layout: no router, no design rule check, no layout
# versus schematic. Those need tools that are not on this machine, and the
# results say so wherever they appear.


def opamp_devices(params):
    """The eight transistors of the two-stage op-amp, in schematic order."""
    return [
        ("M8", OPAMP_W8, params["l"]),
        ("M1", params["wpair"], params["l"]),
        ("M2", params["wpair"], params["l"]),
        ("M3", params["wload"], params["l"]),
        ("M4", params["wload"], params["l"]),
        ("M5", OPAMP_W5, params["l"]),
        ("M6", params["w6"], params["l"]),
        ("M7", params["w7"], params["l"]),
    ]


def ota_devices(params):
    """The six transistors of the OTA, bias diode included."""
    return [
        ("M8", OPAMP_W8, params["l"]),
        ("M1", params["wpair"], params["l"]),
        ("M2", params["wpair"], params["l"]),
        ("M3", params["wload"], params["l"]),
        ("M4", params["wload"], params["l"]),
        ("M5", OPAMP_W5, params["l"]),
    ]


#: Which devices each internal net has to reach. The supply and ground rails
#: are left out: they are drawn as planes, not as runs between two devices,
#: and treating them as point to point wires would overstate them.
OPAMP_NETS = {
    "nbias": ["M8", "M5", "M7"],
    "d1": ["M1", "M3", "M4"],
    "d2": ["M2", "M4", "M6"],
    "tail": ["M1", "M2", "M5"],
    "out": ["M6", "M7"],
}

OTA_NETS = {
    "nbias": ["M8", "M5"],
    "d1": ["M1", "M3", "M4"],
    "tail": ["M1", "M2", "M5"],
    "out": ["M2", "M4"],
}


CIRCUITS = {
    "divider": {
        "id": "divider",
        "name": "DC divider",
        "analysis": "dc",
        "caption": "Figure 1: Resistive divider, DC operating point.",
        "params": [
            param("vdd", "VDD", "V", 5.0, -1000.0, 1000.0),
            param("r1", "R1", "Ω", 10000.0, 1e-3, 1e12),
            param("r2", "R2", "Ω", 10000.0, 1e-3, 1e12),
        ],
        # Definition, design target, ratio, polarity, top of range, and the
        # limit of the model: the gigohm pair returns exactly the same
        # 2.500 V as the half rail, because an ideal netlist has no load.
        "presets": [
            preset("Half rail", vdd=5.0, r1=10000.0, r2=10000.0),
            preset("3.3 V from 5 V", vdd=5.0, r1=4700.0, r2=9100.0),
            preset("10:1 attenuator", vdd=10.0, r1=9000.0, r2=1000.0),
            preset("Negative rail", vdd=-12.0, r1=10000.0, r2=10000.0),
            preset("1 kV probe", vdd=1000.0, r1=9.9e6, r2=1e5),
            preset("Gigohm pair", vdd=5.0, r1=1e9, r2=1e9),
        ],
        "build": build_divider,
        "measure": measure_divider,
        "checks": [
            check("vout_ideal", "analytic check", "vout", "V", 1e-6,
                  lambda p: p["vdd"] * p["r2"] / (p["r1"] + p["r2"])),
        ],
        "readout": {
            "headline": metric("vout", "simulated v(out)", "eng", "V", "vout_ideal"),
            "stats": [],
        },
    },

    "rc_lowpass": {
        "id": "rc_lowpass",
        "name": "RC low-pass",
        "analysis": "ac",
        "caption": "Figure 2: RC low-pass, frequency response.",
        "params": [
            param("r", "R", "Ω", 1000.0, 1e-3, 1e12),
            param("c", "C", "F", 1.59e-7, 1e-15, 1.0),
        ],
        "centre": rc_corner,
        # "Same corner, 100k" returns every number identically to the first
        # preset: R and C trade off exactly, and the plot does not move.
        "presets": [
            preset("1 kHz corner", r=1000.0, c=1.59e-7),
            preset("Same corner, 100k", r=100000.0, c=1.59e-9),
            preset("10 kHz corner", r=1000.0, c=1.59e-8),
            preset("1 Hz corner", r=1e6, c=1.59e-7),
            preset("Stray 15 pF", r=1e7, c=1.5e-11),
            preset("1 GHz corner", r=50.0, c=3.18e-12),
        ],
        "build": build_rc_lowpass,
        "measure": measure_rc_lowpass,
        "checks": [
            check("fc", "analytic check", "f3db", "Hz", 0.02, rc_corner),
        ],
        "readout": {
            "headline": metric("f3db", "measured f-3 dB", "eng", "Hz", "fc"),
            "stats": [
                metric("dc_gain_db", "DC gain", "db"),
                metric("phase_at_f3db", "phase at f-3 dB", "deg"),
            ],
            "markers": [{"key": "f3db", "label": "f-3dB"}],
        },
    },

    "rc_highpass": {
        "id": "rc_highpass",
        "name": "RC high-pass",
        "analysis": "ac",
        "caption": "Figure 3: RC high-pass, frequency response.",
        "params": [
            param("r", "R", "Ω", 1000.0, 1e-3, 1e12),
            param("c", "C", "F", 1.59e-7, 1e-15, 1.0),
        ],
        "centre": rc_corner,
        "presets": [
            preset("1 kHz corner", r=1000.0, c=1.59e-7),
            preset("100 Hz corner", r=1000.0, c=1.59e-6),
            preset("1 kHz at 100 k\u03a9", r=100000.0, c=1.59e-9),
            preset("Audio coupling", r=10000.0, c=1e-6),
            preset("3 MHz corner", r=50.0, c=1e-9),
            preset("0.16 Hz corner", r=1e6, c=1e-6),
        ],
        "build": build_rc_highpass,
        "measure": measure_rc_highpass,
        "checks": [
            check("fc", "analytic check", "f3db", "Hz", 0.02, rc_corner),
        ],
        "readout": {
            "headline": metric("f3db", "measured f-3 dB", "eng", "Hz", "fc"),
            "stats": [
                metric("passband_db", "passband gain", "db"),
                metric("phase_at_f3db", "phase at f-3 dB", "deg"),
            ],
            "markers": [{"key": "f3db", "label": "f-3dB"}],
        },
    },

    "rlc_bandpass": {
        "id": "rlc_bandpass",
        "name": "RLC band-pass",
        "analysis": "ac",
        "caption": "Figure 4: Series RLC band-pass, frequency response.",
        "params": [
            param("r", "R", "Ω", 10.0, 1e-3, 1e12),
            param("l", "L", "H", 1e-3, 1e-12, 1e3),
            param("c", "C", "F", 1e-6, 1e-15, 1.0),
        ],
        "centre": rlc_centre,
        # A Q ladder, then the same bandwidth at a tenth the Q: "Same width,
        # 500 Hz" shares R and L with "Q = 3.2" and measures the same skirts.
        "presets": [
            preset("Q = 0.1", r=316.23, l=1e-3, c=1e-6),
            preset("Q = 1", r=31.623, l=1e-3, c=1e-6),
            preset("Q = 3.2", r=10.0, l=1e-3, c=1e-6),
            preset("Same width, 500 Hz", r=10.0, l=1e-3, c=1e-4),
            preset("1 MHz centre", r=10.0, l=5e-6, c=5e-9),
            preset("Q = 4.5", r=7.0, l=1e-3, c=1e-6),
        ],
        "build": build_rlc_bandpass,
        "measure": measure_rlc_bandpass,
        "checks": [
            check("f0", "analytic check", "f0_measured", "Hz", 0.02, rlc_centre),
            check("q", "analytic Q", "q_measured", "", 0.03, rlc_q),
        ],
        "readout": {
            "headline": metric("f0_measured", "measured f0", "eng", "Hz", "f0"),
            "stats": [
                metric("q_measured", "Q", "plain", None, "q"),
                metric("peak_gain_db", "peak gain", "db"),
                metric("bw", "bandwidth", "eng", "Hz"),
            ],
            "markers": [
                {"key": "f0_measured", "label": "f0"},
                {"key": "f_lower", "label": "-3dB"},
                {"key": "f_upper", "label": "-3dB"},
            ],
        },
    },

    "inverting_amp": {
        "id": "inverting_amp",
        "name": "Inverting amp",
        "analysis": "ac",
        "caption": "Figure 5: Inverting amplifier, closed-loop response.",
        "params": [
            param("rin", "Rin", "Ω", 1000.0, 1.0, 1e9),
            param("rf", "Rf", "Ω", 10000.0, 1.0, 1e9),
            param("a0", "A0 (open-loop gain)", "", 1e5, 10.0, 1e9),
            param("gbw", "GBW", "Hz", 1e6, 1.0, 1e10),
        ],
        "centre": amp_bandwidth,
        # "Low open-loop gain" ships two amber badges on purpose: a finite A0
        # really does miss the ideal closed-loop gain, and gain x BW still
        # agrees. That disagreement is the module's whole subject.
        "presets": [
            preset("Gain 10x", rin=1000.0, rf=10000.0, a0=1e5, gbw=1e6),
            preset("Gain 100x", rin=1000.0, rf=100000.0, a0=1e5, gbw=1e6),
            preset("Unity gain", rin=10000.0, rf=10000.0, a0=1e5, gbw=1e6),
            preset("Gain 0.1x", rin=10000.0, rf=1000.0, a0=1e5, gbw=1e6),
            preset("Faster op-amp", rin=1000.0, rf=100000.0, a0=1e5, gbw=1e8),
            preset("Low open-loop gain", rin=1000.0, rf=100000.0, a0=100.0, gbw=1e6),
        ],
        "build": build_inverting_amp,
        "measure": measure_inverting_amp,
        "checks": [
            check("midband", "analytic check", "midband_db", "dB", 0.02, amp_midband_db),
            check("bw", "analytic GBW/N", "f3db", "Hz", 0.02, amp_bandwidth),
            check("gain_bw", "analytic gain x BW", "gain_bw_product", "Hz", 0.02,
                  amp_gain_bw),
        ],
        "readout": {
            "headline": metric("midband_db", "midband gain", "db", "dB", "midband"),
            "stats": [
                metric("f3db", "bandwidth", "eng", "Hz", "bw"),
                metric("gain_bw_product", "gain × BW", "eng", "Hz", "gain_bw"),
            ],
            "markers": [{"key": "f3db", "label": "f-3dB"}],
        },
    },


    "twopole_amp": {
        "id": "twopole_amp",
        "name": "Two-pole amp",
        "analysis": "ac",
        "caption": "Figure 6: Two-pole op-amp, loop gain and phase margin.",
        # Two responses from one sweep: the closed loop, and the loop gain that
        # produced it. Measuring them on the same grid is the point.
        "outputs": 2,
        "params": [
            param("rin", "Rin", "\u03a9", 1000.0, 1.0, 1e9),
            param("rf", "Rf", "\u03a9", 10000.0, 1.0, 1e9),
            param("a0", "A0 (open-loop gain)", "", 1e5, 100.0, 1e9),
            param("gbw", "GBW", "Hz", 1e6, 1.0, 1e10),
            param("fp2", "Second pole", "Hz", 1e5, 1.0, 1e10),
        ],
        "centre": twopole_frame,
        # The second pole walking toward crossover is the whole lesson: the
        # circuit does not change, only how much phase is left when it matters.
        "presets": [
            preset("Phase margin 85 deg",
                   rin=1000.0, rf=10000.0, a0=1e5, gbw=1e6, fp2=1e6),
            preset("Phase margin 67 deg",
                   rin=1000.0, rf=10000.0, a0=1e5, gbw=1e6, fp2=2e5),
            preset("Phase margin 54 deg",
                   rin=1000.0, rf=10000.0, a0=1e5, gbw=1e6, fp2=1e5),
            preset("Phase margin 37 deg",
                   rin=1000.0, rf=10000.0, a0=1e5, gbw=1e6, fp2=4e4),
            preset("Nearly unstable",
                   rin=1000.0, rf=10000.0, a0=1e5, gbw=1e6, fp2=1e4),
            preset("More feedback, less margin",
                   rin=10000.0, rf=10000.0, a0=1e5, gbw=1e6, fp2=1e5),
        ],
        "build": build_twopole_amp,
        "measure": measure_twopole_amp,
        "checks": [
            check("pm", "analytic check", "phase_margin", "deg", 0.02,
                  twopole_phase_margin),
            check("fc", "analytic crossover", "f_crossover", "Hz", 0.02,
                  twopole_crossover),
            check("midband", "analytic gain", "midband_db", "dB", 0.02,
                  amp_midband_db),
            check("loop_dc", "analytic loop gain", "loop_gain_db", "dB", 0.02,
                  twopole_loop_db),
        ],
        "readout": {
            "headline": metric("phase_margin", "phase margin", "deg", None, "pm"),
            "stats": [
                metric("f_crossover", "loop crossover", "eng", "Hz", "fc"),
                metric("loop_gain_db", "loop gain at DC", "db", "dB", "loop_dc"),
                metric("midband_db", "closed-loop gain", "db", "dB", "midband"),
                metric("f3db", "closed-loop BW", "eng", "Hz"),
            ],
            "markers": [{"key": "f_crossover", "label": "0dB"}],
        },
        "design": {
            "tunable": ["gbw", "fp2"],
            "goals": [
                goal("phase_margin", "phase margin", ">=", "deg", 60.0),
                goal("f_crossover", "loop crossover", ">=", "Hz", 1e5),
            ],
        },
    },


    "ota_5t": {
        "id": "ota_5t",
        "pdk": True,
        "name": "OTA (SKY130)",
        "analysis": "ac",
        "caption": "Figure 9: SKY130 five-transistor OTA, open-loop response. "
                   "A DC servo sets the operating point; the sweep sees the open loop.",
        "timeout_s": runner.PDK_TIMEOUT_S,
        "outputs": 1,
        "decades": 4,
        "params": [
            param("ibias", "Ibias", "A", 20e-6, 5e-7, 2e-4),
            param("l", "L (all devices)", "m", 5e-7, 1.5e-7, 2e-6),
            param("wpair", "W1,2 (input pair)", "m", 1e-5, 4.2e-7, 1e-4),
            param("wload", "W3,4 (mirror load)", "m", 1e-5, 4.2e-7, 1e-4),
            param("cl", "CL (load)", "F", 2e-12, 1e-13, 1e-10),
        ],
        "floorplan": {
            "devices": ota_devices,
            "nets": OTA_NETS,
            "caption": "Figure 15: six devices in a row at the minimum "
                       "diffusion spacing, drawn to scale. A floorplan, not a "
                       "layout: nothing here has been design rule checked.",
        },
        "datasheet": {
            "build": build_ota_datasheet,
            "caption": "Figure 13: the OTA measured four ways at once. A single "
                       "stage rejects a moving supply differently from a "
                       "two-stage, which is what these numbers show.",
            "readout": [
                metric("cmrr_db", "CMRR", "eng", "dB"),
                metric("psrr_db", "PSRR", "eng", "dB"),
                metric("input_range", "input range", "eng", "V"),
                metric("output_swing", "output swing", "eng", "V"),
            ],
        },
        "step": {
            "build": build_ota_step,
            "window": ota_step_window,
            "caption": "Figure 11: the OTA as a unity buffer, hit with a 0.3 V "
                       "step. A single stage drives the load capacitor "
                       "directly, so the load is what sets the slew rate.",
            "readout": [
                metric("slew_rate", "slew rate", "slew", "V/s"),
                metric("settling_time", "settling to 0.1%", "eng", "s"),
                metric("overshoot", "overshoot", "percent", None),
            ],
        },
        "centre": opamp_frame,
        "presets": [
            preset("Balanced", ibias=2e-5, l=5e-7, wpair=1e-5, wload=1e-5, cl=2e-12),
            preset("High gain", ibias=2e-5, l=1e-6, wpair=1e-5, wload=1e-5, cl=2e-12),
            preset("Low power", ibias=4e-6, l=5e-7, wpair=1e-5, wload=1e-5, cl=2e-12),
            preset("Fast", ibias=6e-5, l=5e-7, wpair=3e-5, wload=1e-5, cl=1e-12),
            preset("Heavy load", ibias=2e-5, l=5e-7, wpair=1e-5, wload=1e-5, cl=2e-11),
            preset("Micropower", ibias=1e-6, l=5e-7, wpair=1e-5, wload=1e-5, cl=2e-12),
        ],
        "build": build_ota_5t,
        "measure": measure_ota_5t,
        # A real device circuit: no closed form here predicts it.
        "checks": [],
        "readout": {
            "headline": metric("loop_gain_db", "open-loop gain", "db", "dB"),
            "stats": [
                metric("f_crossover", "unity-gain BW", "eng", "Hz"),
                metric("phase_margin", "phase margin", "deg"),
                metric("power", "power", "eng", "W"),
                metric("out_dc", "output DC", "eng", "V"),
            ],
            "markers": [{"key": "f_crossover", "label": "0dB"}],
        },
        "design": {
            "tunable": ["ibias", "l", "wpair", "wload"],
            "seed": ota_seed,
            "goals": [
                goal("loop_gain_db", "open-loop gain", ">=", "dB", 35.0),
                goal("f_crossover", "unity-gain BW", ">=", "Hz", 5e6),
                goal("phase_margin", "phase margin", ">=", "deg", 60.0),
                goal("power", "power", "<=", "W", 1e-4),
            ],
        },
    },

    "opamp_two_stage": {
        "id": "opamp_two_stage",
        "pdk": True,
        "name": "Op-amp (SKY130)",
        "analysis": "ac",
        "caption": "Figure 8: SKY130 two-stage op-amp, open-loop response. "
                   "A DC servo sets the operating point; the sweep sees the open loop.",
        "timeout_s": runner.PDK_TIMEOUT_S,
        "outputs": 1,
        # The open-loop plot must show flat gain and crossover on one axis, so
        # this circuit sweeps four decades either side instead of three.
        "decades": 4,
        "params": [
            param("ibias", "Ibias", "A", 20e-6, 5e-7, 2e-4),
            param("l", "L (all devices)", "m", 5e-7, 1.5e-7, 2e-6),
            param("wpair", "W1,2 (input pair)", "m", 1e-5, 4.2e-7, 1e-4),
            param("wload", "W3,4 (mirror load)", "m", 1e-5, 4.2e-7, 1e-4),
            param("w6", "W6 (output driver)", "m", 4e-5, 1e-6, SKY130_MAX_WIDTH_M),
            param("w7", "W7 (output sink)", "m", 1e-5, 4.2e-7, SKY130_MAX_WIDTH_M),
            param("cc", "Cc (Miller)", "F", 2e-12, 5e-14, 2e-11),
            param("rz", "Rz (zero nulling)", "\u03a9", 2000.0, 1e-3, 1e5),
            param("cl", "CL (load)", "F", 2e-12, 1e-13, 1e-10),
        ],
        "floorplan": {
            "devices": opamp_devices,
            "nets": OPAMP_NETS,
            "caption": "Figure 14: the eight devices in a row at the minimum "
                       "diffusion spacing, drawn to scale. A floorplan, not a "
                       "layout: nothing here has been design rule checked.",
        },
        "datasheet": {
            "build": build_opamp_datasheet,
            "caption": "Figure 12: the same op-amp measured four ways at once. "
                       "Three copies take a differential, a common-mode and a "
                       "supply drive; a fourth is swept rail to rail as a buffer.",
            "readout": [
                metric("cmrr_db", "CMRR", "eng", "dB"),
                metric("psrr_db", "PSRR", "eng", "dB"),
                metric("input_range", "input range", "eng", "V"),
                metric("output_swing", "output swing", "eng", "V"),
            ],
        },
        "step": {
            "build": build_opamp_step,
            "window": opamp_step_window,
            "caption": "Figure 10: the same op-amp as a unity buffer, hit with a "
                       "0.3 V step. What the output does here is what it can "
                       "actually do, not what the small-signal model predicts.",
            "readout": [
                metric("slew_rate", "slew rate", "slew", "V/s"),
                metric("settling_time", "settling to 0.1%", "eng", "s"),
                metric("overshoot", "overshoot", "percent", None),
            ],
        },
        "centre": opamp_frame,
        "presets": [
            preset("Balanced", ibias=2e-5, l=5e-7, wpair=1e-5, wload=1e-5,
                   w6=4e-5, w7=1e-5, cc=2e-12, rz=2000.0, cl=2e-12),
            preset("Low power", ibias=5e-6, l=5e-7, wpair=1e-5, wload=1e-5,
                   w6=4e-5, w7=1e-5, cc=2e-12, rz=2000.0, cl=2e-12),
            preset("High gain", ibias=2e-5, l=1e-6, wpair=1e-5, wload=1e-5,
                   w6=4e-5, w7=1e-5, cc=2e-12, rz=2000.0, cl=2e-12),
            preset("No zero nulling", ibias=2e-5, l=5e-7, wpair=1e-5, wload=1e-5,
                   w6=4e-5, w7=1e-5, cc=2e-12, rz=1e-3, cl=2e-12),
            preset("Heavy load", ibias=2e-5, l=5e-7, wpair=1e-5, wload=1e-5,
                   w6=4e-5, w7=1e-5, cc=2e-12, rz=2000.0, cl=2e-11),
            preset("Under-compensated", ibias=2e-5, l=5e-7, wpair=1e-5, wload=1e-5,
                   w6=4e-5, w7=1e-5, cc=5e-13, rz=1e-3, cl=2e-12),
        ],
        "build": build_opamp_two_stage,
        "measure": measure_opamp_two_stage,
        # A real transistor amplifier: like the CS stage, no closed form in this
        # codebase predicts it, so nothing here pretends to check it.
        "checks": [],
        "readout": {
            "headline": metric("loop_gain_db", "open-loop gain", "db", "dB"),
            "stats": [
                metric("f_crossover", "unity-gain BW", "eng", "Hz"),
                metric("phase_margin", "phase margin", "deg"),
                metric("power", "power", "eng", "W"),
                metric("out_dc", "output DC", "eng", "V"),
            ],
            "markers": [{"key": "f_crossover", "label": "0dB"}],
        },
        "design": {
            "tunable": ["ibias", "wpair", "w6", "cc", "rz"],
            "seed": opamp_seed,
            "goals": [
                goal("loop_gain_db", "open-loop gain", ">=", "dB", 60.0),
                goal("f_crossover", "unity-gain BW", ">=", "Hz", 5e6),
                goal("phase_margin", "phase margin", ">=", "deg", 60.0),
                goal("power", "power", "<=", "W", 2e-4),
            ],
        },
    },

    "nfet_cs_amp": {
        "id": "nfet_cs_amp",
        "pdk": True,
        "name": "NFET amp (SKY130)",
        "analysis": "ac",
        "caption": "Figure 7: SKY130 NFET common-source amplifier, frequency response.",
        "timeout_s": runner.PDK_TIMEOUT_S,
        "params": [
            param("vdd", "VDD", "V", 1.8, 0.5, 1.8),
            param("vgs", "Vgs (gate bias)", "V", 0.9, 0.0, 1.8),
            param("w", "W", "m", 1e-6, 4.2e-7, 1e-4),
            param("l", "L", "m", 1.5e-7, 1.5e-7, 1e-5),
            param("rd", "RD", "\u03a9", 20000.0, 100.0, 1e7),
            param("cl", "CL", "F", 1e-12, 1e-15, 1e-9),
        ],
        "centre": cs_amp_pole,
        # "Heavy load" is the controlled experiment: identical to the first
        # preset in every parameter except CL. "Bottomed out" is the one that
        # fires the triode caution, on numbers ngspice produced.
        "presets": [
            preset("13 dB midband",
                   vdd=1.8, vgs=0.9, w=1e-6, l=1.5e-7, rd=20000.0, cl=1e-12),
            preset("Heavy load",
                   vdd=1.8, vgs=0.9, w=1e-6, l=1.5e-7, rd=20000.0, cl=2e-11),
            preset("Wider device",
                   vdd=1.8, vgs=0.8, w=4e-6, l=1.5e-7, rd=10000.0, cl=5e-13),
            preset("Low current",
                   vdd=1.8, vgs=0.75, w=1e-6, l=1.5e-7, rd=40000.0, cl=1e-12),
            preset("Long channel",
                   vdd=1.8, vgs=0.95, w=1e-6, l=1e-6, rd=100000.0, cl=1e-12),
            preset("Bottomed out",
                   vdd=1.8, vgs=1.2, w=1e-6, l=1.5e-7, rd=100000.0, cl=1e-12),
        ],
        "build": build_nfet_cs_amp,
        "measure": measure_nfet_cs_amp,
        # Deliberately empty.  Square law does not describe a 150 nm device, and
        # a check that is permanently tens of percent out would read as a fault
        # in the simulator rather than as the known limit of the closed form.
        # The operating point is shown instead, and it comes from ngspice too.
        "checks": [],
        "readout": {
            "headline": metric("midband_db", "midband gain", "db", "dB"),
            "stats": [
                metric("f3db", "bandwidth", "eng", "Hz"),
                metric("drain_current", "drain current", "eng", "A"),
                metric("drain_voltage", "drain voltage", "eng", "V"),
                metric("power", "power", "eng", "W"),
            ],
            "markers": [{"key": "f3db", "label": "f-3dB"}],
        },
    },
}

#: Display order for the UI.  Explicit so it does not depend on dict ordering.
CIRCUIT_ORDER = [
    "divider",
    "rc_lowpass",
    "rc_highpass",
    "rlc_bandpass",
    "inverting_amp",
    "twopole_amp",
    "nfet_cs_amp",
    "opamp_two_stage",
    "ota_5t",
]


def get_circuit(circuit_id):
    """Return one catalogue entry, or raise UnknownCircuitError."""
    try:
        return CIRCUITS[circuit_id]
    except KeyError:
        raise UnknownCircuitError(
            "Unknown circuit " + repr(circuit_id) + ". Known circuits: "
            + ", ".join(CIRCUIT_ORDER) + "."
        ) from None


def defaults(circuit_id):
    """The default parameter set for a circuit."""
    return {spec["key"]: spec["default"] for spec in get_circuit(circuit_id)["params"]}


def catalog():
    """The catalogue as plain JSON-ready data, with no callables in it."""
    listing = []
    for circuit_id in CIRCUIT_ORDER:
        circuit = CIRCUITS[circuit_id]
        listing.append({
            "id": circuit["id"],
            "name": circuit["name"],
            "analysis": circuit["analysis"],
            "caption": circuit["caption"],
            "params": [dict(spec) for spec in circuit["params"]],
            "presets": [
                {"label": item["label"], "params": dict(item["params"])}
                for item in circuit["presets"]
            ],
            "checks": [
                {key: value for key, value in item.items() if key != "formula"}
                for item in circuit["checks"]
            ],
            "readout": circuit["readout"],
            "design": (
                {
                    "tunable": list(circuit["design"]["tunable"]),
                    "goals": [dict(item) for item in circuit["design"]["goals"]],
                    "seeded": "seed" in circuit["design"],
                }
                if circuit.get("design") else None
            ),
            "pdk": bool(circuit.get("pdk")),
            # A circuit that declares a step testbench advertises what the
            # panel should show: no callables, same as everything else here.
            "floorplan": (
                {"caption": circuit["floorplan"]["caption"]}
                if circuit.get("floorplan") else None
            ),
            "datasheet": (
                {
                    "caption": circuit["datasheet"]["caption"],
                    "readout": [dict(item) for item
                                in circuit["datasheet"]["readout"]],
                }
                if circuit.get("datasheet") else None
            ),
            "step": (
                {
                    "caption": circuit["step"]["caption"],
                    "readout": [dict(item) for item in circuit["step"]["readout"]],
                }
                if circuit.get("step") else None
            ),
        })
    return listing


def analytic_values(circuit_id, params):
    """Evaluate every closed-form check for a circuit."""
    circuit = get_circuit(circuit_id)
    return {item["key"]: item["formula"](params) for item in circuit["checks"]}


def _timeout(circuit, default):
    """A circuit may claim a longer budget than the shared default.

    The SKY130 circuits need one: loading the model library costs 10 to 30 s
    before any solving starts.
    """
    return circuit.get("timeout_s") or default


def _run_dc(circuit, params, transform=None):
    netlist = circuit["build"](params)
    if transform is not None:
        netlist = transform(netlist)
    stdout = runner.run_netlist(
        netlist,
        timeout_s=_timeout(circuit, runner.DEFAULT_TIMEOUT_S),
    )
    return circuit["measure"](stdout, params)


def _reserve_data_path():
    """A unique name with no file at it.

    Reserving the name and then deleting the file is what lets the runner
    tell "ngspice wrote nothing" apart from "something was already there".
    """
    handle, path = tempfile.mkstemp(
        suffix=".data", prefix=runner.TEMP_PREFIX, dir=tempfile.gettempdir()
    )
    os.close(handle)
    try:
        os.unlink(path)
    except OSError:
        pass
    return path


def _run_ac(circuit, params, transform=None):
    fstart, fstop = sweep_range(
        circuit["centre"](params), circuit.get("decades")
    )

    # Most circuits write one data file and their builder takes a single
    # path. A circuit that declares "outputs" takes a list, always, even for
    # one file, and all of them come from a single sweep so its responses
    # cannot disagree about where the samples fell.
    declares = "outputs" in circuit
    count = circuit.get("outputs", 1)
    paths = [_reserve_data_path() for _ in range(count)]

    if declares:
        netlist = circuit["build"](params, fstart, fstop, paths)
    else:
        netlist = circuit["build"](params, fstart, fstop, paths[0])

    # PVT and Monte Carlo runs modify the finished netlist text: the corner
    # in the .lib line, the supply, the temperature. The circuit builders
    # stay ignorant of all of it.
    if transform is not None:
        netlist = transform(netlist)

    texts, stdout = runner.run_ac_multi(
        netlist,
        paths,
        timeout_s=_timeout(circuit, runner.AC_TIMEOUT_S),
        with_stdout=True,
    )
    bodes = [
        runner.compute_bode(runner.parse_wrdata_complex(text)) for text in texts
    ]

    return circuit["measure"](
        bodes if declares else bodes[0], params, stdout
    )


def build_netlist_preview(circuit_id, params):
    """The exact netlist these values produce, without running it.

    Data-file paths are shown as placeholder names, because the real ones are
    throwaway temp files chosen at run time.
    """
    circuit = get_circuit(circuit_id)
    values = dict(params)

    if circuit["analysis"] == "dc":
        return circuit["build"](values)

    fstart, fstop = sweep_range(
        circuit["centre"](values), circuit.get("decades")
    )
    count = circuit.get("outputs", 1)
    placeholders = [
        "response.data" if count == 1 else "response%d.data" % (index + 1)
        for index in range(count)
    ]
    if "outputs" in circuit:
        return circuit["build"](values, fstart, fstop, placeholders)
    return circuit["build"](values, fstart, fstop, placeholders[0])


#: Points kept for drawing the waveform. The measurement always uses every
#: point ngspice produced; this is only what travels to the browser.
WAVEFORM_POINTS = 900


class NoStepResponseError(CircuitInputError):
    """Raised when a circuit has no step testbench to run."""


def has_step(circuit_id):
    """True when this circuit declares a step response."""
    return "step" in get_circuit(circuit_id)


def _decimate(points, limit):
    """Thin a series for drawing, keeping the first and last sample.

    Plain stride sampling: the waveform is already smooth at the timestep
    the run used, and anything cleverer would be a picture of a filter
    rather than a picture of the output.
    """
    if len(points) <= limit:
        return [[t, v] for t, v in points]
    stride = len(points) / float(limit)
    kept = [points[int(i * stride)] for i in range(limit)]
    kept[-1] = points[-1]
    return [[t, v] for t, v in kept]


def run_step(circuit_id, params, transform=None):
    """Run one circuit's step response and return what it measured.

    The waveform comes back with the numbers, thinned for drawing, because
    a slew rate without the edge it came from is a number nobody can check.
    """
    circuit = get_circuit(circuit_id)
    step = circuit.get("step")
    if step is None:
        raise NoStepResponseError(
            "The circuit " + repr(circuit_id) + " has no step response. "
            "The two SKY130 amplifiers do; pick one of those."
        )

    values = dict(params)
    window = step["window"](values)
    paths = [_reserve_data_path()]
    netlist = step["build"](values, window, paths)
    if transform is not None:
        netlist = transform(netlist)

    texts, stdout = runner.run_data_netlist(
        netlist, paths, timeout_s=runner.TRAN_TIMEOUT_S, with_stdout=True
    )
    points = runner.parse_wrdata_real(texts[0])
    measured = measure_step_response(points, values, window)
    measured["waveform"] = _decimate(points, WAVEFORM_POINTS)
    return measured


class NoDatasheetError(CircuitInputError):
    """Raised when a circuit has no rejection testbench to run."""


def has_datasheet(circuit_id):
    """True when this circuit declares a rejection and range run."""
    return "datasheet" in get_circuit(circuit_id)


def run_datasheet(circuit_id, params, transform=None):
    """Measure rejection and range, and return the transfer curve with them.

    Four copies of the amplifier go into one deck so that a single library
    load answers four questions. The curve comes back for drawing, because
    a range without the curve it was read off is a number nobody can check.
    """
    circuit = get_circuit(circuit_id)
    sheet = circuit.get("datasheet")
    if sheet is None:
        raise NoDatasheetError(
            "The circuit " + repr(circuit_id) + " has no rejection testbench. "
            "The two SKY130 amplifiers do; pick one of those."
        )

    values = dict(params)
    paths = [_reserve_data_path() for _ in range(4)]
    netlist = sheet["build"](values, paths)
    if transform is not None:
        netlist = transform(netlist)

    texts = runner.run_data_netlist(
        netlist, paths, timeout_s=runner.DATASHEET_TIMEOUT_S
    )
    transfer = runner.parse_wrdata_real(texts[0])
    bodes = [
        runner.compute_bode(runner.parse_wrdata_complex(text))
        for text in texts[1:]
    ]

    measured = measure_datasheet(bodes, transfer, values)
    measured["transfer"] = _decimate(transfer, WAVEFORM_POINTS)
    return measured


class NoFloorplanError(CircuitInputError):
    """Raised when a circuit has no floorplan to compute."""


def has_floorplan(circuit_id):
    """True when this circuit can be floorplanned."""
    return "floorplan" in get_circuit(circuit_id)


def run_layout(circuit_id, params):
    """Area, interconnect, and the specs measured again with it loading them.

    Two simulations: the circuit as drawn on the schematic, and the same
    circuit with each net's wire capacitance hung on it. The difference is
    what the interconnect costs, and it is measured rather than asserted.
    """
    circuit = get_circuit(circuit_id)
    block = circuit.get("floorplan")
    if block is None:
        raise NoFloorplanError(
            "The circuit " + repr(circuit_id) + " has no floorplan. The two "
            "SKY130 amplifiers do; pick one of those."
        )

    values = dict(params)
    try:
        tech = layout.tech_constants()
    except layout.LayoutDataError as exc:
        raise NoFloorplanError(str(exc)) from None

    plan = layout.floorplan(block["devices"](values), tech)
    parasitics = layout.net_parasitics(plan, block["nets"], tech)

    clean = simulate(circuit_id, values)
    loaded = simulate(circuit_id, values,
                      transform=layout.parasitic_transform(parasitics))

    keys = [item["key"] for item in
            [circuit["readout"]["headline"]] + list(circuit["readout"]["stats"])]
    comparison = []
    for key in keys:
        before = clean.get(key)
        after = loaded.get(key)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        comparison.append({
            "key": key,
            "before": before,
            "after": after,
            "change": after - before,
        })

    # The geometry, in the format every layout tool reads. It is the same
    # placement the area was measured over, so the file and the number
    # cannot disagree.
    try:
        stream = layout.floorplan_gds(plan, name=circuit_id.upper())
        encoded = base64.b64encode(stream).decode("ascii")
    except layout.LayoutDataError:
        encoded = None

    return {
        "floorplan": plan,
        "gds_base64": encoded,
        "gds_bytes": len(stream) if encoded else 0,
        "parasitics": parasitics,
        "total_parasitic_f": sum(item["capacitance_f"]
                                 for item in parasitics.values()),
        "comparison": comparison,
        "tech": {name: tech[name] for name in sorted(tech)},
    }


def simulate(circuit_id, params, transform=None):
    """Run one catalogue circuit and return its measurements plus the checks.

    The returned dict is the measurement, with an "analytic" object alongside
    holding what each check expected.  Comparing them is the caller's job; this
    function never reconciles the two.

    transform, when given, edits the finished netlist text before it runs.
    The PVT and Monte Carlo machinery lives on this hook.
    """
    circuit = get_circuit(circuit_id)
    values = dict(params)

    if circuit["analysis"] == "dc":
        result = _run_dc(circuit, values, transform)
    else:
        result = _run_ac(circuit, values, transform)

    result["analytic"] = analytic_values(circuit_id, values)
    return result
