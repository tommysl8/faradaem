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

import math
import os
import tempfile

from . import runner
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

#: Presentation-layer sanity bands for the common-source bias.  These are not
#: device parameters and nothing is computed from them: they only decide which
#: caution the readout shows beside numbers ngspice already produced.
CS_TRIODE_VDS = 0.15
CS_WEAK_MARGIN = 0.10


class UnknownCircuitError(KeyError):
    """Raised when a circuit id is not in the catalogue."""


class BiasError(ValueError):
    """Raised when a bias leaves nothing measurable. Maps to HTTP 400.

    This is a bad input, not a broken simulator: the run itself succeeded and
    the message says which way to move the bias.
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


def sweep_range(centre_hz):
    """Frame a sweep symmetrically around a centre frequency, in decades."""
    if not math.isfinite(centre_hz) or centre_hz <= 0.0:
        raise ValueError(
            "The centre frequency must be finite and positive, got " + repr(centre_hz)
        )

    span = 10.0 ** DECADES_EACH_SIDE
    fstart = min(max(centre_hz / span, FREQ_MIN), FREQ_MAX)
    fstop = min(max(centre_hz * span, FREQ_MIN), FREQ_MAX)

    if fstop <= fstart:
        raise ValueError(
            "The centre frequency " + ("%g" % centre_hz) + " Hz falls outside the "
            "sweepable range " + ("%g" % FREQ_MIN) + " Hz to " + ("%g" % FREQ_MAX) + " Hz."
        )

    return fstart, fstop


def ac_control_block(fstart, fstop, out_path, op_prints=None):
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
        "wrdata " + str(out_path).replace("\\", "/") + " v(out)",
        "quit",
        ".endc",
        ".end",
    ])

    return lines


def _netlist(title, devices, fstart, fstop, out_path, op_prints=None):
    return "\n".join(
        [title] + devices + ac_control_block(fstart, fstop, out_path, op_prints)
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

CIRCUITS = {
    "divider": {
        "id": "divider",
        "name": "DC divider",
        "analysis": "dc",
        "caption": "Figure 1 — Resistive divider, DC operating point.",
        "params": [
            param("vdd", "VDD", "V", 5.0, -1000.0, 1000.0),
            param("r1", "R1", "Ω", 10000.0, 1e-3, 1e12),
            param("r2", "R2", "Ω", 10000.0, 1e-3, 1e12),
        ],
        "presets": [
            preset("Half rail", vdd=5.0, r1=10000.0, r2=10000.0),
            preset("3.3 V from 5 V", vdd=5.0, r1=4700.0, r2=9100.0),
            preset("10:1 attenuator", vdd=10.0, r1=9000.0, r2=1000.0),
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
        "caption": "Figure 2 — RC low-pass, frequency response.",
        "params": [
            param("r", "R", "Ω", 1000.0, 1e-3, 1e12),
            param("c", "C", "F", 1.59e-7, 1e-15, 1.0),
        ],
        "centre": rc_corner,
        "presets": [
            preset("1 kHz corner", r=1000.0, c=1.59e-7),
            preset("10 kHz corner", r=1000.0, c=1.59e-8),
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
        "caption": "Figure 3 — RC high-pass, frequency response.",
        "params": [
            param("r", "R", "Ω", 1000.0, 1e-3, 1e12),
            param("c", "C", "F", 1.59e-7, 1e-15, 1.0),
        ],
        "centre": rc_corner,
        "presets": [
            preset("1 kHz corner", r=1000.0, c=1.59e-7),
            preset("100 Hz corner", r=1000.0, c=1.59e-6),
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
        "caption": "Figure 4 — Series RLC band-pass, frequency response.",
        "params": [
            param("r", "R", "Ω", 10.0, 1e-3, 1e12),
            param("l", "L", "H", 1e-3, 1e-12, 1e3),
            param("c", "C", "F", 1e-6, 1e-15, 1.0),
        ],
        "centre": rlc_centre,
        "presets": [
            preset("Q = 1", r=31.623, l=1e-3, c=1e-6),
            preset("Q = 3.2", r=10.0, l=1e-3, c=1e-6),
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
        "caption": "Figure 5 — Inverting amplifier, closed-loop response.",
        "params": [
            param("rin", "Rin", "Ω", 1000.0, 1.0, 1e9),
            param("rf", "Rf", "Ω", 10000.0, 1.0, 1e9),
            param("a0", "A0 (open-loop gain)", "", 1e5, 10.0, 1e9),
            param("gbw", "GBW", "Hz", 1e6, 1.0, 1e10),
        ],
        "centre": amp_bandwidth,
        "presets": [
            preset("Gain 10x", rin=1000.0, rf=10000.0, a0=1e5, gbw=1e6),
            preset("Gain 100x", rin=1000.0, rf=100000.0, a0=1e5, gbw=1e6),
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

    "nfet_cs_amp": {
        "id": "nfet_cs_amp",
        "name": "NFET amp (SKY130)",
        "analysis": "ac",
        "caption": "Figure 6 \u2014 SKY130 NFET common-source amplifier, frequency response.",
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
        "presets": [
            preset("13 dB midband",
                   vdd=1.8, vgs=0.9, w=1e-6, l=1.5e-7, rd=20000.0, cl=1e-12),
            preset("Wider device",
                   vdd=1.8, vgs=0.8, w=4e-6, l=1.5e-7, rd=10000.0, cl=5e-13),
            preset("Low current",
                   vdd=1.8, vgs=0.75, w=1e-6, l=1.5e-7, rd=40000.0, cl=1e-12),
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
    "nfet_cs_amp",
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


def _run_dc(circuit, params):
    stdout = runner.run_netlist(
        circuit["build"](params),
        timeout_s=_timeout(circuit, runner.DEFAULT_TIMEOUT_S),
    )
    return circuit["measure"](stdout, params)


def _run_ac(circuit, params):
    fstart, fstop = sweep_range(circuit["centre"](params))

    # Reserve a unique name, then remove the file, so run_ac_netlist can tell
    # whether ngspice actually wrote any data.
    handle, out_path = tempfile.mkstemp(
        suffix=".data", prefix=runner.TEMP_PREFIX, dir=tempfile.gettempdir()
    )
    os.close(handle)
    try:
        os.unlink(out_path)
    except OSError:
        pass

    netlist = circuit["build"](params, fstart, fstop, out_path)
    text, stdout = runner.run_ac_netlist(
        netlist,
        out_path,
        timeout_s=_timeout(circuit, runner.AC_TIMEOUT_S),
        with_stdout=True,
    )
    bode = runner.compute_bode(runner.parse_wrdata_complex(text))

    return circuit["measure"](bode, params, stdout)


def simulate(circuit_id, params):
    """Run one catalogue circuit and return its measurements plus the checks.

    The returned dict is the measurement, with an "analytic" object alongside
    holding what each check expected.  Comparing them is the caller's job; this
    function never reconciles the two.
    """
    circuit = get_circuit(circuit_id)
    values = dict(params)

    if circuit["analysis"] == "dc":
        result = _run_dc(circuit, values)
    else:
        result = _run_ac(circuit, values)

    result["analytic"] = analytic_values(circuit_id, values)
    return result
