"""Locate, drive and read back a real ngspice process.

This module is the only numerical-truth boundary in Faradaem: every circuit
value the rest of the app reports comes out of an ngspice run performed here.
Nothing else in the codebase is allowed to compute a circuit quantity.

Extension points for later versions:
  * build_*_netlist -- one builder per topology (V0.1 RC, V0.4 op-amp).
  * parse_*         -- one parser per analysis output shape.
  * run_netlist     -- shared, stays the single process-invocation path.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

#: Environment variable that overrides ngspice discovery.
NGSPICE_ENV_VAR = "FARADAEM_NGSPICE"

#: Console build of ngspice.  Never ngspice.exe -- that one opens a GUI window.
NGSPICE_EXE_NAME = "ngspice_con.exe"

#: Last-resort location for the standard Windows ngspice-47 install.
NGSPICE_FALLBACK_PATH = r"C:\ngspice\Spice64\bin\ngspice_con.exe"

#: Default wall-clock budget for a single ngspice invocation, in seconds.
DEFAULT_TIMEOUT_S = 10.0

#: An AC sweep does more work than an operating point, so it gets longer.
AC_TIMEOUT_S = 20.0

#: The half-power point, 10*log10(2) = 3.0103 dB below the passband.
HALF_POWER_DB = 10.0 * math.log10(2.0)

#: Sweep shaping used by simulate_rc_lowpass.
AC_POINTS_PER_DECADE = 20
AC_DECADES_EACH_SIDE = 3
AC_FREQ_MIN = 0.01
AC_FREQ_MAX = 1e10

#: Prefix used for the throw-away netlists written to the system temp dir.
TEMP_PREFIX = "faradaem_"

#: Environment variable holding the PDK install root.
PDK_ROOT_ENV_VAR = "PDK_ROOT"

#: Where the SKY130 PDK lives when PDK_ROOT is not set in this process.
PDK_ROOT_FALLBACK = r"C:\pdk"

#: The ngspice model library, relative to the PDK root.
SKY130_LIB_PARTS = ("sky130A", "libs.tech", "ngspice", "sky130.lib.spice")

#: Process corners the library defines.  V0.2 loads tt only.
SKY130_CORNERS = ("tt", "ss", "ff", "sf", "fs")
SKY130_DEFAULT_CORNER = "tt"

#: Loading the SKY130 library costs 10 to 30 s on a cold run, so a PDK
#: simulation gets a far longer budget than a discrete one.
PDK_TIMEOUT_S = 90.0


class NgspiceNotFoundError(RuntimeError):
    """Raised when no usable ngspice executable can be located."""


class NgspiceRunError(RuntimeError):
    """Raised when ngspice fails to start, times out, or exits nonzero."""


class NgspiceParseError(ValueError):
    """Raised when expected numbers cannot be read out of ngspice output."""


class PdkNotFoundError(RuntimeError):
    """Raised when the SKY130 model library cannot be located."""


def find_ngspice() -> str:
    r"""Return the path to the console ngspice executable.

    Resolution order:
      1. the FARADAEM_NGSPICE environment variable,
      2. ngspice_con.exe on PATH,
      3. the literal C:\ngspice\Spice64\bin\ngspice_con.exe fallback.

    Raises NgspiceNotFoundError naming all three attempts if none resolve.
    """
    attempts: list[str] = []

    env_value = os.environ.get(NGSPICE_ENV_VAR, "").strip()
    if env_value:
        if os.path.isfile(env_value):
            return env_value
        resolved = shutil.which(env_value)
        if resolved:
            return resolved
        attempts.append(
            "1. $" + NGSPICE_ENV_VAR + " = " + repr(env_value) + " (not an executable)"
        )
    else:
        attempts.append("1. $" + NGSPICE_ENV_VAR + " (not set)")

    on_path = shutil.which(NGSPICE_EXE_NAME)
    if on_path:
        return on_path
    attempts.append("2. " + NGSPICE_EXE_NAME + " on PATH (not found)")

    if os.path.isfile(NGSPICE_FALLBACK_PATH):
        return NGSPICE_FALLBACK_PATH
    attempts.append("3. " + NGSPICE_FALLBACK_PATH + " (does not exist)")

    raise NgspiceNotFoundError(
        "Could not locate the console ngspice executable. Tried:\n  "
        + "\n  ".join(attempts)
        + "\nInstall ngspice, put " + NGSPICE_EXE_NAME + " on PATH, or set $"
        + NGSPICE_ENV_VAR + " to its full path."
    )


def _fmt(value: float, label: str) -> str:
    """Format a component value for a netlist line.

    Whole numbers print without a trailing .0 so netlists stay readable;
    everything else uses the shortest round-tripping repr.  No SPICE unit
    suffixes are ever emitted, so there is no meg/m style ambiguity.
    """
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(label + " must be a finite number, got " + repr(value))
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def build_divider_netlist(vdd: float, r1: float, r2: float) -> str:
    """Return the netlist for a two-resistor divider.

    V1 drives node "in", R1 spans "in" to "out", R2 spans "out" to ground.
    The embedded .control block runs a DC operating point and prints v(out).
    """
    lines = [
        "* Faradaem V0.0 resistor divider",
        "V1 in 0 DC " + _fmt(vdd, "vdd"),
        "R1 in out " + _fmt(r1, "r1"),
        "R2 out 0 " + _fmt(r2, "r2"),
        ".control",
        "op",
        "print v(out)",
        "quit",
        ".endc",
        ".end",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# counting the simulations, at the only two places one happens
# ---------------------------------------------------------------------------

#: The observer is per-thread: the server answers requests on several, and a
#: budget belonging to one experiment must never be spent by another.
_LOCAL = threading.local()


#: Where a deck names a throwaway file. Two runs of one design differ only
#: in these, so they are normalised out before the deck is hashed: a hash
#: that changes every run cannot show that two runs were the same circuit.
_SCRATCH = re.compile(r"(wrdata|write|\.lib)\s+\S+", re.IGNORECASE)


def deck_digest(netlist):
    """A hash of what the deck says, not of where it wrote its output."""
    normalised = _SCRATCH.sub(lambda m: m.group(1) + " <path>", netlist)
    return hashlib.sha256(normalised.encode("ascii", "replace")).hexdigest()


class SimBudgetExhausted(NgspiceRunError):
    """Raised when a run has spent the simulations it was given.

    A budget that stops the work is the point: it is what makes two design
    methods comparable. An arm that hits it has not failed to converge, it
    has run out, and the difference is recorded rather than inferred.
    """


class SimObserver:
    """Watches every ngspice subprocess for one piece of work.

    ledger, when given, gets one record per simulation: what was run, how
    long it took, and whether it worked. budget, when given, is a hard
    ceiling on how many may be run.
    """

    def __init__(self, ledger=None, arm=None, budget=None, phase="search",
                 exp=None):
        self.ledger = ledger
        self.arm = arm
        self.budget = budget
        self.phase = phase
        self.exp = exp
        self.count = 0
        self.seconds = 0.0
        self.durations = []

    def about_to_run(self):
        if self.budget is not None and self.count >= self.budget:
            raise SimBudgetExhausted(
                "This run has spent its budget of " + str(self.budget)
                + " simulations. The budget is what makes two design methods "
                "comparable, so it stops the work rather than being exceeded."
            )
        self.count += 1
        return self.count

    def ran(self, index, netlist, seconds, returncode, error=None):
        self.seconds += seconds
        self.durations.append(seconds)
        if self.ledger is None:
            return
        self.ledger.record(
            "sim", by="tool", arm=self.arm, exp=self.exp,
            sim_index=index, phase=self.phase,
            netlist_sha256=deck_digest(netlist),
            netlist_bytes=len(netlist),
            duration_s=round(seconds, 4),
            returncode=returncode,
            error=error,
        )

    def median_seconds(self):
        if not self.durations:
            return None
        ordered = sorted(self.durations)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0


def observer():
    """The observer watching this thread, if any."""
    return getattr(_LOCAL, "observer", None)


@contextlib.contextmanager
def observing(watcher):
    """Install an observer for the duration of a block.

    Nested blocks are refused rather than stacked: two budgets in force at
    once is a counting bug waiting to be argued about.
    """
    if observer() is not None:
        raise NgspiceRunError(
            "A simulation observer is already watching this thread. Two "
            "budgets in force at once cannot both be the budget."
        )
    _LOCAL.observer = watcher
    try:
        yield watcher
    finally:
        _LOCAL.observer = None


def _watch_start(netlist):
    """Called immediately before a subprocess. Returns (watcher, index)."""
    watcher = observer()
    if watcher is None:
        return None, None
    return watcher, watcher.about_to_run()


def _watch_end(watcher, index, netlist, started, returncode, error=None):
    if watcher is None:
        return
    watcher.ran(index, netlist, time.time() - started, returncode, error)


def run_netlist(netlist: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
    """Run a netlist through ngspice in batch mode and return its stdout.

    The netlist is written as ASCII to a temp file in the *system* temp dir --
    never inside the project folder, which lives in OneDrive.  ngspice also
    runs with its working directory set to that temp dir, so any incidental
    output it drops lands there too.  The temp file is always removed.
    """
    executable = find_ngspice()
    temp_dir = tempfile.gettempdir()

    handle, path = tempfile.mkstemp(suffix=".cir", prefix=TEMP_PREFIX, dir=temp_dir)
    watcher, index = _watch_start(netlist)
    started = time.time()
    try:
        with os.fdopen(handle, "w", encoding="ascii") as netlist_file:
            netlist_file.write(netlist)

        try:
            completed = subprocess.run(
                [executable, "-b", path],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=temp_dir,
            )
            _watch_end(watcher, index, netlist, started, completed.returncode)
        except subprocess.TimeoutExpired as exc:
            _watch_end(watcher, index, netlist, started, None, "timeout")
            raise NgspiceRunError(
                "ngspice did not finish within the " + str(timeout_s) + " s timeout"
                " (executable: " + executable + "). The circuit may not be converging."
            ) from exc
        except OSError as exc:
            raise NgspiceRunError(
                "Could not start ngspice at " + executable + ": " + str(exc)
            ) from exc

        if completed.returncode != 0:
            raise NgspiceRunError(
                "ngspice exited with status " + str(completed.returncode) + ".\n"
                "--- stderr ---\n" + (completed.stderr or "").strip() + "\n"
                "--- stdout ---\n" + (completed.stdout or "").strip()
            )

        return completed.stdout or ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


#: A "v(out) = <number>" line: any spacing, optional sign, scientific notation.
_VOUT_PATTERN = re.compile(
    r"^[ \t]*v[ \t]*\([ \t]*out[ \t]*\)[ \t]*=[ \t]*"
    r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def _tail(text: str, count: int = 20) -> str:
    """Return the last `count` lines of text, for error messages."""
    return "\n".join(text.splitlines()[-count:]) or "<no output>"


def parse_vout(stdout: str) -> float:
    """Extract the printed v(out) value from ngspice stdout.

    Raises NgspiceParseError, quoting the last 20 lines of output, if no
    well-formed "v(out) = <number>" line is present.
    """
    text = stdout or ""
    matches = _VOUT_PATTERN.findall(text)
    if not matches:
        raise NgspiceParseError(
            "Could not find a well-formed 'v(out) = <number>' line in the "
            "ngspice output.\n--- last 20 lines of stdout ---\n" + _tail(text)
        )

    raw = matches[-1]
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - the regex already constrains this
        raise NgspiceParseError(
            "Found a v(out) line but " + repr(raw) + " is not a usable number.\n"
            "--- last 20 lines of stdout ---\n" + _tail(text)
        ) from exc


def simulate_divider(vdd: float, r1: float, r2: float) -> float:
    """Simulate the divider with real ngspice and return v(out) in volts."""
    return parse_vout(run_netlist(build_divider_netlist(vdd, r1, r2)))


# ---------------------------------------------------------------------------
# V0.1: RC low-pass, AC sweep
# ---------------------------------------------------------------------------


def build_rc_lowpass_netlist(r, c, fstart, fstop, points_per_decade, out_path):
    """Return the netlist for an RC low-pass driven by a 1 V AC source.

    V1 excites node "in", R1 spans "in" to "out", C1 shunts "out" to ground.
    The control block sweeps by decade and writes the complex v(out) to
    out_path with wrdata.

    out_path is written with forward slashes even on Windows: a backslash is
    escape-prone inside an ngspice control block.
    """
    sweep = (
        "ac dec " + _fmt(points_per_decade, "points_per_decade")
        + " " + _fmt(fstart, "fstart")
        + " " + _fmt(fstop, "fstop")
    )
    lines = [
        "* Faradaem V0.1 RC low-pass",
        "V1 in 0 AC 1",
        "R1 in out " + _fmt(r, "r"),
        "C1 out 0 " + _fmt(c, "c"),
        ".control",
        sweep,
        "wrdata " + str(out_path).replace("\\", "/") + " v(out)",
        "quit",
        ".endc",
        ".end",
    ]
    return "\n".join(lines) + "\n"


def run_data_netlist(netlist, out_paths, timeout_s, with_stdout=False):
    """Run a netlist that writes data files, and return their text.

    The analysis is the netlist's business, not this function's: a sweep, a
    step response, anything that ends in wrdata. What happens here is the
    same either way. The netlist goes to a temp file, ngspice runs with the
    temp directory as its working directory, and every data file is removed
    in the finally block; simulator output is a temp file too, and none of
    it belongs in the project folder.

    With with_stdout=True the return is (texts, stdout) instead of texts.
    """
    executable = find_ngspice()
    temp_dir = tempfile.gettempdir()

    handle, netlist_path = tempfile.mkstemp(
        suffix=".cir", prefix=TEMP_PREFIX, dir=temp_dir
    )
    watcher, index = _watch_start(netlist)
    started = time.time()
    try:
        with os.fdopen(handle, "w", encoding="ascii") as netlist_file:
            netlist_file.write(netlist)

        try:
            completed = subprocess.run(
                [executable, "-b", netlist_path],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=temp_dir,
            )
            _watch_end(watcher, index, netlist, started, completed.returncode)
        except subprocess.TimeoutExpired as exc:
            _watch_end(watcher, index, netlist, started, None, "timeout")
            raise NgspiceRunError(
                "ngspice did not finish within the "
                + str(timeout_s) + " s timeout (executable: " + executable + ")."
            ) from exc
        except OSError as exc:
            raise NgspiceRunError(
                "Could not start ngspice at " + executable + ": " + str(exc)
            ) from exc

        if completed.returncode != 0:
            raise NgspiceRunError(
                "ngspice exited with status " + str(completed.returncode) + ".\n"
                "--- stderr ---\n" + (completed.stderr or "").strip() + "\n"
                "--- stdout ---\n" + (completed.stdout or "").strip()
            )

        texts = []
        for path in out_paths:
            if not os.path.isfile(path):
                raise NgspiceRunError(
                    "ngspice finished but wrote no data file at " + str(path) + ".\n"
                    "The wrdata command may have failed.\n"
                    "--- stdout ---\n" + _tail(completed.stdout or "")
                )
            with open(path, encoding="utf-8", errors="replace") as data_file:
                texts.append(data_file.read())

        return (texts, completed.stdout or "") if with_stdout else texts
    finally:
        for leftover in [netlist_path] + list(out_paths):
            try:
                os.unlink(leftover)
            except OSError:
                pass


def run_ac_multi(netlist, out_paths, timeout_s=AC_TIMEOUT_S, with_stdout=False):
    """Run an AC netlist that writes several data files. The sweep spelling
    of run_data_netlist, with the AC timeout as its default."""
    return run_data_netlist(netlist, out_paths, timeout_s, with_stdout)


def run_ac_netlist(netlist, out_path, timeout_s=AC_TIMEOUT_S, with_stdout=False):
    """Run an AC netlist that writes one data file, and return its text.

    The single-output case, which is most of them.  Delegates to run_ac_multi so
    there is still exactly one place that starts a simulator process.
    """
    result = run_ac_multi(netlist, [out_path], timeout_s, with_stdout)
    if with_stdout:
        texts, stdout = result
        return texts[0], stdout
    return result[0]


def parse_wrdata_complex(text):
    """Parse an ngspice wrdata file holding one complex vector.

    Each non-blank row is "frequency real imaginary".  That column order is not
    taken on faith: the integration test asserts it against physics by checking
    that the lowest-frequency point comes back at unity magnitude and zero
    phase, which only holds if the columns are read correctly.
    """
    points = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        columns = stripped.split()
        if len(columns) != 3:
            raise NgspiceParseError(
                "Row " + str(number) + " of the wrdata file has " + str(len(columns))
                + " columns, expected 3 (frequency, real, imaginary): " + repr(line)
            )

        try:
            frequency = float(columns[0])
            real = float(columns[1])
            imaginary = float(columns[2])
        except ValueError as exc:
            raise NgspiceParseError(
                "Row " + str(number) + " of the wrdata file is not numeric: " + repr(line)
            ) from exc

        points.append((frequency, real, imaginary))

    if not points:
        raise NgspiceParseError("The wrdata file contained no data rows.")

    return points


def unwrap_degrees(phases):
    """Remove 360 degree jumps so a lagging phase descends continuously.

    atan2 returns values in (-180, 180], so a phase heading past -180 wraps
    around to +180.  Accumulating an offset keeps the curve monotonic.
    """
    if not phases:
        return []

    unwrapped = [phases[0]]
    offset = 0.0
    for index in range(1, len(phases)):
        step = phases[index] - phases[index - 1]
        while step > 180.0:
            offset -= 360.0
            step -= 360.0
        while step < -180.0:
            offset += 360.0
            step += 360.0
        unwrapped.append(phases[index] + offset)

    return unwrapped


def compute_bode(points):
    """Turn (freq, re, im) rows into magnitude in dB and unwrapped phase."""
    freq = []
    mag_db = []
    wrapped_phase = []

    for frequency, real, imaginary in points:
        magnitude = math.hypot(real, imaginary)
        if magnitude == 0.0:
            raise NgspiceParseError(
                "The response at " + repr(frequency)
                + " Hz has zero magnitude, which has no dB value."
            )
        freq.append(frequency)
        mag_db.append(20.0 * math.log10(magnitude))
        wrapped_phase.append(math.degrees(math.atan2(imaginary, real)))

    return {
        "freq": freq,
        "mag_db": mag_db,
        "phase_deg": unwrap_degrees(wrapped_phase),
    }


def measure_lowpass(bode):
    """Measure DC gain, the -3.0103 dB corner, and the phase at that corner.

    The corner is interpolated linearly in log frequency between the two swept
    points that bracket the crossing, which is the right space to interpolate
    in for a decade sweep.
    """
    freq = bode["freq"]
    mag_db = bode["mag_db"]
    phase_deg = bode["phase_deg"]

    if len(freq) < 2:
        raise NgspiceParseError(
            "A Bode measurement needs at least two swept points, got " + str(len(freq)) + "."
        )

    dc_gain_db = mag_db[0]
    target = dc_gain_db - HALF_POWER_DB

    for index in range(1, len(freq)):
        lower = mag_db[index - 1]
        upper = mag_db[index]
        if not (lower > target >= upper):
            continue

        span = upper - lower
        fraction = 0.0 if span == 0.0 else (target - lower) / span

        if freq[index - 1] <= 0.0 or freq[index] <= 0.0:
            raise NgspiceParseError(
                "The sweep contains a non-positive frequency, so the corner "
                "cannot be interpolated in log frequency."
            )

        log_low = math.log10(freq[index - 1])
        log_high = math.log10(freq[index])

        return {
            "dc_gain_db": dc_gain_db,
            "f3db": 10.0 ** (log_low + fraction * (log_high - log_low)),
            "phase_at_f3db": (
                phase_deg[index - 1]
                + fraction * (phase_deg[index] - phase_deg[index - 1])
            ),
        }

    raise NgspiceParseError(
        "The sweep never crosses " + ("%.4f" % target) + " dB, so the -3 dB corner "
        "is not bracketed. Swept " + ("%g" % freq[0]) + " Hz to "
        + ("%g" % freq[-1]) + " Hz, magnitude ran from "
        + ("%.4f" % mag_db[0]) + " dB to " + ("%.4f" % mag_db[-1]) + " dB."
    )


# ---------------------------------------------------------------------------
# V0.1.5: measurements shared by the wider circuit library
#
# These are pure functions over a bode dict.  They never touch a simulator, so
# they can be proved against synthetic transfer functions.
# ---------------------------------------------------------------------------


def _log_interpolate(freq, values, low, high, target):
    """Interpolate where values crosses target between two swept samples.

    Returns (frequency, fraction).  Frequency is interpolated in log space,
    which is the right space for a decade sweep; fraction is reusable for
    interpolating any other series across the same pair.
    """
    if freq[low] <= 0.0 or freq[high] <= 0.0:
        raise NgspiceParseError(
            "The sweep contains a non-positive frequency, so a crossing cannot "
            "be interpolated in log frequency."
        )

    span = values[high] - values[low]
    fraction = 0.0 if span == 0.0 else (target - values[low]) / span
    log_low = math.log10(freq[low])
    log_high = math.log10(freq[high])

    return 10.0 ** (log_low + fraction * (log_high - log_low)), fraction


def _between(series, low, high, fraction):
    """Linear interpolation of a companion series across the same bracket."""
    return series[low] + fraction * (series[high] - series[low])


def _require_series(bode):
    freq = bode["freq"]
    mag_db = bode["mag_db"]
    phase_deg = bode.get("phase_deg") or [0.0] * len(freq)
    if len(freq) < 3:
        raise NgspiceParseError(
            "A measurement needs at least three swept points, got " + str(len(freq)) + "."
        )
    return freq, mag_db, phase_deg


def measure_highpass(bode):
    """Measure a high-pass corner.

    The passband is at the top of the sweep, so the reference gain is the
    highest-frequency sample and the -3 dB crossing is found by walking down
    from that end.
    """
    freq, mag_db, phase_deg = _require_series(bode)

    passband_db = mag_db[-1]
    target = passband_db - HALF_POWER_DB

    for index in range(len(freq) - 1, 0, -1):
        if mag_db[index] >= target > mag_db[index - 1]:
            f3db, fraction = _log_interpolate(freq, mag_db, index - 1, index, target)
            return {
                "passband_db": passband_db,
                "f3db": f3db,
                "phase_at_f3db": _between(phase_deg, index - 1, index, fraction),
            }

    raise NgspiceParseError(
        "The sweep never rises through " + ("%.4f" % target) + " dB, so the high-pass "
        "corner is not bracketed. Swept " + ("%g" % freq[0]) + " Hz to "
        + ("%g" % freq[-1]) + " Hz, magnitude ran from " + ("%.4f" % mag_db[0])
        + " dB to " + ("%.4f" % mag_db[-1]) + " dB."
    )


def measure_bandpass(bode):
    """Measure a band-pass peak, its -3 dB skirts, and the resulting Q.

    The sample grid is uniform in log frequency, so the true peak is recovered
    by fitting a parabola through the largest sample and its two neighbours --
    a decade sweep rarely lands a sample exactly on resonance.
    """
    freq, mag_db, phase_deg = _require_series(bode)

    peak_index = mag_db.index(max(mag_db))
    log_freq = [math.log10(f) for f in freq]

    if 0 < peak_index < len(mag_db) - 1:
        before = mag_db[peak_index - 1]
        centre = mag_db[peak_index]
        after = mag_db[peak_index + 1]
        curvature = before - 2.0 * centre + after
        # A flat or upward-curving triple has no interior vertex to find.
        offset = 0.0 if curvature == 0.0 else 0.5 * (before - after) / curvature
        offset = max(-1.0, min(1.0, offset))
        spacing = log_freq[peak_index + 1] - log_freq[peak_index]
        f0_measured = 10.0 ** (log_freq[peak_index] + offset * spacing)
        peak_gain_db = centre - 0.25 * (before - after) * offset
    else:
        f0_measured = freq[peak_index]
        peak_gain_db = mag_db[peak_index]

    target = peak_gain_db - HALF_POWER_DB

    lower = None
    for index in range(peak_index, 0, -1):
        if mag_db[index] >= target > mag_db[index - 1]:
            lower, _ = _log_interpolate(freq, mag_db, index - 1, index, target)
            break

    upper = None
    for index in range(peak_index + 1, len(freq)):
        if mag_db[index - 1] > target >= mag_db[index]:
            upper, _ = _log_interpolate(freq, mag_db, index - 1, index, target)
            break

    if lower is None or upper is None:
        missing = []
        if lower is None:
            missing.append("lower")
        if upper is None:
            missing.append("upper")
        raise NgspiceParseError(
            "The " + " and ".join(missing) + " -3 dB crossing is not bracketed by the "
            "sweep. Peak " + ("%.4f" % peak_gain_db) + " dB near "
            + ("%g" % f0_measured) + " Hz; swept " + ("%g" % freq[0]) + " Hz to "
            + ("%g" % freq[-1]) + " Hz, ending at " + ("%.4f" % mag_db[-1]) + " dB."
        )

    bandwidth = upper - lower

    return {
        "f0_measured": f0_measured,
        "peak_gain_db": peak_gain_db,
        "f_lower": lower,
        "f_upper": upper,
        "bw": bandwidth,
        "q_measured": f0_measured / bandwidth if bandwidth > 0 else float("inf"),
    }


def measure_closedloop(bode):
    """Measure a closed-loop amplifier: midband gain, corner, and their product.

    Midband is the bottom of the sweep, where feedback still holds the gain
    flat.  The corner is the highest -3 dB crossing, walked down from the top
    so a single-pole rolloff is picked up on its own terms.
    """
    freq, mag_db, phase_deg = _require_series(bode)

    midband_db = mag_db[0]
    target = midband_db - HALF_POWER_DB

    for index in range(len(freq) - 1, 0, -1):
        if mag_db[index - 1] > target >= mag_db[index]:
            f3db, fraction = _log_interpolate(freq, mag_db, index - 1, index, target)
            return {
                "midband_db": midband_db,
                "f3db": f3db,
                "phase_at_f3db": _between(phase_deg, index - 1, index, fraction),
                "gain_bw_product": (10.0 ** (midband_db / 20.0)) * f3db,
            }

    raise NgspiceParseError(
        "The sweep never falls through " + ("%.4f" % target) + " dB, so the closed-loop "
        "corner is not bracketed. Swept " + ("%g" % freq[0]) + " Hz to "
        + ("%g" % freq[-1]) + " Hz, magnitude ran from " + ("%.4f" % mag_db[0])
        + " dB to " + ("%.4f" % mag_db[-1]) + " dB."
    )


def measure_loop(bode):
    """Crossover and phase margin from a loop gain sweep.

    The loop gain starts positive and real at DC, so its unwrapped phase starts
    at zero and falls.  Crossover is where the magnitude passes through 0 dB,
    interpolated in log frequency like every other crossing in this module, and
    the phase margin is how far the phase still is from -180 degrees there.

    A phase margin near zero means the circuit is close to oscillating.  This
    function does not judge that; it reports it.
    """
    freq, mag_db, phase_deg = _require_series(bode)

    if mag_db[0] <= 0.0:
        raise NgspiceParseError(
            "The loop gain is already at or below 0 dB at the bottom of the "
            "sweep (" + ("%.4f" % mag_db[0]) + " dB at " + ("%g" % freq[0])
            + " Hz), so there is no crossover to find. The loop needs gain "
            "before it can have a phase margin."
        )

    for index in range(1, len(freq)):
        if mag_db[index - 1] > 0.0 >= mag_db[index]:
            crossover, fraction = _log_interpolate(freq, mag_db, index - 1, index, 0.0)
            phase_at = _between(phase_deg, index - 1, index, fraction)
            return {
                "loop_gain_db": mag_db[0],
                "f_crossover": crossover,
                "phase_at_crossover": phase_at,
                "phase_margin": 180.0 + phase_at,
            }

    raise NgspiceParseError(
        "The loop gain never falls through 0 dB, so the crossover is not "
        "bracketed. Swept " + ("%g" % freq[0]) + " Hz to " + ("%g" % freq[-1])
        + " Hz, magnitude ran from " + ("%.4f" % mag_db[0]) + " dB to "
        + ("%.4f" % mag_db[-1]) + " dB."
    )


def simulate_rc_lowpass(r, c):
    """Sweep an RC low-pass through real ngspice and measure its corner.

    The sweep is centred on the analytic corner, three decades either side,
    clamped to a range ngspice can actually handle.
    """
    resistance = float(r)
    capacitance = float(c)
    if not math.isfinite(resistance) or resistance <= 0.0:
        raise ValueError("r must be a finite, positive resistance, got " + repr(r))
    if not math.isfinite(capacitance) or capacitance <= 0.0:
        raise ValueError("c must be a finite, positive capacitance, got " + repr(c))

    fc_analytic = 1.0 / (2.0 * math.pi * resistance * capacitance)
    decade_span = 10.0 ** AC_DECADES_EACH_SIDE
    fstart = min(max(fc_analytic / decade_span, AC_FREQ_MIN), AC_FREQ_MAX)
    fstop = min(max(fc_analytic * decade_span, AC_FREQ_MIN), AC_FREQ_MAX)

    if fstop <= fstart:
        raise ValueError(
            "The corner frequency " + ("%g" % fc_analytic) + " Hz falls outside the "
            "sweepable range " + ("%g" % AC_FREQ_MIN) + " Hz to "
            + ("%g" % AC_FREQ_MAX) + " Hz."
        )

    # Reserve a unique name, then remove the file so run_ac_netlist can tell
    # whether ngspice actually wrote the data.
    handle, out_path = tempfile.mkstemp(
        suffix=".data", prefix=TEMP_PREFIX, dir=tempfile.gettempdir()
    )
    os.close(handle)
    try:
        os.unlink(out_path)
    except OSError:
        pass

    netlist = build_rc_lowpass_netlist(
        resistance, capacitance, fstart, fstop, AC_POINTS_PER_DECADE, out_path
    )
    bode = compute_bode(parse_wrdata_complex(run_ac_netlist(netlist, out_path)))
    measured = measure_lowpass(bode)

    return {
        "freq": bode["freq"],
        "mag_db": bode["mag_db"],
        "phase_deg": bode["phase_deg"],
        "f3db": measured["f3db"],
        "fc_analytic": fc_analytic,
        "dc_gain_db": measured["dc_gain_db"],
        "phase_at_f3db": measured["phase_at_f3db"],
    }


# ---------------------------------------------------------------------------
# V0.2: the SKY130 PDK
#
# The PDK is machine tooling installed outside the project -- never inside
# OneDrive -- so its location is resolved at call time rather than baked in.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# transient: the step response, and what it measures
# ---------------------------------------------------------------------------

#: A transient run integrates thousands of timepoints through the PDK
#: models, so it gets a longer budget than a sweep of the same circuit.
TRAN_TIMEOUT_S = 240.0

#: Settled means inside this fraction of the step, and stays there.
SETTLE_TOLERANCE = 0.001


def parse_wrdata_real(text):
    """Read a wrdata table of real values: (x, y) per row.

    Transient output is time in the first column and the vector in the
    second. Blank lines and any header ngspice writes are skipped, and a
    row that will not parse is skipped rather than guessed at.
    """
    points = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if len(points) < 3:
        raise NgspiceParseError(
            "The transient run produced " + str(len(points)) + " usable points. "
            "Expected a time series."
        )
    return points


def _edge_slew(points, edge_at, window):
    """Volts per second across the middle of one edge.

    The 10 to 90 percent band is the conventional measure: it excludes the
    corner where the amplifier is still linear and the tail where it is
    recovering, leaving the part that is genuinely slew limited.

    Returns (rate, start_value, final_value) or None when no edge is there.
    """
    before = [value for time, value in points if time < edge_at]
    segment = [(t, v) for t, v in points if edge_at <= t <= edge_at + window]
    if not before or len(segment) < 3:
        return None

    start = before[-1]
    final = segment[-1][1]
    span = final - start
    if abs(span) < 1e-4:
        return None

    low = start + 0.1 * span
    high = start + 0.9 * span
    rising = span > 0
    at_low = at_high = None
    for time, value in segment:
        if at_low is None and (value >= low if rising else value <= low):
            at_low = time
        if at_low is not None and at_high is None and (
                value >= high if rising else value <= high):
            at_high = time
            break
    if at_low is None or at_high is None or at_high <= at_low:
        return None
    return abs(high - low) / (at_high - at_low), start, final


def _settling_time(points, edge_at, window, start, final, tolerance):
    """When the output last leaves the band around its final value.

    Measured from the edge. Returns None if it never enters the band, which
    is a real answer: the amplifier did not settle inside the window.
    """
    band = abs(final - start) * tolerance
    segment = [(t, v) for t, v in points if edge_at <= t <= edge_at + window]
    if len(segment) < 10:
        return None

    # Settled means it stopped moving and stayed put. Taking the last sample
    # as the final value and asking only whether it is close to itself would
    # call an output still climbing at the end of the window settled, which
    # is the one case this needs to catch. So check the tail is flat first.
    tail = segment[max(0, int(len(segment) * 0.9)):]
    if abs(tail[-1][1] - tail[0][1]) > band:
        return None
    if abs(segment[-1][1] - final) > band:
        return None
    settled = None
    for time, value in reversed(segment):
        if abs(value - final) > band:
            break
        settled = time
    return None if settled is None else settled - edge_at


def _overshoot(points, edge_at, window, start, final):
    """How far past the final value the output went, as a fraction of the step."""
    span = final - start
    if abs(span) < 1e-9:
        return 0.0
    segment = [v for t, v in points if edge_at <= t <= edge_at + window]
    if not segment:
        return 0.0
    extreme = max(segment) if span > 0 else min(segment)
    return max(0.0, (extreme - final) / span if span > 0 else (final - extreme) / -span)


def measure_step(points, rise_at, fall_at, window,
                 tolerance=SETTLE_TOLERANCE):
    """Slew rate, settling and overshoot from a two-edge step response.

    Both edges are measured because they need not match: a two-stage
    amplifier charges its compensation capacitor from the tail current on
    the way up and discharges it through the output stage on the way down.
    The reported slew rate is the worse of the two, which is the number a
    datasheet would have to honour.
    """
    rise = _edge_slew(points, rise_at, window)
    fall = _edge_slew(points, fall_at, window)
    if rise is None and fall is None:
        raise NgspiceParseError(
            "No output edge was found in the step response. The amplifier "
            "may not be responding to the input step."
        )

    rates = {}
    if rise is not None:
        rates["slew_rise"] = rise[0]
    if fall is not None:
        rates["slew_fall"] = fall[0]

    result = dict(rates)
    result["slew_rate"] = min(rates.values())

    if rise is not None:
        rate, start, final = rise
        result["settling_time"] = _settling_time(
            points, rise_at, window, start, final, tolerance
        )
        result["overshoot"] = _overshoot(points, rise_at, window, start, final)
        result["step_final"] = final
    return result


# ---------------------------------------------------------------------------
# rejection, and the range over which a follower follows
# ---------------------------------------------------------------------------

#: Four amplifiers, a DC sweep and an AC sweep in one process.
DATASHEET_TIMEOUT_S = 300.0

#: A follower is following when the error is under this and the slope is
#: near one. The error alone is not enough: at the bottom of a sweep a dead
#: amplifier sits at zero, which matches a zero input and means nothing.
FOLLOW_ERROR_V = 0.01
FOLLOW_SLOPE_TOLERANCE = 0.1


def rejection_db(wanted, unwanted):
    """How far the wanted gain sits above the unwanted one, in dB.

    Both are read at the low-frequency end of the same sweep, where each is
    flat, which is the number a datasheet quotes as CMRR or PSRR.
    """
    if not wanted.get("mag_db") or not unwanted.get("mag_db"):
        raise NgspiceParseError("A rejection sweep produced no magnitudes.")
    return wanted["mag_db"][0] - unwanted["mag_db"][0]


def measure_follower_range(points, error_v=FOLLOW_ERROR_V,
                           slope_tolerance=FOLLOW_SLOPE_TOLERANCE):
    """Where a unity buffer tracks its input, swept across the supply.

    Returns the input range that works and the output range it produced.
    For a buffer these are the same measurement seen twice: how far the
    common mode can go before the input pair loses its headroom, and how
    far the output can swing before the output devices lose theirs.
    """
    if len(points) < 5:
        raise NgspiceParseError(
            "The sweep produced " + str(len(points)) + " points. Expected a "
            "transfer curve across the supply."
        )

    following = []
    for index in range(1, len(points) - 1):
        vin, vout = points[index]
        span = points[index + 1][0] - points[index - 1][0]
        if span == 0:
            continue
        slope = (points[index + 1][1] - points[index - 1][1]) / span
        if abs(vout - vin) < error_v and abs(slope - 1.0) <= slope_tolerance:
            following.append((vin, vout))

    if not following:
        raise NgspiceParseError(
            "The buffer never followed its input anywhere across the supply, "
            "so it has no usable range. The bias is probably wrong."
        )

    return {
        "input_low": following[0][0],
        "input_high": following[-1][0],
        "output_low": following[0][1],
        "output_high": following[-1][1],
        "input_range": following[-1][0] - following[0][0],
        "output_swing": following[-1][1] - following[0][1],
    }


def pdk_root():
    r"""Return the PDK install root: $PDK_ROOT, or C:\pdk if it is unset.

    Resolved on every call rather than at import, so a shell that gains the
    variable does not need the server restarted to be believed.
    """
    return os.environ.get(PDK_ROOT_ENV_VAR, "").strip() or PDK_ROOT_FALLBACK


def sky130_lib_path():
    """Return the OS-native path to the SKY130 ngspice model library."""
    return os.path.join(pdk_root(), *SKY130_LIB_PARTS)


def sky130_available():
    """True when the model library is present and readable.

    Tests that need a real PDK skip on this, the same way the ngspice
    integration tests skip when no simulator is installed.
    """
    return os.path.isfile(sky130_lib_path())


def find_sky130_lib(corner=SKY130_DEFAULT_CORNER):
    """Return the library path in the form a netlist .lib line wants.

    Forward slashes, always: a backslash is escape-prone inside an ngspice
    control block and inside a .lib path.  Raises PdkNotFoundError naming both
    the environment variable and the fallback if the file is not there.
    """
    if corner not in SKY130_CORNERS:
        raise ValueError(
            "Unknown SKY130 corner " + repr(corner) + ". Choose one of: "
            + ", ".join(SKY130_CORNERS) + "."
        )

    path = sky130_lib_path()
    if not os.path.isfile(path):
        env_value = os.environ.get(PDK_ROOT_ENV_VAR, "").strip()
        source = (
            "$" + PDK_ROOT_ENV_VAR + " = " + repr(env_value)
            if env_value
            else "$" + PDK_ROOT_ENV_VAR + " is not set in this process, so the "
            + repr(PDK_ROOT_FALLBACK) + " fallback was used"
        )
        raise PdkNotFoundError(
            "Could not find the SKY130 model library at " + path + ".\n"
            + source + ".\n"
            "Install the SKY130 PDK, then set " + PDK_ROOT_ENV_VAR + " to its "
            "root and restart the server so the new value is picked up."
        )

    return path.replace("\\", "/")


#: Collapses "v ( out )" and "V(OUT)" to one comparable key.
_SPACE = re.compile(r"\s+")


def parse_op_values(stdout, names):
    """Read printed operating-point values out of ngspice stdout.

    ngspice prints one "name = value" line per vector after an `op`.  Names are
    matched with whitespace removed and case folded, so "v(out)" matches
    however ngspice chose to space it.  The last occurrence wins, which is what
    is wanted when a control block prints more than once.

    Raises NgspiceParseError naming every value that never appeared.
    """
    wanted = {_SPACE.sub("", name).lower(): name for name in names}
    found = {}

    for line in (stdout or "").splitlines():
        if "=" not in line:
            continue
        left, _, right = line.partition("=")
        key = _SPACE.sub("", left).lower()
        if key not in wanted:
            continue
        fields = right.strip().split()
        if not fields:
            continue
        try:
            found[wanted[key]] = float(fields[0])
        except ValueError:
            continue

    missing = [name for name in names if name not in found]
    if missing:
        raise NgspiceParseError(
            "The operating point did not report " + ", ".join(missing)
            + ". Check that the control block prints it.\n"
            "--- last 20 lines of stdout ---\n" + _tail(stdout or "")
        )

    return found
