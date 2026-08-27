"""Faradaem local web server -- standard library only.

Serves four pages (simulator, manual, about, changelog), their static assets, and
the simulation API:

    GET  /api/circuits   the catalogue, so the UI renders its forms from data
    POST /api/simulate   run any catalogued circuit
    POST /simulate       legacy DC divider, kept byte-compatible
    POST /simulate_ac    legacy RC low-pass sweep, kept byte-compatible

The server knows nothing about any particular circuit.  Topology, sweep
framing, measurement and closed-form checks all live in spice.circuits, so a
new circuit is a catalogue entry and never a new route.

Nothing here computes a circuit value.  The analytic numbers returned alongside
the simulated ones are *checks*, clearly labelled as such in the UI, never
substitutes for the simulator.

GET is served from a strict whitelist, ROUTES.  A request path is only ever
used as a dict key -- it is never joined onto the filesystem -- so there is no
path traversal surface at all.

Routing and request validation are both pure functions (resolve_route,
validate_simulate_request) so they can be tested directly rather than only
through a live socket.
"""

from __future__ import annotations

import json
import os
import math
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import doctor as doctor_checks
import workbench
from spice import (autopsy, charact, circuits, design, llm, packet, pins,
                   pvt, signoff, strategist, triage)
from spice.runner import NgspiceNotFoundError, NgspiceRunError, PdkNotFoundError

#: Errors from a simulation attempt that map to HTTP 500 rather than a crash.
#: A missing PDK belongs here: the request was fine, the machine is not set up.
SIMULATION_ERRORS = (
    NgspiceNotFoundError,
    NgspiceRunError,
    PdkNotFoundError,
    ValueError,
)

#: Inputs the circuit cannot be run at map to 400, not 500: a bias that leaves
#: nothing to measure, a combination that puts the sweep outside what ngspice
#: can cover, a loop with no crossover. CircuitInputError is the base of all of
#: them, and it subclasses ValueError, so it must be caught before
#: SIMULATION_ERRORS.
INPUT_ERRORS = (circuits.CircuitInputError,)

#: Design jobs live in memory: id -> snapshot dict. A job survives its HTTP
#: request because the search takes real simulator time; the browser polls.
JOBS = {}
JOBS_LOCK = threading.Lock()

#: How many finished jobs are kept before the oldest are dropped.
MAX_JOBS = 12

#: Iteration budget bounds for one design job.
MIN_DESIGN_EVALS = 4
MAX_DESIGN_EVALS = 120
DEFAULT_DESIGN_EVALS = 40

#: Strategist sessions, same in-memory pattern as design jobs.
ADVISE_JOBS = {}
ADVISE_LOCK = threading.Lock()
MAX_ADVISE_JOBS = 8

#: One request to the strategist, at most this long.
MAX_ADVISE_CHARS = 4000

#: Robustness jobs: PVT suites and Monte Carlo runs, same job pattern.
ROBUST_JOBS = {}
ROBUST_LOCK = threading.Lock()
MAX_ROBUST_JOBS = 8

HOST = "127.0.0.1"

#: The port, overridable so a second copy can run beside a first without
#: either of them having to be stopped.
PORT = int(os.environ.get("FARADAEM_PORT", "8000"))

PROJECT_ROOT = Path(__file__).resolve().parent

HTML = "text/html; charset=utf-8"
CSS = "text/css; charset=utf-8"
JS = "text/javascript; charset=utf-8"
SVG = "image/svg+xml"
PNG = "image/png"

#: The complete GET surface: exact request path -> (project file, content type).
#: Values are literals.  Nothing derived from a request ever reaches the disk.
ROUTES = {
    "/": ("index.html", HTML),
    "/manual": ("manual.html", HTML),
    "/about": ("about.html", HTML),
    "/changelog": ("changelog.html", HTML),
    "/static/style.css": ("static/style.css", CSS),
    "/static/app.js": ("static/app.js", JS),
    "/static/schematic.js": ("static/schematic.js", JS),
    "/static/bodeplot.js": ("static/bodeplot.js", JS),
    "/static/stepplot.js": ("static/stepplot.js", JS),
    "/static/layoutplot.js": ("static/layoutplot.js", JS),
    "/static/panel-step.js": ("static/panel-step.js", JS),
    "/static/panel-sheet.js": ("static/panel-sheet.js", JS),
    "/static/panel-robust.js": ("static/panel-robust.js", JS),
    "/static/panel-datasheet.js": ("static/panel-datasheet.js", JS),
    "/notebook": ("notebook.html", HTML),
    "/static/notebook.js": ("static/notebook.js", JS),
    "/static/theme.js": ("static/theme.js", JS),
    "/datasheet": ("datasheet.html", HTML),
    "/static/datasheet.js": ("static/datasheet.js", JS),
    "/static/panel-layout.js": ("static/panel-layout.js", JS),
    "/favicon.svg": ("static/favicon.svg", SVG),
    "/favicon.ico": ("static/icon-32.png", PNG),
    "/static/icon.svg": ("static/icon.svg", SVG),
    "/static/hero-layout.svg": ("static/hero-layout.svg", SVG),
    "/static/icon-32.png": ("static/icon-32.png", PNG),
    "/static/apple-touch-icon.png": ("static/apple-touch-icon.png", PNG),
    "/static/og.png": ("static/og.png", PNG),
}

