"""The workbench's server half: jobs, stores, and the notebook's reading.

server.py stays the router; the work the new panels ask for lives here.
Four long-running kinds -- a full characterization, a blame run, a sweep,
a corner autopsy -- share one job registry and one rule: one job per
circuit at a time. The rule is enforced here, where it cannot be raced,
and the refusal names the job that is in the way, so the page can say
"a characterization is already running" instead of queueing surprises.

Simulation counts on finished jobs are observed at the subprocess
boundary (runner.observing), never estimated, because a page that says
"about 20 simulations" before a run must say exactly what happened after
it.
"""

import re
import threading
import time
import traceback
import uuid

from spice import (autopsy, blame, charact, circuits, ledger, pins,
                   runner, triage)

JOBS = {}
LOCK = threading.Lock()
MAX_JOBS = 12

#: What each kind runs. Kept closed on purpose, like the ledger's kinds.
KINDS = ("charact", "blame", "sweep", "autopsy")


class Busy(Exception):
    """A job for this circuit is already running; the message names it."""


def snapshot(job):
    return {
        "job": job["job"],
        "circuit": job["circuit"],
        "kind": job["kind"],
        "status": job["status"],
        "stage": job["stage"],
        "result": job["result"] if job["status"] in ("done", "stopped")
        else None,
        "stored_id": job.get("stored_id"),
        "sims": job["sims"],
        "seconds": round(time.monotonic() - job["born"], 1),
        "error": job["error"],
    }


def running_job(circuit_id):
    """The running job for this circuit, if any. Callers hold LOCK."""
    for job in JOBS.values():
        if job["circuit"] == circuit_id and job["status"] == "running":
            return job
    return None


def start(circuit_id, params, kind, targets=None):
    """Start one background job, or refuse with Busy.

    The busy rule is per circuit and across kinds: two simulator-heavy
    jobs for the same circuit at once would interleave their ngspice
    runs and double every wait without telling anyone why.
    """
    if kind not in KINDS:
        raise ValueError("Unknown workbench job kind " + repr(kind) + ".")

    job = {
        "job": uuid.uuid4().hex[:12], "circuit": circuit_id, "kind": kind,
        "status": "running", "stage": "starting", "result": None,
        "sims": None, "error": None, "born": time.monotonic(),
        "stop": threading.Event(),
    }
    with LOCK:
        stuck = running_job(circuit_id)
        if stuck is not None:
            # The registry token is not a word a user should have to read.
            names = {"charact": "characterization", "blame": "sensitivity run",
                     "sweep": "bias sweep", "autopsy": "corner autopsy"}
            raise Busy("A %s for this circuit is already running. Wait "
                       "for it or stop it first."
                       % names.get(stuck["kind"], stuck["kind"]))
        JOBS[job["job"]] = job
        if len(JOBS) > MAX_JOBS:
            done = [k for k, j in JOBS.items() if j["status"] != "running"]
            for key in done[: len(JOBS) - MAX_JOBS]:
                del JOBS[key]

    def stage(text):
        with LOCK:
            job["stage"] = text

    def work():
        observer = runner.SimObserver(phase=kind)
        try:
            with runner.observing(observer):
                if kind == "charact":
                    found = charact.characterize(
                        circuit_id, params,
                        on_progress=lambda section, state:
                        stage(section + " " + state),
                        should_stop=job["stop"].is_set)
                    stored = charact.store(found)
                    with LOCK:
                        job["stored_id"] = stored
                elif kind == "blame":
                    found = blame.sensitivities(
                        circuit_id, params, targets=targets,
                        on_progress=stage,
                        should_stop=job["stop"].is_set)
                elif kind == "sweep":
                    found = triage.sweep(
                        circuit_id, params, on_progress=stage,
                        should_stop=job["stop"].is_set)
                else:
                    found = autopsy.run(
                        circuit_id, params, on_progress=stage,
                        should_stop=job["stop"].is_set)
            with LOCK:
                job["result"] = found
                job["sims"] = observer.count
                job["status"] = ("stopped" if job["stop"].is_set()
                                 else "done")
        except Exception as exc:  # noqa: BLE001 - worker boundary
            traceback.print_exc()
            with LOCK:
                job["error"] = str(exc)
                job["sims"] = observer.count
                job["status"] = "failed"

    threading.Thread(target=work, daemon=True).start()
    return job["job"]


