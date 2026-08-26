"""The full characterization of one sizing, collected once, stamped once.

Engineers lose days assembling characterization reports by hand: the gain
from one run, the corners from another, the layout verdicts from a third,
pasted into a document that is stale before it is finished. Everything in
that document is something this tool already measures. This module runs
the whole bench in one pass and returns one object: every number, where it
came from, and what it was measured with.

Nothing here computes a circuit value. Each section calls the measurement
path that already exists -- the bench, the step, the rejection testbench,
the PVT suite, the layout with its four verdicts, the foundry's deck --
and a section that fails is recorded with its error, because a corner
that breaks the bias is a finding, not a crash.

The result is written beside the ledger, never in the project folder,
following the precedent the comparison summary set. A stored
characterization remembers the sizing it measured, so a reader can always
tell whether it still describes the circuit on the bench.
"""

import io
import json
import os
import re
import secrets
import time

from . import circuits, ledger, pvt, signoff

#: The sections, in the order they run. Layout runs before signoff because
#: signoff checks the geometry layout produced.
SECTIONS = ("bench", "step", "sheet", "corners", "layout", "signoff")

#: Non-metric keys the bench returns that a datasheet table should not list.
_CARRIED = ("bode", "note", "analytic", "transfer")

_SAFE_ID = re.compile(r"^[a-z0-9_]+-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def store_root():
    """Where characterizations live: beside the ledger, never the project."""
    return os.path.join(ledger.root(), "charact")


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def characterize(circuit_id, params,
                 include=SECTIONS, on_progress=None, should_stop=None):
    """Run every measurement this tool has for one sizing.

    include picks sections; a circuit without a step or rejection bench
    simply reports the section as not applicable. on_progress(section,
    state) narrates; should_stop() aborts between sections, never inside a
    simulation.
    """
    circuit = circuits.get_circuit(circuit_id)
    values = circuits.defaults(circuit_id)
    values.update(params or {})

    result = {
        "circuit": circuit_id,
        "name": circuit["name"],
        "sizing": values,
        "provenance": ledger.provenance(),
        "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sections": {},
    }

    def tell(section, state):
        if on_progress is not None:
            on_progress(section, state)

    def wants(section):
        if section not in include:
            return False
        if should_stop is not None and should_stop():
            result["stopped_early"] = True
            return False
        return True

    def run_section(section, runner_fn):
        if not wants(section):
            return
        tell(section, "running")
        try:
            payload = runner_fn()
            result["sections"][section] = {"ran": True, "data": payload}
            tell(section, "done")
        except Exception as exc:  # noqa: BLE001 - a failing section is a finding
            result["sections"][section] = {
                "ran": False, "error": str(exc).splitlines()[0]}
            tell(section, "failed")

    def bench():
        measured = circuits.simulate(circuit_id, dict(values))
        keep = {key: value for key, value in measured.items()
                if key not in _CARRIED}
        return {"measured": keep,
                "note": measured.get("note"),
                "margins": margins_of(circuit, keep)}

    run_section("bench", bench)

    if circuits.has_step(circuit_id):
        run_section("step", lambda: _slim_step(
            circuits.run_step(circuit_id, dict(values))))
    elif "step" in include:
        result["sections"]["step"] = {"ran": False,
                                      "why": "no step testbench"}

    if circuits.has_datasheet(circuit_id):
        run_section("sheet", lambda: _slim_sheet(
            circuits.run_datasheet(circuit_id, dict(values))))
    elif "sheet" in include:
        result["sections"]["sheet"] = {"ran": False,
                                       "why": "no rejection testbench"}

    def corners():
        found = pvt.run_pvt(circuit_id, dict(values),
                            should_stop=should_stop)
        return {"rows": found["rows"], "worst": found["worst"],
                "keys": found["keys"]}

    if pvt.supported(circuit_id):
        run_section("corners", corners)
    elif "corners" in include:
        result["sections"]["corners"] = {"ran": False,
                                         "why": "no corner suite"}

    if circuits.has_floorplan(circuit_id):
        run_section("layout", lambda: _slim_layout(
            circuits.run_layout(circuit_id, dict(values))))

        def deck():
            shapes = circuits.layout_shapes(circuit_id, dict(values))
            return signoff.run_drc(shapes, circuit_id)

        if signoff.available():
            run_section("signoff", deck)
        elif "signoff" in include:
            result["sections"]["signoff"] = {
                "ran": False, "why": "KLayout is not installed here"}
    elif "layout" in include:
        result["sections"]["layout"] = {"ran": False, "why": "no floorplan"}
        result["sections"]["signoff"] = {"ran": False, "why": "no floorplan"}

    return result


def margins_of(circuit, measured, targets=None):
    """Each design goal, its measured value, and the margin, signed.

    Margin is relative and positive means met, the same convention the
    experiment uses. Reported per goal so a table can say which one binds.
    targets overrides the registry's default target per goal key; without
    it the circuit is measured against its own published numbers.
    """
    goals = (circuit.get("design") or {}).get("goals", [])
    out = []
    for goal in goals:
        value = measured.get(goal["key"])
        if not isinstance(value, (int, float)):
            continue
        target = (targets or {}).get(goal["key"], goal["default"])
        if goal["op"] == ">=":
            margin = (value - target) / abs(target) if target else 0.0
        else:
            margin = (target - value) / abs(target) if target else 0.0
        out.append({"key": goal["key"], "label": goal.get("label", goal["key"]),
                    "op": goal["op"], "target": target,
                    "measured": value, "margin": margin,
                    "met": margin >= 0.0})
    if out:
        binding = min(out, key=lambda item: item["margin"])
        for item in out:
            item["binding"] = item is binding
    return out


def _slim_step(payload):
    return {key: value for key, value in payload.items()
            if key not in ("wave", "waves", "points", "transfer")}


def _slim_sheet(payload):
    return {key: value for key, value in payload.items()
            if key not in ("transfer",)}


def _slim_layout(payload):
    keep = {}
    for key in ("area_um2", "active_area_um2", "width_um", "height_um",
                "interconnect_f", "longest_route_um", "total_route_um",
                "wire_resistance_ohm"):
        if key in payload:
            keep[key] = payload[key]
    for verdict in ("drc", "lvs", "klvs"):
        found = payload.get(verdict)
        if isinstance(found, dict):
            keep[verdict] = {k: found[k] for k in
                             ("clean", "match", "ran", "reason")
                             if k in found}
    loaded = payload.get("loaded")
    if isinstance(loaded, dict):
        keep["loaded"] = {k: v for k, v in loaded.items()
                          if isinstance(v, (int, float, str, bool))}
    return keep


# ---------------------------------------------------------------------------
# the store: characterizations that outlive the page
# ---------------------------------------------------------------------------


def store(result):
    """Write one characterization; return its id.

    The id is circuit-stamp-token: sortable by eye, safe as a path
    component, unique enough for one machine.
    """
    ident = "%s-%s-%s" % (result["circuit"], _stamp(), secrets.token_hex(3))
    directory = store_root()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, ident + ".json")
    with io.open(path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(dict(result, id=ident), stream, indent=1)
    return ident


def load(ident):
    """One stored characterization, or None. Refuses malformed ids so a
    request can never walk the filesystem."""
    if not _SAFE_ID.match(ident or ""):
        return None
    path = os.path.join(store_root(), ident + ".json")
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as stream:
        return json.load(stream)


def listing(circuit_id=None):
    """Stored characterizations, newest first: id, circuit, when, verdict."""
    directory = store_root()
    if not os.path.isdir(directory):
        return []
    rows = []
    for name in sorted(os.listdir(directory), reverse=True):
        if not name.endswith(".json"):
            continue
        ident = name[:-5]
        if not _SAFE_ID.match(ident):
            continue
        if circuit_id and not ident.startswith(circuit_id + "-"):
            continue
        found = load(ident)
        if not found:
            continue
        bench = found["sections"].get("bench") or {}
        margins = (bench.get("data") or {}).get("margins") or []
        rows.append({
            "id": ident,
            "circuit": found["circuit"],
            "name": found.get("name"),
            "when_utc": found.get("when_utc"),
            "met": all(m["met"] for m in margins) if margins else None,
            "sections_ran": [key for key, sec in found["sections"].items()
                             if sec.get("ran")],
        })
    return rows


def describes(result, params):
    """Whether a stored characterization still measures this sizing.

    A datasheet for a different sizing shown next to the current circuit is
    the stale document this feature exists to kill, so the check is exact.
    """
    sizing = result.get("sizing") or {}
    if set(sizing) != set(params):
        return False
    for key, value in params.items():
        if sizing[key] != value:
            return False
    return True
