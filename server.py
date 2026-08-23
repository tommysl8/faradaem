"""Faradaem local web server -- standard library only.

Serves three pages (simulator, changelog, about), their static assets, and the
simulation API:

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
import math
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from spice import circuits
from spice.runner import NgspiceNotFoundError, NgspiceRunError

#: Errors from a simulation attempt that map to HTTP 500 rather than a crash.
SIMULATION_ERRORS = (NgspiceNotFoundError, NgspiceRunError, ValueError)

HOST = "127.0.0.1"
PORT = 8000

PROJECT_ROOT = Path(__file__).resolve().parent

HTML = "text/html; charset=utf-8"
CSS = "text/css; charset=utf-8"
JS = "text/javascript; charset=utf-8"
SVG = "image/svg+xml"

#: The complete GET surface: exact request path -> (project file, content type).
#: Values are literals.  Nothing derived from a request ever reaches the disk.
ROUTES = {
    "/": ("index.html", HTML),
    "/about": ("about.html", HTML),
    "/changelog": ("changelog.html", HTML),
    "/static/style.css": ("static/style.css", CSS),
    "/static/app.js": ("static/app.js", JS),
    "/static/schematic.js": ("static/schematic.js", JS),
    "/static/bodeplot.js": ("static/bodeplot.js", JS),
    "/favicon.svg": ("static/favicon.svg", SVG),
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

    server_version = "Faradaem/0.1.6"
    protocol_version = "HTTP/1.1"

    # ---- routing -------------------------------------------------------

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self._guard(self._route_get)

    def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        self._guard(self._route_post)

    def _route_get(self):
        path = urlsplit(self.path).path
        if path == "/api/circuits":
            self._send_json(200, {"circuits": circuits.catalog()})
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
        except SIMULATION_ERRORS as exc:
            self._send_json(500, {"error": str(exc)})

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