def status(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        return snapshot(job) if job else None


def stop(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return False
        job["stop"].set()
        return True


# ---------------------------------------------------------------------------
# pins, measured server-side so a pin is always what ngspice said
# ---------------------------------------------------------------------------


def pin_now(circuit_id, params):
    """Measure this sizing now and pin exactly what was measured.

    The client never supplies the numbers: a pin is the server's own
    measurement or it is nothing.
    """
    values = circuits.defaults(circuit_id)
    values.update(params or {})
    measured = circuits.simulate(circuit_id, dict(values))
    keep = {key: value for key, value in measured.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)}
    return pins.pin(circuit_id, values, keep)


def pin_state(circuit_id):
    """The pin, its recent history, and where the history first broke."""
    entry = pins.load().get(circuit_id)
    records = pins.history(circuit_id)
    if entry is not None:
        # A sparkline across two different pins would invent a trend, so
        # history is cut to the active pin.
        records = [r for r in records
                   if r.get("pin_utc") == entry.get("pinned_utc")]
    return {
        "pinned": entry,
        "history": [{"when_utc": r.get("when_utc"), "ok": r.get("ok"),
                     "rows": r.get("rows")}
                    for r in records],
        "first_break": pins.first_break(records),
    }


# ---------------------------------------------------------------------------
# the notebook's reading of the ledger
# ---------------------------------------------------------------------------


def notebook_page(offset=0, limit=20):
    """Ledger runs, newest first, one summary row each.

    Damaged line counts are surfaced, not hidden: a truncated record is
    what a crash leaves behind, and the notebook is where it should show.
    """
    # Only run files: the pins history lives beside the ledger and wears
    # the same suffix, but a list of checks is not a run.
    run_name = re.compile(r"\d{8}-\d{6}-[0-9a-f]+\.jsonl$")
    paths = [path for path in reversed(ledger.runs())
             if run_name.search(path.replace("\\", "/"))]
    window = paths[offset:offset + limit]
    rows = []
    for path in window:
        loaded = ledger.read(path)
        records = loaded["records"]
        provenance = next((r for r in records
                           if r.get("kind") == "provenance"), {})
        results = [r for r in records if r.get("kind") == "result"]
        circuits_seen = sorted({r.get("circuit") for r in records
                                if r.get("circuit")})
        arms = sorted({r.get("arm") for r in records if r.get("arm")})
        rows.append({
            "run": provenance.get("run_id") or path.rsplit("\\", 1)[-1],
            "path_name": path.replace("\\", "/").rsplit("/", 1)[-1],
            "records": len(records),
            "damaged": loaded["damaged"],
            "sims": sum(1 for r in records if r.get("kind") == "sim"),
            "circuits": circuits_seen,
            "arms": arms,
            # A result names itself however its writer did: the comparison
            # arms carry arm/status, the studies carry what/verdict. A row
            # with neither says nothing and is left out, because a chip
            # reading "run: null" is noise wearing a badge.
            "results": [
                {"arm": r.get("arm") or r.get("what"),
                 "status": r.get("status") or r.get("verdict")}
                for r in results
                if (r.get("arm") or r.get("what"))
                and (r.get("status") or r.get("verdict"))
            ],
            "started_utc": (records[0].get("when_utc")
                            if records else None),
            "git": (provenance.get("git") or {}).get("commit"),
        })
    return {
        "rows": rows,
        "offset": offset,
        "total": len(paths),
        "more": offset + limit < len(paths),
    }
