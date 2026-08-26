"""The numbers: what was measured, and what was expected.

Two kinds of thing live here and they belong together. The measure
functions turn what ngspice wrote into the response the UI reads. The
formulas say what a textbook would have predicted for the same circuit.

They are kept side by side because the whole point is the comparison, and
kept strictly apart in what they do: ngspice produces the measurement, the
formula only says what we expected, and where they disagree that is
reported rather than quietly reconciled. No formula here is ever
substituted for a measurement.
"""

import math

from .errors import BiasError, CircuitInputError
from .benches import (
    MIN_USABLE_GAIN_DB, POINTS_PER_DECADE, STEP_VOLTS, SWEEP_STEP_V,
)
from .topologies import (
    CS_TRIODE_VDS, CS_WEAK_MARGIN, MACROMODEL_RP,
    OPAMP_RAIL_MARGIN, OPAMP_VCM, OPAMP_VDD, OPAMP_W5, OPAMP_W8,
    SKY130_MAX_WIDTH_M,
)
from . import runner


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


def folded_cascode_seed(targets, params):
    """A launch point for the folded cascode, from measured behaviour.

    Three relationships, measured 2026-08-25 across a bias decade rather
    than assumed:

        power      about 6.4 x Ibias x the supply, steady to a few percent
        gain       falls about 25 dB per decade of bias: 59.5 dB at 10 uA,
                   51.5 at 20, 44.6 at 40
        bandwidth  rises about as the 0.7 power of bias, 18 to 49 MHz over
                   the same range

    So bias is what trades gain against speed here, and the seed sets it
    from whichever target is tightest. Heuristics only: nothing from here
    is ever reported, and the iterator does the real work against ngspice.
    """
    seeded = dict(params)

    # Start from the power budget, which is the one hard ceiling.
    budget = targets.get("power")
    bias = (budget / (6.4 * OPAMP_VDD)) if budget else params["ibias"]

    # Then move it for whichever of the two conflicting targets is asking
    # for more than the base sizing gives.
    gain_target = targets.get("loop_gain_db")
    if gain_target:
        # 51.5 dB at 20 uA, about 25 dB per decade against bias.
        wanted = 20e-6 * (10.0 ** ((51.5 - gain_target) / 25.0))
        bias = min(bias, wanted)

    speed_target = targets.get("f_crossover")
    if speed_target:
        # 32.4 MHz at 20 uA, rising as bias to the 0.7.
        wanted = 20e-6 * ((speed_target / 32.4e6) ** (1.0 / 0.7))
        bias = max(bias, wanted)

    seeded["ibias"] = min(max(bias, 5e-7), 2e-4)
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


#: The folded cascode is measured the way the OTA is, because it is the
#: same measurement: one stage, one servo, four numbers off one bode. A
#: second copy of that arithmetic would be a second thing to keep right.
measure_folded_cascode = measure_ota_5t