#: Refuse absurd request bodies before reading them into memory.
MAX_BODY_BYTES = 64 * 1024

#: Fields every /simulate request must carry.
REQUIRED_FIELDS = ("vdd", "r1", "r2")

#: Plausible component ranges for /simulate_ac: field -> (low, high, unit).
#: Outside these ngspice either refuses the sweep or returns numerical noise.
AC_LIMITS = {
    "r": (1e-3, 1e12, "ohms"),
    "c": (1e-15, 1.0, "farads"),
}


class ValidationError(ValueError):
    """Raised when a /simulate request body is not usable. Maps to HTTP 400."""


class _BadBody:
    """Sentinel: the body was rejected and a 400 has already been sent."""


_BAD_BODY = _BadBody()


def _as_finite_float(raw, field):
    """Coerce one request field to a finite float or raise ValidationError."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValidationError(
            "Field " + repr(field) + " must be a number, got "
            + type(raw).__name__ + ". Enter a numeric value and run again."
        )
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValidationError(
            "Field " + repr(field) + " must be a number, got " + repr(raw)
            + ". Enter a numeric value and run again."
        ) from None
    if not math.isfinite(value):
        raise ValidationError(
            "Field " + repr(field) + " must be a finite number, got "
            + repr(raw) + ". Enter a real value and run again."
        )
    return value


def validate_simulate_request(payload):
    """Validate a decoded /simulate body and return (vdd, r1, r2) as floats.

    Raises ValidationError with a specific message if the body is not an
    object, if a required field is missing or non-numeric, or if either
    resistance is not strictly positive.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object with vdd, r1 and r2.")

    values = {}
    for field in REQUIRED_FIELDS:
        if field not in payload:
            raise ValidationError("Missing required field " + repr(field) + ".")
        values[field] = _as_finite_float(payload[field], field)

    for field in ("r1", "r2"):
        if values[field] <= 0:
            raise ValidationError(
                "Field " + repr(field) + " must be greater than 0 ohms. "
                "Set a positive resistance and run again."
            )

    return values["vdd"], values["r1"], values["r2"]


