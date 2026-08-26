"""The testbenches: everything built around a circuit to measure it.

A topology on its own measures nothing. It needs a supply, a stimulus, a
servo to find its operating point, a sweep or a step or four copies of
itself driven four different ways, and a control block saying what to write
out. All of that is here, and none of it is the circuit.

The split matters because the same topology is measured several ways. The
two-stage op-amp appears in an open-loop sweep, a unity-buffer step, and a
four-copy rejection deck, and it is the same devices every time.
"""

import math

from .errors import BiasError, CircuitInputError
from .runner import _fmt
from .topologies import (
    _folded_cascode_core, _microns, _opamp_core, _ota_core, _twopole_stages,
    FC_W_BIAS, FC_W_TAIL,
    MACROMODEL_RP, NFET_MODEL,
    OPAMP_VCM, OPAMP_VDD, OPAMP_W5, OPAMP_W8,
)
from . import runner


#: Sweeps span this many decades either side of the circuit's centre frequency.
DECADES_EACH_SIDE = 3


#: Samples per decade for every AC sweep.
POINTS_PER_DECADE = 20


#: What ngspice can meaningfully sweep.
FREQ_MIN = 0.01


FREQ_MAX = 1e10


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


def build_folded_cascode(params, fstart, fstop, out_paths):
    """SKY130 folded cascode, measured open loop.

    The same servo the other two use: the loop is closed at DC through a
    huge inductor so the bias finds its own operating point, and opened
    above DC so what the sweep sees is the open loop. M2's gate is the
    inverting input, so that is where the feedback goes.
    """
    loop_path = out_paths[0]

    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
        "Vcm inp 0 DC " + _fmt(OPAMP_VCM, "vcm"),
    ] + _folded_cascode_core(params) + [
        "Lfb out inn 1e9",
        "Vs ac 0 DC 0 AC 1",
        "Cin ac inn 1e9",
        "Einv lg 0 0 out 1",
        ".nodeset v(out)=" + _fmt(OPAMP_VCM, "vcm")
        + " v(inn)=" + _fmt(OPAMP_VCM, "vcm"),
    ]

    return _netlist(
        "* Faradaem SKY130 folded cascode, open-loop response",
        devices, fstart, fstop, None,
        op_prints=["v(out)", "i(vdd)"],
        outputs=[(loop_path, "v(lg)")],
    )


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


def build_folded_cascode_step(params, window, out_paths):
    """The folded cascode as a unity buffer, hit with a step."""
    devices = [
        ".lib " + runner.find_sky130_lib() + " " + runner.SKY130_DEFAULT_CORNER,
        "Vdd vdd 0 DC " + _fmt(OPAMP_VDD, "vdd"),
        _pulse_source(window),
    ] + _folded_cascode_core(params, inverting="out") + [
        ".nodeset v(out)=" + _fmt(OPAMP_VCM - STEP_VOLTS / 2.0, "start"),
    ]

    return "\n".join(
        ["* Faradaem SKY130 folded cascode, unity buffer step response"]
        + devices
        + step_control_block(window, out_paths[0], op_prints=["v(out)"])
    ) + "\n"


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


def folded_cascode_step_window(params):
    """A folded cascode slews at whatever the cascode branch has left.

    The tail is twice the reference and each input device takes half of it,
    so what reaches the output is the folding current less that half.
    """
    reference = params["ibias"] / FC_W_BIAS
    available = reference * (params["wfold"] - FC_W_TAIL / 2.0)
    return _step_window(max(available, 1e-9) / params["cl"])


#: Points kept for drawing the waveform. The measurement always uses every
#: point ngspice produced; this is only what travels to the browser.
WAVEFORM_POINTS = 900
