"""Corner autopsy: which transistor gave up, at which corner, by how much.

A corner fails and the traditional next move is an afternoon in a
waveform viewer. But the question has a short answer: some device that
had saturation headroom at the typical corner lost it when the process,
the supply or the temperature moved. ngspice knows both numbers for
every transistor -- vds, and the vdsat the model computed -- so the
autopsy asks it directly, at every corner, and reports the difference.

Headroom is |vds| - |vdsat|, which is correct in either sign
convention a model may use: the SKY130 p-channel primitives hand back
magnitude-positive values (a diode-tied PMOS reads vds = +1.06 V,
vdsat = +0.12 V, measured), while classical conventions report both
negative. The magnitude form gives a positive margin to a saturated
device under both, which was verified against the diode-connected
mirror input, a device that is saturated by construction.
The numbers come from the model card the foundry
wrote, through the simulator, at the operating point of the whole
circuit. Nothing here decides what "should" be saturated; it reports
what is. A device whose model exposes no vdsat is reported as exactly
that, never as zero.

One simulation per corner: the same eleven conditions the PVT suite
runs, so the autopsy lines up row for row with the table that prompted
it.
"""

import re

from . import circuits, pvt, runner

_PRINT = re.compile(
    r"@m\.x(?P<device>\w+)\.m(?P<model>\w+)\[(?P<what>vds|vdsat)\]"
    r"\s*=\s*(?P<value>[-+0-9.eE]+)")

#: An X-card in the deck: instance name, then nets, then the model.
_XCARD = re.compile(r"^X(?P<name>\w+)\s+.*?(?P<model>sky130_fd_pr__\w+)",
                    re.MULTILINE)


def _instances(netlist_head):
    """Device name -> model, read from the deck itself, never guessed.

    The deck is the one place the instance names are guaranteed to match
    what ngspice will accept in an @m.x...m... vector."""
    return {m.group("name").upper(): m.group("model")
            for m in _XCARD.finditer(netlist_head)}


def _op_deck(circuit_id, values):
    """The bench deck with its control block swapped for op prints.

    The bench netlist already biases the circuit exactly as it is
    measured -- feedback closed at DC, the same sources -- so the
    operating point here is the operating point the measurements were
    taken at, not a convenient approximation of it. Returns the deck and
    the instances it prints, read from the deck itself.
    """
    text = circuits.build_netlist_preview(circuit_id, values)
    head = text[:text.index(".control")]
    instances = _instances(head)

    lines = [".control", "op"]
    for name, model in instances.items():
        vector = "@m.x%s.m%s" % (name.lower(), model.lower())
        lines.append("print %s[vds]" % vector)
        lines.append("print %s[vdsat]" % vector)
    lines.extend(["quit", ".endc", ".end", ""])
    return head + "\n".join(lines), instances


def _parse(stdout):
    found = {}
    for match in _PRINT.finditer(stdout):
        name = match.group("device").upper()
        slot = found.setdefault(name, {})
        slot[match.group("what")] = float(match.group("value"))
    return found


def run(circuit_id, params, on_progress=None, should_stop=None):
    """Measure per-device headroom at every PVT condition.

    A corner where the circuit cannot be simulated is reported with its
    error, exactly as the PVT suite reports it: a bias that collapses at
    ss and 1.62 V is the autopsy's most important row, not a crash.
    """
    pvt.require_supported(circuit_id)
    values = circuits.defaults(circuit_id)
    values.update(params or {})
    deck_text, instances = _op_deck(circuit_id, values)
    kinds = {name: item["kind"] for name, item
             in circuits.circuit_devices(circuit_id, values).items()}

    rows = []
    for condition in pvt.PVT_CONDITIONS:
        if should_stop is not None and should_stop():
            break
        if on_progress is not None:
            on_progress(condition["label"])
        transform = pvt.make_transform(
            condition["corner"], condition["vdd"], condition["temp"])
        deck = transform(deck_text)
        row = {"label": condition["label"], "corner": condition["corner"],
               "vdd": condition["vdd"], "temp": condition["temp"]}
        try:
            stdout = runner.run_netlist(
                deck, timeout_s=runner.PDK_TIMEOUT_S)
            measured = _parse(stdout)
            row["devices"] = {}
            for name in instances:
                slot = measured.get(name, {})
                row["devices"][name] = {
                    "vds": slot.get("vds"),
                    "vdsat": slot.get("vdsat"),
                    "headroom": headroom_of(slot, kinds.get(name)),
                }
            # A vector the model did not hand back is a fact the table
            # must show, never a blank that reads as fine.
            row["missing"] = [name for name in instances
                              if name not in measured]
            row["error"] = None
        except Exception as exc:  # noqa: BLE001 - a collapsed corner is a finding
            row["devices"] = None
            row["error"] = str(exc).splitlines()[0]
        rows.append(row)

    return {
        "circuit": circuit_id,
        "device_order": list(instances),
        "rows": rows,
        "sims": len(rows),
        "tightest": tightest(rows),
    }


def headroom_of(slot, kind):
    """Saturation margin as |vds| - |vdsat|, or None when not measurable.

    The magnitude form is convention-proof: it reads positive for a
    saturated device whether the model reports p-channel voltages as
    negative pairs or as magnitudes, and the SKY130 primitives do the
    latter. kind is accepted for the caller's clarity; the formula no
    longer needs it, and depending on it burned us once.
    """
    vds, vdsat = slot.get("vds"), slot.get("vdsat")
    if vds is None or vdsat is None:
        return None
    return abs(vds) - abs(vdsat)


def tightest(rows):
    """The device and corner with the least headroom, measured.

    This is the sentence's subject: on a pass it names the closest call,
    on a fail it names the collapse.
    """
    worst = None
    for row in rows:
        if not row.get("devices"):
            continue
        for name, slot in row["devices"].items():
            headroom = slot.get("headroom")
            if headroom is None:
                continue
            if worst is None or headroom < worst["headroom"]:
                worst = {"device": name, "label": row["label"],
                         "headroom": headroom}
    return worst


def sentence(found):
    """What a senior engineer would say after reading the table."""
    worst = found.get("tightest")
    if worst is None:
        return "No operating point could be read at any corner."
    mv = worst["headroom"] * 1000.0
    if mv < 0:
        return ("%s leaves saturation at %s: %.0f mV of headroom."
                % (worst["device"], worst["label"], mv))
    return ("Every device holds saturation at every corner. The tightest "
            "is %s with %.0f mV at %s."
            % (worst["device"], mv, worst["label"]))