def validate_ac_request(payload):
    """Validate a decoded /simulate_ac body and return (r, c) as floats.

    Raises ValidationError with a specific message if the body is not an
    object, if a field is missing or non-numeric, or if a component value is
    outside the range an AC sweep can meaningfully cover.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object with r and c.")

    values = {}
    for field in ("r", "c"):
        if field not in payload:
            raise ValidationError("Missing required field " + repr(field) + ".")
        values[field] = _as_finite_float(payload[field], field)

    for field in ("r", "c"):
        low, high, unit = AC_LIMITS[field]
        value = values[field]
        if value <= 0:
            raise ValidationError(
                "Field " + repr(field) + " must be greater than 0 " + unit
                + ". Set a positive value and run again."
            )
        if not low <= value <= high:
            raise ValidationError(
                "Field " + repr(field) + " must be between " + ("%g" % low) + " and "
                + ("%g" % high) + " " + unit + ", got " + repr(value)
                + ". Choose a value in that range and run again."
            )

    return values["r"], values["c"]


def validate_api_request(payload):
    """Validate a decoded /api/simulate body and return (circuit_id, params).

    Every parameter is checked against its own spec from the catalogue, so a
    new circuit gets validation for free the moment it is registered.
    """
    if not isinstance(payload, dict):
        raise ValidationError(
            "Request body must be a JSON object with 'circuit' and 'params'."
        )

    circuit_id = payload.get("circuit")
    if not isinstance(circuit_id, str) or not circuit_id:
        raise ValidationError(
            "Field 'circuit' must be a circuit id string. Choose one of: "
            + ", ".join(circuits.CIRCUIT_ORDER) + "."
        )

    try:
        circuit = circuits.get_circuit(circuit_id)
    except circuits.UnknownCircuitError as exc:
        raise ValidationError(str(exc)) from None

    raw = payload.get("params", {})
    if not isinstance(raw, dict):
        raise ValidationError("Field 'params' must be a JSON object.")

    values = {}
    for spec in circuit["params"]:
        key = spec["key"]
        if key not in raw:
            raise ValidationError(
                "Missing required parameter " + repr(key) + " (" + spec["label"]
                + ") for circuit " + repr(circuit_id) + ". Supply it and run again."
            )

        value = _as_finite_float(raw[key], key)
        if not spec["min"] <= value <= spec["max"]:
            unit = (" " + spec["unit"]) if spec["unit"] else ""
            raise ValidationError(
                "Parameter " + repr(key) + " (" + spec["label"] + ") must be between "
                + ("%g" % spec["min"]) + " and " + ("%g" % spec["max"]) + unit
                + ", got " + repr(value) + ". Choose a value in that range and run again."
            )
        values[key] = value

    return circuit_id, values


def validate_design_request(payload):
    """Validate a /api/design body: circuit, start params, targets, budget.

    Returns (circuit_id, params, targets, max_evals). The circuit must declare
    a design block; the start parameters are validated exactly like a simulate
    request; targets are validated by the design layer itself.
    """
    circuit_id, params = validate_api_request(payload)

    try:
        _, block = design.design_block(circuit_id)
        targets = design.resolve_targets(block, payload.get("targets") or {})
    except design.DesignError as exc:
        raise ValidationError(str(exc)) from None

    raw_evals = payload.get("max_evals", DEFAULT_DESIGN_EVALS)
    if isinstance(raw_evals, bool) or not isinstance(raw_evals, int):
        raise ValidationError(
            "Field 'max_evals' must be a whole number of simulations to allow."
        )
    if not MIN_DESIGN_EVALS <= raw_evals <= MAX_DESIGN_EVALS:
        raise ValidationError(
            "Field 'max_evals' must be between " + str(MIN_DESIGN_EVALS)
            + " and " + str(MAX_DESIGN_EVALS) + "."
        )

    return circuit_id, params, targets, raw_evals


def _job_snapshot(job):
    """A JSON-ready copy of one job, history capped to the recent past."""
    return {
        "job": job["job"],
        "circuit": job["circuit"],
        "status": job["status"],
        "evals": job["evals"],
        "max_evals": job["max_evals"],
        "targets": job["targets"],
        "best": job["best"],
        "recent": job["recent"][-12:],
        "feasible": job["feasible"],
        "reason": job["reason"],
        "error": job["error"],
    }


def _prune_jobs_locked():
    """Drop the oldest finished jobs once the store is over its cap."""
    if len(JOBS) <= MAX_JOBS:
        return
    finished = [
        key for key, job in JOBS.items() if job["status"] != "running"
    ]
    for key in finished[: len(JOBS) - MAX_JOBS]:
        del JOBS[key]


def start_design_job(circuit_id, params, targets, max_evals):
    """Spawn the search on a worker thread and return its job id."""
    job_id = uuid.uuid4().hex[:12]
    stop_event = threading.Event()
    job = {
        "job": job_id,
        "circuit": circuit_id,
        "status": "running",
        "evals": 0,
        "max_evals": max_evals,
        "targets": targets,
        "best": None,
        "recent": [],
        "feasible": False,
        "reason": None,
        "error": None,
        "stop": stop_event,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
        _prune_jobs_locked()

    def entry_view(entry):
        return {
            "evals": entry["evals"],
            "params": entry["params"],
            "measured": entry["measured"],
            "margins": entry["margins"],
            "score": entry["score"],
            "feasible": entry["feasible"],
            "error": entry["error"],
        }

    def on_eval(entry, best):
        with JOBS_LOCK:
            job["evals"] = entry["evals"]
            job["recent"].append(entry_view(entry))
            del job["recent"][:-24]
            if best is not None:
                job["best"] = entry_view(best)

    def work():
        try:
            result = design.run_design(
                circuit_id, params, targets, max_evals,
                on_eval=on_eval, should_stop=stop_event.is_set,
            )
            with JOBS_LOCK:
                job["feasible"] = result["feasible"]
                job["reason"] = result["reason"]
                job["evals"] = result["evals"]
                if result["best"] is not None:
                    job["best"] = entry_view(result["best"])
                job["status"] = "stopped" if stop_event.is_set() else "done"
        except Exception as exc:  # noqa: BLE001 - boundary of a worker thread
            traceback.print_exc()
            with JOBS_LOCK:
                job["status"] = "failed"
                job["error"] = str(exc)

    threading.Thread(target=work, daemon=True).start()
    return job_id


def validate_advise_message(payload):
    """The user's message for the strategist: a bounded, non-empty string."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValidationError(
            "Field 'message' must say what you want designed. Describe the "
            "circuit and the numbers that matter, and send it again."
        )
    if len(message) > MAX_ADVISE_CHARS:
        raise ValidationError(
            "Field 'message' is longer than " + str(MAX_ADVISE_CHARS)
            + " characters. Shorten it and send it again."
        )
    return message.strip()


