"""The catalogue: one entry a circuit, and nothing that runs one.

This is data. Every circuit the app can simulate is one dict here, naming
the pieces that live in the other modules -- the deck to build, the
measurement to take, the closed form to compare against, the devices to
lay out -- and adding a circuit means adding an entry, never a route or a
branch in the server.

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
    step,           optional benches: the transient one, the four-copy
    datasheet       rejection deck, and the geometry
    floorplan
    design          optional: what a spec-driven search may tune

A check is a *check*. ngspice produces the measurement; the formula only
says what we expected, and disagreement is reported, never silently
reconciled.
"""

from .errors import UnknownCircuitError
from .benches import *             # noqa: F401,F403
from .measure import *             # noqa: F401,F403
from .topologies import *          # noqa: F401,F403
from . import runner


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
            "matched": [["M1", "M2"], ["M3", "M4"]],
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

    "folded_cascode": {
        "id": "folded_cascode",
        "pdk": True,
        "name": "Folded cascode (SKY130)",
        "analysis": "ac",
        "caption": "Figure 16: SKY130 folded cascode, open-loop response. One "
                   "stage, and most of the gain of two, because the cascodes "
                   "make the output resistance rather than a second stage "
                   "making the gain.",
        "timeout_s": runner.PDK_TIMEOUT_S,
        "outputs": 1,
        "decades": 4,
        "params": [
            param("ibias", "Ibias", "A", 20e-6, 5e-7, 2e-4),
            param("l", "L (all devices)", "m", 5e-7, 1.5e-7, 2e-6),
            param("wpair", "W1,2 (input pair)", "m", 2e-5, 4.2e-7, 1e-4),
            param("wfold", "W3,4 (folding sources)", "m", 1e-4, 4.2e-7, 1e-4),
            param("wcasc", "W6,7 (p-cascodes)", "m", 1e-4, 4.2e-7, 1e-4),
            param("cl", "CL (load)", "F", 2e-12, 1e-13, 1e-10),
        ],
        "floorplan": {
            "devices": folded_cascode_devices,
            # The pair, the folding sources, and both cascode pairs: every
            # place this topology relies on two devices being the same.
            "matched": [["M1", "M2"], ["M3", "M4"], ["M6", "M7"],
                        ["M9", "M10"], ["M11", "M12"]],
            "caption": "Figure 17: fourteen devices in a row, n-channel first "
                       "so the five p-channel devices share one well. Drawn "
                       "to scale, checked, and compared against the netlist.",
        },
        "step": {
            "build": build_folded_cascode_step,
            "window": folded_cascode_step_window,
            "caption": "Figure 18: the folded cascode as a unity buffer, hit "
                       "with a 0.3 V step. One stage driving the load, so "
                       "what is left in the cascode branch sets the slew.",
            "readout": [
                metric("slew_rate", "slew rate", "slew", "V/s"),
                metric("settling_time", "settling to 0.1%", "eng", "s"),
                metric("overshoot", "overshoot", "percent", None),
            ],
        },
        "centre": opamp_frame,
        "presets": [
            preset("Balanced", ibias=2e-5, l=5e-7, wpair=2e-5, wfold=1e-4,
                   wcasc=1e-4, cl=2e-12),
            # Not chosen by hand: the spec-driven search found this and it
            # was re-simulated on its own to confirm. 77.7 dB, 13.5 MHz,
            # 61 degrees and 78 microwatts, which is twenty-six decibels
            # more gain than the balanced sizing at a third of the power.
            preset("High gain", ibias=1e-5, l=5e-7, wpair=1e-5, wfold=5e-5,
                   wcasc=5e-5, cl=2e-12),
            preset("Low power", ibias=6e-6, l=5e-7, wpair=2e-5, wfold=1e-4,
                   wcasc=1e-4, cl=2e-12),
            preset("Heavy load", ibias=2e-5, l=5e-7, wpair=2e-5, wfold=1e-4,
                   wcasc=1e-4, cl=2e-11),
        ],
        "build": build_folded_cascode,
        "measure": measure_folded_cascode,
        "design": {
            # Bias is what trades gain against speed in this topology, and
            # the two pair widths set how much of each the bias buys.
            "tunable": ["ibias", "wpair", "wfold", "wcasc"],
            "seed": folded_cascode_seed,
            "goals": [
                goal("loop_gain_db", "open-loop gain", ">=", "dB", 45.0),
                goal("f_crossover", "unity-gain BW", ">=", "Hz", 1e7),
                goal("phase_margin", "phase margin", ">=", "deg", 60.0),
                goal("power", "power", "<=", "W", 3e-4),
            ],
        },
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
            # The input pair and the mirror load. A differential pair whose
            # halves sample the process gradient at two different points
            # shows it at the output as an offset the schematic never
            # predicted, so these are drawn common centroid.
            "matched": [["M1", "M2"], ["M3", "M4"]],
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
    "folded_cascode",
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
