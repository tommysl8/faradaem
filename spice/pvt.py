"""Process, voltage and temperature corners, and Monte Carlo mismatch.

A design that works at the typical corner, room temperature and a nominal
supply has been demonstrated once. This module asks the harder questions: the
same circuit is rerun with the netlist text edited for each condition, the
corner in the .lib line, the supply value, the temperature card, and for
Monte Carlo the PDK's mismatch statistics under a changing seed. Every number
still comes from ngspice; this module only arranges the runs and reports the
spread.

Only the SKY130 circuits carry process corners, so only they can be run here.
"""

from __future__ import annotations

import math
import re

from . import circuits

#: The corner set one PVT suite runs, each with a label the UI shows.
#: Five process corners at nominal conditions, the four supply and
#: temperature extremes at typical process, and the two classic cross
#: corners. Eleven runs; on a PDK circuit expect about three minutes.
PVT_CONDITIONS = [
    {"label": "tt nominal", "corner": "tt", "vdd": 1.8, "temp": 27},
    {"label": "ss", "corner": "ss", "vdd": 1.8, "temp": 27},
    {"label": "ff", "corner": "ff", "vdd": 1.8, "temp": 27},
    {"label": "sf", "corner": "sf", "vdd": 1.8, "temp": 27},
    {"label": "fs", "corner": "fs", "vdd": 1.8, "temp": 27},
    {"label": "low vdd, cold", "corner": "tt", "vdd": 1.62, "temp": -40},
    {"label": "low vdd, hot", "corner": "tt", "vdd": 1.62, "temp": 125},
    {"label": "high vdd, cold", "corner": "tt", "vdd": 1.98, "temp": -40},
    {"label": "high vdd, hot", "corner": "tt", "vdd": 1.98, "temp": 125},
    {"label": "worst slow", "corner": "ss", "vdd": 1.62, "temp": 125},
    {"label": "worst fast", "corner": "ff", "vdd": 1.98, "temp": -40},
]

#: The mismatch library section Monte Carlo samples. The PDK's bare "mc"
#: section is incomplete (it carries no device models), so mismatch at the
#: typical process corner is what a Monte Carlo run means here.
MC_SECTION = "tt_mm"

#: How many mismatch samples one Monte Carlo run takes by default.
MC_DEFAULT_RUNS = 20
MC_MIN_RUNS = 4
MC_MAX_RUNS = 60

_LIB_LINE = re.compile(r"^(\.lib .*sky130\.lib\.spice) tt$", re.M)
_VDD_LINE = re.compile(r"^Vdd vdd 0 DC .*$", re.M)


class PvtError(ValueError):
    """A robustness request that cannot run. Maps to HTTP 400."""


def supported(circuit_id):
    return bool(circuits.get_circuit(circuit_id).get("pdk"))


def require_supported(circuit_id):
    if not supported(circuit_id):
        raise PvtError(
            "Circuit " + repr(circuit_id) + " is not built on the PDK, so it "
            "has no process corners to run. Pick one of the SKY130 circuits."
        )


def make_transform(corner=None, vdd=None, temp=None, seed=None):
    """A netlist text edit for one condition.

    The edits are anchored on lines every PDK builder emits: the .lib line
    with the tt corner, and the Vdd source. Temperature and seed are new
    cards inserted ahead of the control block.
    """
    def transform(netlist):
        text = netlist
        if corner and corner != "tt":
            text, count = _LIB_LINE.subn(r"\1 " + corner, text, count=1)
            if count != 1:
                raise PvtError(
                    "The netlist carries no tt library line to retarget, so "
                    "a corner cannot be applied to this circuit."
                )
        if vdd is not None:
            text, count = _VDD_LINE.subn(
                "Vdd vdd 0 DC " + repr(float(vdd)), text, count=1
            )
            if count != 1:
                raise PvtError(
                    "The netlist carries no Vdd source line to retarget, so "
                    "a supply condition cannot be applied to this circuit."
                )
        extra = []
        if temp is not None:
            extra.append(".temp " + repr(float(temp)))
        if seed is not None:
            extra.append(".options seed=" + str(int(seed)))
        if extra:
            text = text.replace(".control", "\n".join(extra) + "\n.control", 1)
        return text

    return transform