def _advise_snapshot(job):
    events = job["events"]
    return {
        "job": job["job"],
        "provider": job["provider"],
        "model": job["model"],
        "status": job["status"],
        "events": events[-80:],
        # Where the slice starts in the full log, so the client can render
        # by absolute position. Without it, a log past eighty events
        # shifted under the client's index and the conversation froze.
        "first": max(0, len(events) - 80),
        "now": job.get("now"),
    }


def _prune_advise_locked():
    if len(ADVISE_JOBS) <= MAX_ADVISE_JOBS:
        return
    finished = [k for k, j in ADVISE_JOBS.items() if j["status"] != "running"]
    for key in finished[: len(ADVISE_JOBS) - MAX_ADVISE_JOBS]:
        del ADVISE_JOBS[key]


def _advise_worker(job, client):
    """One strategist pass over the job's current conversation."""

    def on_event(event):
        # Progress is a heartbeat, not history: it lives in one transient
        # field the page polls, never in the event log. Appended, a forty
        # evaluation search would flood the log window and freeze the
        # conversation the user is trying to read.
        if event.get("kind") == "progress":
            entry = event.get("entry") or {}
            with ADVISE_LOCK:
                job["now"] = {
                    "tool": event.get("tool"),
                    "evals": entry.get("evals"),
                    "score": entry.get("score"),
                    "margins": entry.get("margins"),
                    "error": entry.get("error"),
                }
            return
        with ADVISE_LOCK:
            job["now"] = None
            job["events"].append(event)

    def run_tool_stoppable(name, arguments, on_progress=None):
        # The stop must reach inside a running search, not only between
        # tool calls: a forty-simulation search is where the waiting is.
        return strategist.run_tool(name, arguments, on_progress,
                                   should_stop=job["stop"].is_set)

    try:
        state = strategist.advise(
            client, job["messages"], on_event,
            should_stop=job["stop"].is_set,
            run_tool_fn=run_tool_stoppable,
        )
        with ADVISE_LOCK:
            job["status"] = state
            job["now"] = None
    except Exception as exc:  # noqa: BLE001 - boundary of a worker thread
        traceback.print_exc()
        with ADVISE_LOCK:
            job["events"].append({"kind": "error", "message": str(exc)})
            job["status"] = "error"
            job["now"] = None


def start_advise_job(provider, message):
    """Create the session and run the first strategist pass on a thread."""
    client = llm.get_client(provider)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job": job_id,
        "provider": provider,
        "model": client.model,
        "status": "running",
        "events": [{"kind": "user", "text": message}],
        "messages": [{"role": "user", "text": message}],
        "stop": threading.Event(),
    }
    with ADVISE_LOCK:
        ADVISE_JOBS[job_id] = job
        _prune_advise_locked()

    threading.Thread(target=_advise_worker, args=(job, client), daemon=True).start()
    return job_id


def continue_advise_job(job, message):
    """Append the user's reply and run another pass."""
    client = llm.get_client(job["provider"])
    with ADVISE_LOCK:
        job["events"].append({"kind": "user", "text": message})
        job["messages"].append({"role": "user", "text": message})
        job["status"] = "running"
        # A stop pressed last turn must not kill this one at birth.
        job["stop"].clear()
    threading.Thread(target=_advise_worker, args=(job, client), daemon=True).start()


def validate_targets(circuit_id, payload):
    """The optional targets dict: goal keys only, numbers only.

    The same shape the design panel sends; a triage or blame run measures
    against the user's numbers when given and the registry's otherwise.
    """
    raw = payload.get("targets")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError("targets must be an object of goal: number.")
    goals = {g["key"] for g in
             (circuits.get_circuit(circuit_id).get("design") or {})
             .get("goals", [])}
    found = {}
    for key, value in raw.items():
        if key not in goals:
            raise ValidationError("Unknown goal " + repr(key) + ".")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError("Goal " + repr(key) + " must be a number.")
        found[key] = float(value)
    return found or None


def validate_robust_request(payload):
    """Validate a /api/robust body: circuit, params, mode, optional runs."""
    circuit_id, params = validate_api_request(payload)
    try:
        pvt.require_supported(circuit_id)
    except pvt.PvtError as exc:
        raise ValidationError(str(exc)) from None

    mode = payload.get("mode")
    if mode not in ("pvt", "mc"):
        raise ValidationError(
            "Field 'mode' must be 'pvt' for the corner suite or 'mc' for "
            "Monte Carlo."
        )

    runs = payload.get("runs", pvt.MC_DEFAULT_RUNS)
    if isinstance(runs, bool) or not isinstance(runs, int):
        raise ValidationError("Field 'runs' must be a whole number of samples.")
    if mode == "mc" and not pvt.MC_MIN_RUNS <= runs <= pvt.MC_MAX_RUNS:
        raise ValidationError(
            "Field 'runs' must be between " + str(pvt.MC_MIN_RUNS) + " and "
            + str(pvt.MC_MAX_RUNS) + "."
        )
    return circuit_id, params, mode, runs


