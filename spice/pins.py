"""Pinned numbers: the regression test an analog circuit never had.

Software engineers pin behaviour in tests and a machine tells them when it
drifts. Analog engineers diff waveforms by eye. This module gives a
circuit the same deal: pin the numbers a known sizing measured, and any
later check re-runs that exact sizing and says which numbers moved.

The semantics are deliberately narrow. A pin freezes the sizing AND the
numbers together, and a check re-simulates the pinned sizing, not the
current one. What this catches is the stack drifting under the circuit:
a model library update, a simulator upgrade, an edit to the bench deck.
Comparing the current sizing against a stored run is a different question
and belongs to the compare view, not here.

Pins live beside the ledger, never in the project folder. Every check
appends one line to a history file, so the page can show the numbers over
time and point at the first check that broke.
"""

import io
import json
import os
import time

from . import circuits, ledger

#: A measured value may drift this fraction before the check fails.
#: Half a percent: ngspice at a fixed sizing is deterministic, so any
#: real drift is the stack changing, but wrdata prints finite digits.
DEFAULT_TOLERANCE = 0.005

_HISTORY_KEEP = 500


def _pins_path():
    return os.path.join(ledger.root(), "pins.json")


def _history_path():
    return os.path.join(ledger.root(), "pins-history.jsonl")


def load():
    """Every pinned circuit: {circuit: {sizing, expected, pinned_utc}}."""
    path = _pins_path()
    if not os.path.isfile(path):
        return {}
    with io.open(path, encoding="utf-8") as stream:
        return json.load(stream)


def _write(pins):
    directory = ledger.root()
    os.makedirs(directory, exist_ok=True)
    with io.open(_pins_path(), "w", encoding="utf-8",
                 newline="\n") as stream:
        json.dump(pins, stream, indent=1)


def pin(circuit_id, sizing, measured, tolerance=DEFAULT_TOLERANCE):
    """Freeze this sizing and its numbers as the expectation.

    Only plain numbers are pinned; curves and notes are not expectations.
    """
    expected = {key: value for key, value in measured.items()
                if isinstance(value, (int, float))
                and not isinstance(value, bool)}
    if not expected:
        raise ValueError("There is nothing numeric to pin. Run the "
                         "simulation first.")
    pins = load()
    pins[circuit_id] = {
        "sizing": dict(sizing),
        "expected": expected,
        "tolerance": tolerance,
        "pinned_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # What the numbers were measured with, so a later failure can say
        # whether the stack changed under the circuit.
        "provenance": ledger.provenance(),
    }
    _write(pins)
    return pins[circuit_id]


def unpin(circuit_id):
    pins = load()
    if circuit_id in pins:
        del pins[circuit_id]
        _write(pins)
        return True
    return False


def check(circuit_id):
    """Re-simulate the pinned sizing and compare against the pinned numbers.

    Every value is measured now, by ngspice, at the sizing that was pinned.
    The result names each metric that moved beyond tolerance. The check is
    appended to the history whatever the outcome, because a run of green
    checks is the record that makes a red one meaningful.
    """
    pins = load()
    entry = pins.get(circuit_id)
    if entry is None:
        raise KeyError("Nothing is pinned for " + repr(circuit_id) +
                       ". Run the simulation and pin its numbers first.")

    measured = circuits.simulate(circuit_id, dict(entry["sizing"]))
    tolerance = entry.get("tolerance", DEFAULT_TOLERANCE)

    rows = []
    for key, expected in entry["expected"].items():
        value = measured.get(key)
        if not isinstance(value, (int, float)):
            rows.append({"key": key, "expected": expected, "measured": None,
                         "drift": None, "ok": False,
                         "why": "the bench no longer reports this number"})
            continue
        scale = abs(expected) if expected else 1.0
        drift = (value - expected) / scale
        rows.append({"key": key, "expected": expected, "measured": value,
                     "drift": drift, "ok": abs(drift) <= tolerance})

    record = {
        "circuit": circuit_id,
        "when_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Which pin this check belongs to: history across a re-pin is two
        # histories, and a sparkline must never stitch them together.
        "pin_utc": entry.get("pinned_utc"),
        "ok": all(row["ok"] for row in rows),
        "rows": rows,
        "tolerance": tolerance,
        "provenance": ledger.provenance(),
    }
    _append_history(record)
    return record


def _append_history(record):
    directory = ledger.root()
    os.makedirs(directory, exist_ok=True)
    with io.open(_history_path(), "a", encoding="utf-8",
                 newline="\n") as stream:
        stream.write(json.dumps(record) + "\n")


def history(circuit_id, limit=50):
    """The most recent checks for one circuit, oldest first.

    Reads the whole file and keeps the tail; the file is capped by
    trimming on read when it grows past _HISTORY_KEEP records total.
    """
    path = _history_path()
    if not os.path.isfile(path):
        return []
    records = []
    with io.open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            records.append(record)

    if len(records) > _HISTORY_KEEP:
        keep = records[-_HISTORY_KEEP:]
        with io.open(path, "w", encoding="utf-8", newline="\n") as stream:
            for record in keep:
                stream.write(json.dumps(record) + "\n")
        records = keep

    mine = [r for r in records if r.get("circuit") == circuit_id]
    return mine[-limit:]


def first_break(records):
    """The first check that failed after a passing one, or None.

    The page highlights this row: it is where the stack changed.
    """
    previous_ok = None
    for index, record in enumerate(records):
        if record.get("ok") is False and previous_ok:
            return index
        previous_ok = record.get("ok")
    return None