def _watched_keys(circuit):
    """The measured numbers a robustness table reports: the goal keys when a
    design block exists, the readout keys otherwise."""
    block = circuit.get("design")
    if block:
        return [item["key"] for item in block["goals"]]
    keys = [circuit["readout"]["headline"]["key"]]
    keys.extend(stat["key"] for stat in circuit["readout"]["stats"])
    return keys


def run_pvt(circuit_id, params, on_each=None, should_stop=None):
    """The PVT suite: one simulation per condition, worst case per metric.

    A condition where the circuit cannot be measured is reported with its
    error rather than ending the suite: a corner that breaks the bias is a
    finding, not a crash.
    """
    require_supported(circuit_id)
    circuit = circuits.get_circuit(circuit_id)
    keys = _watched_keys(circuit)
    goals = {item["key"]: item for item in (circuit.get("design") or {}).get("goals", [])}

    rows = []
    for condition in PVT_CONDITIONS:
        if should_stop is not None and should_stop():
            break
        transform = make_transform(
            condition["corner"], condition["vdd"], condition["temp"]
        )
        row = {"label": condition["label"], "corner": condition["corner"],
               "vdd": condition["vdd"], "temp": condition["temp"]}
        try:
            measured = circuits.simulate(circuit_id, dict(params), transform)
            row["measured"] = {key: measured[key] for key in keys}
            row["error"] = None
        except Exception as exc:  # noqa: BLE001 - a failing corner is a result
            row["measured"] = None
            row["error"] = str(exc).splitlines()[0]
        rows.append(row)
        if on_each is not None:
            on_each(row)

    worst = {}
    for key in keys:
        values = [row["measured"][key] for row in rows if row["measured"]]
        if not values:
            continue
        goal = goals.get(key)
        if goal and goal["op"] == "<=":
            value = max(values)
        elif goal:
            value = min(values)
        else:
            value = min(values)
        at = next(row["label"] for row in rows
                  if row["measured"] and row["measured"][key] == value)
        worst[key] = {"value": value, "at": at}

    return {"rows": rows, "worst": worst, "keys": keys}


def run_monte_carlo(circuit_id, params, runs=MC_DEFAULT_RUNS,
                    on_each=None, should_stop=None):
    """Mismatch Monte Carlo at the typical corner: mean and spread per metric.

    Each sample reruns the whole circuit with the PDK's mismatch statistics
    under a different seed. Sigma is the sample standard deviation.
    """
    require_supported(circuit_id)
    if not MC_MIN_RUNS <= runs <= MC_MAX_RUNS:
        raise PvtError(
            "Monte Carlo runs must be between " + str(MC_MIN_RUNS) + " and "
            + str(MC_MAX_RUNS) + "."
        )
    circuit = circuits.get_circuit(circuit_id)
    keys = _watched_keys(circuit)

    rows = []
    for index in range(runs):
        if should_stop is not None and should_stop():
            break
        transform = make_transform(corner=MC_SECTION, seed=index + 1)
        row = {"seed": index + 1}
        try:
            measured = circuits.simulate(circuit_id, dict(params), transform)
            row["measured"] = {key: measured[key] for key in keys}
            row["error"] = None
        except Exception as exc:  # noqa: BLE001 - a failing sample is a result
            row["measured"] = None
            row["error"] = str(exc).splitlines()[0]
        rows.append(row)
        if on_each is not None:
            on_each(row)

    stats = {}
    for key in keys:
        values = [row["measured"][key] for row in rows if row["measured"]]
        if not values:
            continue
        mean = sum(values) / len(values)
        if len(values) > 1:
            sigma = math.sqrt(
                sum((v - mean) ** 2 for v in values) / (len(values) - 1)
            )
        else:
            sigma = 0.0
        stats[key] = {
            "mean": mean, "sigma": sigma,
            "min": min(values), "max": max(values),
            "samples": len(values),
        }

    return {"rows": rows, "stats": stats, "keys": keys}