def _robust_snapshot(job):
    return {
        "job": job["job"],
        "circuit": job["circuit"],
        "mode": job["mode"],
        "status": job["status"],
        "done": job["done"],
        "total": job["total"],
        "rows": job["rows"],
        "summary": job["summary"],
        "keys": job["keys"],
        "error": job["error"],
    }


def start_robust_job(circuit_id, params, mode, runs):
    job_id = uuid.uuid4().hex[:12]
    total = len(pvt.PVT_CONDITIONS) if mode == "pvt" else runs
    job = {
        "job": job_id, "circuit": circuit_id, "mode": mode,
        "status": "running", "done": 0, "total": total,
        "rows": [], "summary": None, "keys": [],
        "error": None, "stop": threading.Event(),
    }
    with ROBUST_LOCK:
        ROBUST_JOBS[job_id] = job
        if len(ROBUST_JOBS) > MAX_ROBUST_JOBS:
            for key in [k for k, j in ROBUST_JOBS.items()
                        if j["status"] != "running"][: len(ROBUST_JOBS) - MAX_ROBUST_JOBS]:
                del ROBUST_JOBS[key]

    def on_each(row):
        with ROBUST_LOCK:
            job["rows"].append(row)
            job["done"] += 1

    def work():
        try:
            if mode == "pvt":
                result = pvt.run_pvt(circuit_id, params, on_each=on_each,
                                     should_stop=job["stop"].is_set)
                summary = result["worst"]
            else:
                result = pvt.run_monte_carlo(circuit_id, params, runs,
                                             on_each=on_each,
                                             should_stop=job["stop"].is_set)
                summary = result["stats"]
            with ROBUST_LOCK:
                job["summary"] = summary
                job["keys"] = result["keys"]
                job["status"] = "stopped" if job["stop"].is_set() else "done"
        except Exception as exc:  # noqa: BLE001 - worker boundary
            traceback.print_exc()
            with ROBUST_LOCK:
                job["error"] = str(exc)
                job["status"] = "failed"

    threading.Thread(target=work, daemon=True).start()
    return job_id


def analytic_divider(vdd, r1, r2):
    """Ideal divider value. A sanity check on ngspice, never a substitute."""
    return vdd * r2 / (r1 + r2)


def resolve_route(path):
    """Look up a GET path in the whitelist.

    Returns (relative_file, content_type) or None if the path is not served.
    The lookup is an exact dict hit on the string, so traversal attempts like
    /static/../server.py simply miss and 404 -- there is nothing to sanitise.
    """
    return ROUTES.get(path)


class FaradaemHandler(BaseHTTPRequestHandler):
    """Routes whitelisted GETs and the two POST endpoints; anything else is a JSON 404."""

    server_version = "Faradaem/1.6.0"
    protocol_version = "HTTP/1.1"

    # ---- routing -------------------------------------------------------

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self._guard(self._route_get)

    def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self._guard(self._route_post)

    def _route_get(self):
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/api/circuits":
            self._send_json(200, {"circuits": circuits.catalog()})
            return

        if path == "/api/design/status":
            self._handle_design_status(parts.query)
            return

        if path == "/api/advise/providers":
            self._send_json(200, {"providers": llm.available_providers()})
            return

        if path == "/api/advise/status":
            self._handle_advise_status(parts.query)
            return

        if path == "/api/robust/status":
            self._handle_robust_status(parts.query)
            return
        if path == "/api/workbench/status":
            self._handle_workbench_status(parts.query)
            return
        if path == "/api/charact/list":
            query = parse_qs(parts.query)
            circuit_id = (query.get("circuit") or [None])[0]
            self._send_json(200, {"stored": charact.listing(circuit_id)})
            return
        if path == "/api/charact/get":
            query = parse_qs(parts.query)
            ident = (query.get("id") or [""])[0]
            found = charact.load(ident)
            if found is None:
                self._send_json(404, {"error": "No stored characterization "
                                      + repr(ident) + "."})
            else:
                self._send_json(200, found)
            return
        if path == "/api/pin/status":
            query = parse_qs(parts.query)
            circuit_id = (query.get("circuit") or [""])[0]
            self._send_json(200, workbench.pin_state(circuit_id))
            return
        if path == "/api/doctor":
            self._send_json(200, {"checks": doctor_checks.checks()})
            return
        if path == "/api/notebook":
            query = parse_qs(parts.query)
            try:
                offset = max(0, int((query.get("offset") or ["0"])[0]))
            except ValueError:
                offset = 0
            self._send_json(200, workbench.notebook_page(offset=offset))
            return

        route = resolve_route(path)
        if route is None:
            self._send_json(404, {"error": "Not found: " + path})
            return
        self._send_file(*route)

    def _route_post(self):
        path = urlsplit(self.path).path
        if path == "/api/simulate":
            self._handle_api_simulate()
        elif path == "/api/netlist":
            self._handle_netlist()
        elif path == "/api/design":
            self._handle_design_start()
        elif path == "/api/design/seed":
            self._handle_design_seed()
        elif path == "/api/design/stop":
            self._handle_design_stop()
        elif path == "/api/advise":
            self._handle_advise_start()
        elif path == "/api/advise/reply":
            self._handle_advise_reply()
        elif path == "/api/advise/stop":
            self._handle_advise_stop()
        elif path == "/api/step":
            self._handle_step()
        elif path == "/api/bias":
            self._handle_bias()
        elif path == "/api/datasheet":
            self._handle_datasheet()
        elif path == "/api/layout":
            self._handle_layout()
        elif path == "/api/signoff":
            self._handle_signoff()
        elif path == "/api/robust":
            self._handle_robust_start()
        elif path == "/api/robust/stop":
            self._handle_robust_stop()
        elif path == "/api/workbench":
            self._handle_workbench_start()
        elif path == "/api/workbench/stop":
            self._handle_workbench_stop()
        elif path == "/api/triage":
            self._handle_triage()
        elif path == "/api/pin":
            self._handle_pin()
        elif path == "/api/pin/delete":
            self._handle_pin_delete()
        elif path == "/api/pin/check":
            self._handle_pin_check()
        elif path == "/api/packet":
            self._handle_packet()
        elif path == "/simulate":
            self._handle_simulate()
        elif path == "/simulate_ac":
            self._handle_simulate_ac()
        else:
            # Consume the body we are not going to use, or the next request on a
            # keep-alive connection starts parsing at the wrong offset.
            self._drain_body()
            self._send_json(404, {"error": "Not found: " + path})

    # ---- endpoints -----------------------------------------------------

    def _send_file(self, relative_file, content_type):
        """Serve one whitelisted project file. relative_file is always a literal."""
        try:
            body = (PROJECT_ROOT / relative_file).read_bytes()
        except OSError as exc:
            self._send_json(
                500, {"error": "Could not read " + relative_file + ": " + str(exc)}
            )
            return
        self._send_bytes(200, content_type, body)

    def _handle_api_simulate(self):
        """The one simulate endpoint. Every circuit in the catalogue runs here."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            self._send_json(200, circuits.simulate(circuit_id, params))
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_step(self):
        """The step response: what the amplifier does when pushed hard.

        Synchronous like /api/simulate, and about as slow: one transient run
        integrates thousands of timepoints through the PDK models.
        """
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            self._send_json(200, circuits.run_step(circuit_id, params))
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_bias(self):
        """Per-device operating point at the typical condition: what the
        schematic annotations draw. One simulation, synchronous."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            self._send_json(200, autopsy.bias(circuit_id, params))
        except pvt.PvtError as exc:
            self._send_json(400, {"error": str(exc)})
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_datasheet(self):
        """Rejection and range. Four amplifiers in one deck, about half a
        minute, synchronous like the other measurements."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            self._send_json(200, circuits.run_datasheet(circuit_id, params))
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_layout(self):
        """The floorplan and what its interconnect costs. Two simulations."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            self._send_json(200, circuits.run_layout(circuit_id, params))
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_signoff(self):
        """The foundry's own deck, over the geometry the tool just drew.

        Not part of drawing it: this shells out to KLayout and takes about
        a minute, so it is a thing the reader asks for once the drawing
        looks right. When the tool is not installed it says so, because a
        check that did not run must never be reported as one that passed.
        """
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        if not signoff.available():
            self._send_json(503, {
                "error": "The sign-off deck needs KLayout and the SKY130 "
                         "runset. Neither is being guessed at: install "
                         "KLayout, or set " + signoff.KLAYOUT_ENV_VAR
                         + " to point at it."
            })
            return

        try:
            shapes = circuits.layout_shapes(circuit_id, params)
            self._send_json(200, signoff.run_drc(shapes, circuit_id))
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except signoff.KlayoutNotFoundError as exc:
            self._send_json(503, {"error": str(exc)})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_netlist(self):
        """The deck the current form values would run, for reading."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        try:
            netlist = circuits.build_netlist_preview(circuit_id, params)
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"netlist": netlist})

    def _handle_design_start(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params, targets, max_evals = validate_design_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        job_id = start_design_job(circuit_id, params, targets, max_evals)
        self._send_json(200, {"job": job_id})

    def _handle_design_seed(self):
        """Turn a spec into a starting parameter set, without simulating."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            circuit_id, params = validate_api_request(payload)
            seeded, targets = design.seed_params(
                circuit_id, payload.get("targets") or {}, params
            )
        except (ValidationError, design.DesignError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        self._send_json(200, {"params": seeded, "targets": targets})

    def _handle_advise_start(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        try:
            message = validate_advise_message(payload)
            provider = payload.get("provider") or "anthropic"
            if not isinstance(provider, str):
                raise ValidationError("Field 'provider' must be a string.")
            job_id = start_advise_job(provider, message)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except llm.LlmError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"job": job_id})

    def _handle_advise_status(self, query):
        job_id = (parse_qs(query).get("job") or [""])[0]
        with ADVISE_LOCK:
            job = ADVISE_JOBS.get(job_id)
            snapshot = _advise_snapshot(job) if job else None
        if snapshot is None:
            self._send_json(404, {"error": "Unknown advise session " + repr(job_id) + "."})
            return
        self._send_json(200, snapshot)

    def _handle_advise_reply(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        job_id = payload.get("job") if isinstance(payload, dict) else None
        with ADVISE_LOCK:
            job = ADVISE_JOBS.get(job_id or "")
            busy = bool(job and job["status"] == "running")
        if job is None:
            self._send_json(404, {"error": "Unknown advise session " + repr(job_id) + "."})
            return
        if busy:
            self._send_json(409, {"error": "The strategist is still working. "
                                           "Wait for it to finish, then reply."})
            return
        try:
            message = validate_advise_message(payload)
            continue_advise_job(job, message)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except llm.LlmError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"job": job_id})

    def _handle_advise_stop(self):
        """Stop a running strategist turn. The stop lands between tool
        calls and inside a running search, and the turn ends as
        "stopped"; the conversation survives and can be continued."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        job_id = payload.get("job") if isinstance(payload, dict) else None
        with ADVISE_LOCK:
            job = ADVISE_JOBS.get(job_id or "")
        if job is None:
            self._send_json(404, {"error": "Unknown advise session "
                                           + repr(job_id) + "."})
            return
        job["stop"].set()
        self._send_json(200, {"job": job_id, "stopping": True})

    def _handle_workbench_start(self):
        """One background job: charact, blame, sweep, or autopsy.

        One job per circuit at a time; a second request is refused with
        the name of the one in the way, so the page can say why."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        try:
            circuit_id, params = validate_api_request(payload)
            targets = validate_targets(circuit_id, payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        kind = payload.get("kind")
        try:
            job_id = workbench.start(circuit_id, params, kind,
                                     targets=targets)
        except workbench.Busy as exc:
            self._send_json(409, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"job": job_id})

    def _handle_workbench_status(self, query):
        job_id = (parse_qs(query).get("job") or [""])[0]
        snapshot = workbench.status(job_id)
        if snapshot is None:
            self._send_json(404, {"error": "Unknown workbench job "
                                  + repr(job_id) + "."})
            return
        self._send_json(200, snapshot)

    def _handle_workbench_stop(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        job_id = str(payload.get("job") or "")
        if workbench.stop(job_id):
            self._send_json(200, {"stopping": True})
        else:
            self._send_json(404, {"error": "Unknown workbench job "
                                  + repr(job_id) + "."})

    def _handle_triage(self):
        """One simulation, every margin, the binding goal. Synchronous."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        try:
            circuit_id, params = validate_api_request(payload)
            targets = validate_targets(circuit_id, payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        try:
            self._send_json(200, triage.verdict(circuit_id, params,
                                                targets=targets))
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

    def _handle_pin(self):
        """Measure this sizing now and pin exactly what was measured.

        The client never supplies the numbers: a pin is the server's own
        measurement or it is nothing."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        try:
            entry = workbench.pin_now(circuit_id, params)
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, {"pinned": entry})

    def _handle_pin_delete(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        circuit_id = str(payload.get("circuit") or "")
        self._send_json(200, {"removed": pins.unpin(circuit_id)})

    def _handle_pin_check(self):
        """Re-measure the pinned sizing and report what moved."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        circuit_id = str(payload.get("circuit") or "")
        try:
            record = pins.check(circuit_id)
        except KeyError as exc:
            message = exc.args[0] if exc.args else str(exc)
            self._send_json(400, {"error": message})
            return
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, record)

    def _handle_packet(self):
        """Build the tapeout packet and stream the zip.

        The packet module verifies as it builds; a refusal comes back as
        a plain error naming what failed, never as a partial zip."""
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        try:
            circuit_id, params = validate_api_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        try:
            built = packet.build(circuit_id, params)
        except packet.PacketRefused as exc:
            self._send_json(409, {"error": str(exc)})
            return
        except INPUT_ERRORS as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - tool boundary
            self._send_json(500, {"error": str(exc)})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % built["filename"])
        self.send_header("Content-Length", str(len(built["bytes"])))
        self.end_headers()
        self.wfile.write(built["bytes"])

    def _handle_robust_start(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        try:
            circuit_id, params, mode, runs = validate_robust_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        job_id = start_robust_job(circuit_id, params, mode, runs)
        self._send_json(200, {"job": job_id})

    def _handle_robust_status(self, query):
        job_id = (parse_qs(query).get("job") or [""])[0]
        with ROBUST_LOCK:
            job = ROBUST_JOBS.get(job_id)
            snapshot = _robust_snapshot(job) if job else None
        if snapshot is None:
            self._send_json(404, {"error": "Unknown robustness job " + repr(job_id) + "."})
            return
        self._send_json(200, snapshot)

    def _handle_robust_stop(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        job_id = payload.get("job") if isinstance(payload, dict) else None
        with ROBUST_LOCK:
            job = ROBUST_JOBS.get(job_id or "")
        if job is None:
            self._send_json(404, {"error": "Unknown robustness job " + repr(job_id) + "."})
            return
        job["stop"].set()
        self._send_json(200, {"job": job_id, "stopping": True})

    def _handle_design_status(self, query):
        job_id = (parse_qs(query).get("job") or [""])[0]
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            snapshot = _job_snapshot(job) if job else None
        if snapshot is None:
            self._send_json(404, {"error": "Unknown design job " + repr(job_id) + "."})
            return
        self._send_json(200, snapshot)

    def _handle_design_stop(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return
        job_id = payload.get("job") if isinstance(payload, dict) else None
        with JOBS_LOCK:
            job = JOBS.get(job_id or "")
        if job is None:
            self._send_json(404, {"error": "Unknown design job " + repr(job_id) + "."})
            return
        job["stop"].set()
        self._send_json(200, {"job": job_id, "stopping": True})

    # ---- legacy endpoints ----------------------------------------------
    #
    # These predate the catalogue and are kept byte-compatible: same keys, same
    # order, same values.  They are thin adapters over the registry now, so
    # there is only ever one implementation of a circuit.

    def _handle_simulate(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            vdd, r1, r2 = validate_simulate_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            result = circuits.simulate("divider", {"vdd": vdd, "r1": r1, "r2": r2})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})
            return

        self._send_json(200, {
            "vout": result["vout"],
            "analytic": result["analytic"]["vout_ideal"],
        })

    def _handle_simulate_ac(self):
        payload = self._read_json_body()
        if payload is _BAD_BODY:
            return

        try:
            r, c = validate_ac_request(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            result = circuits.simulate("rc_lowpass", {"r": r, "c": c})
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})
            return

        self._send_json(200, {
            "freq": result["freq"],
            "mag_db": result["mag_db"],
            "phase_deg": result["phase_deg"],
            "f3db": result["f3db"],
            "fc_analytic": result["analytic"]["fc"],
            "dc_gain_db": result["dc_gain_db"],
            "phase_at_f3db": result["phase_at_f3db"],
        })

    def _drain_body(self):
        """Read and discard a request body so keep-alive framing stays in sync."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if length > MAX_BODY_BYTES:
            # Too big to swallow politely; drop the connection instead.
            self.close_connection = True
            return
        if length > 0:
            self.rfile.read(length)

    def _read_json_body(self):
        """Return the decoded body, or _BAD_BODY after already replying 400."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            self._send_json(400, {"error": "Invalid Content-Length header."})
            return _BAD_BODY

        if length <= 0:
            self._send_json(400, {"error": "Request body is empty."})
            return _BAD_BODY
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            self._send_json(
                400,
                {"error": "Request body exceeds " + str(MAX_BODY_BYTES) + " bytes."},
            )
            return _BAD_BODY

        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": "Body is not valid JSON: " + str(exc)})
            return _BAD_BODY

    # ---- plumbing ------------------------------------------------------

    def _guard(self, route):
        """Run a route so an unexpected exception becomes JSON, not a traceback."""
        try:
            route()
        except Exception:  # noqa: BLE001 - deliberate catch-all at the HTTP boundary
            traceback.print_exc()
            try:
                self._send_json(
                    500,
                    {"error": "Internal server error. See the Faradaem console log."},
                )
            except Exception:  # noqa: BLE001 - response committed or socket already gone
                pass

    def _send_json(self, status, payload):
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload).encode("utf-8"),
        )

    def _send_bytes(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("  " + (fmt % args))


class FaradaemServer(ThreadingHTTPServer):
    """A server that does not shout when a browser hangs up mid-connection.

    A client closing a keep-alive socket -- a closed tab, a reload -- surfaces
    as a ConnectionResetError inside the handler thread, and the default
    behaviour is to print a full traceback to the console. That is normal
    network life, not a fault, and it should not look like a crash.
    """

    daemon_threads = True

    QUIET_ERRORS = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

    def handle_error(self, request, client_address):
        kind = sys.exc_info()[0]
        if kind is not None and issubclass(kind, self.QUIET_ERRORS):
            return
        super().handle_error(request, client_address)


def main():
    httpd = FaradaemServer((HOST, PORT), FaradaemHandler)
    print("Faradaem running at http://" + HOST + ":" + str(PORT), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Faradaem stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
