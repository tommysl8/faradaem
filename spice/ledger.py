"""A record of what was tried, measured, and decided, kept so it can be
compared.

A design session produces dozens of ngspice runs and one answer. The answer
is what the tool shows; everything that led to it disappears. That is fine
for using the tool and useless for the question the project actually has to
answer, which is whether an LLM, a numerical optimizer, or a person sizes
these circuits better. That question can only be settled by comparing runs,
and runs can only be compared if they were written down.

So: an append-only log, one JSON object per line, one file per run. Every
attempt, every measurement, every decision, with enough provenance stamped
at the head of the file to say what the numbers came out of -- which commit,
which ngspice, which PDK, which corner. A run whose provenance is unknown is
not evidence.

Where it goes. Not the project folder: that lives in OneDrive, and an
append-heavy log inside a syncing folder is a bad idea twice over. The
default is ~/.faradaem/ledger, overridable with FARADAEM_LEDGER, so the
records are durable, discoverable, and outside anything that syncs on write.

What it does not do: judge. Nothing here decides whether a run was good.
It records what happened and leaves the reading to whoever compares.
"""

import io
import json
import os
import platform
import re
import subprocess
import sys
import time

#: Where runs are written, unless the environment says otherwise.
LEDGER_ENV_VAR = "FARADAEM_LEDGER"

#: One line per record, so a crash costs at most the line being written and
#: never the file. The reader is required to say how many lines it could
#: not parse rather than skipping them quietly.
SUFFIX = ".jsonl"

#: The kinds of thing a run can record. Anything else is refused, so a typo
#: cannot invent a category that later analysis silently misses.
KINDS = (
    "provenance",   # what this run was produced by; written once, first
    "start",        # an arm or a phase beginning, with its inputs
    "sim",          # one ngspice subprocess: the unit every cost is counted in
    "attempt",      # one candidate: params in, measurement out
    "decision",     # a choice made, by a person, a heuristic, or a model
    "layout",       # a drawn version and what the checks said about it
    "result",       # the outcome of an arm or a phase
    "end",          # the last line: what completed and what did not
    "note",         # anything a reader would want and a schema did not fit
)

#: Who produced a record. The whole point of the comparison is that these
#: are told apart, so it is required on every record that has an author.
AUTHORS = ("human", "seed", "optimizer", "llm", "llm_optimizer", "tool")


class LedgerError(RuntimeError):
    """Raised when a record cannot be written or read as intended."""


def root():
    """The directory runs are written to."""
    chosen = os.environ.get(LEDGER_ENV_VAR)
    if chosen:
        return chosen
    return os.path.join(os.path.expanduser("~"), ".faradaem", "ledger")


# ---------------------------------------------------------------------------
# provenance: what a run was produced by
# ---------------------------------------------------------------------------


def _command(args, cwd=None):
    try:
        done = subprocess.run(args, capture_output=True, text=True,
                              timeout=30, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return (done.stdout or "").strip() or None


def _git(project_root):
    commit = _command(["git", "rev-parse", "HEAD"], cwd=project_root)
    if commit is None:
        return None
    dirty = _command(["git", "status", "--porcelain"], cwd=project_root)
    return {
        "commit": commit,
        # A run made from an edited tree is not reproducible from the commit
        # alone, and saying so is the whole value of recording it.
        "clean": not bool(dirty),
    }


def _ngspice():
    from . import runner
    try:
        executable = runner.find_ngspice()
    except Exception:                                      # noqa: BLE001
        return None
    text = _command([executable, "-v"]) or ""
    found = re.search(r"ngspice-(\S+)", text)
    return {
        "executable": executable,
        "version": found.group(1).rstrip(":") if found else None,
    }


def _pdk():
    from . import runner
    try:
        root_path = runner.pdk_root()
        library = runner.find_sky130_lib()
    except Exception:                                      # noqa: BLE001
        return None

    # ciel pins the PDK by hash, and the directory it unpacks under is that
    # hash. It is the only version number the PDK actually carries.
    version = None
    versions = os.path.join(root_path, "ciel", "sky130", "versions")
    if os.path.isdir(versions):
        names = sorted(os.listdir(versions))
        if names:
            version = names[0]

    return {
        "root": root_path,
        "library": library,
        "version": version,
        "corner": runner.SKY130_DEFAULT_CORNER,
    }


def _klayout():
    try:
        import klayout.db as kdb
    except ImportError:
        return None
    return {"module": getattr(kdb, "__version__", None) or "present"}


def provenance():
    """Everything a reader needs to know what these numbers came out of.

    Assembled once per run. Anything that cannot be determined is recorded
    as null rather than omitted, because a missing key reads as an oversight
    and a null reads as what it is: this was not knowable here.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return {
        "git": _git(project_root),
        "ngspice": _ngspice(),
        "pdk": _pdk(),
        "klayout": _klayout(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


class Ledger:
    """One run, as an append-only file.

    clock and token exist so a test can produce a byte-identical file. In
    ordinary use they are the wall clock and the operating system's
    randomness, and the run id carries both a timestamp and a token so two
    runs started in the same second are still told apart.
    """

    def __init__(self, run_id=None, directory=None, clock=None, token=None,
                 stamp_provenance=True):
        self._clock = clock or time.time
        self.directory = directory or root()
        self.run_id = run_id or self._new_id(token)
        self.path = os.path.join(self.directory, self.run_id + SUFFIX)
        self._seq = 0

        os.makedirs(self.directory, exist_ok=True)
        if stamp_provenance:
            self.record("provenance", by="tool", **provenance())

    def _new_id(self, token):
        moment = time.strftime("%Y%m%d-%H%M%S",
                               time.gmtime(self._clock()))
        suffix = token or os.urandom(3).hex()
        return moment + "-" + suffix

    def record(self, kind, by=None, **fields):
        """Append one record and return it.

        The record is flushed to the operating system as it is written, so a
        run that dies mid-experiment still leaves everything up to the point
        it died.
        """
        if kind not in KINDS:
            raise LedgerError(
                "Unknown record kind " + repr(kind) + ". Add it to KINDS "
                "deliberately rather than inventing a category later "
                "analysis will not look for."
            )
        if by is not None and by not in AUTHORS:
            raise LedgerError(
                "Unknown author " + repr(by) + ". Telling these apart is the "
                "point of the comparison, so the list is closed."
            )

        self._seq += 1
        entry = {
            "run": self.run_id,
            "seq": self._seq,
            "at": round(self._clock(), 6),
            "kind": kind,
        }
        if by is not None:
            entry["by"] = by
        for key, value in fields.items():
            if value is not None or key in ("measured", "params", "error"):
                entry[key] = value

        line = json.dumps(entry, sort_keys=True, default=str)
        with io.open(self.path, "a", encoding="utf-8", newline="\n") as out:
            out.write(line + "\n")
            out.flush()
            os.fsync(out.fileno())
        return entry

    def close(self):
        """Nothing to release; here so callers can be written as if there is."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def read(path):
    """Every record in a run, and an honest count of what would not parse.

    A truncated last line is what a crash leaves behind. It is reported
    rather than dropped, because a silent drop turns a partial run into one
    that looks complete.
    """
    records, damaged = [], 0
    with io.open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                damaged += 1
    return {"records": records, "damaged": damaged, "path": path}


def runs(directory=None):
    """Every run in the ledger directory, newest last."""
    directory = directory or root()
    if not os.path.isdir(directory):
        return []
    return sorted(os.path.join(directory, name)
                  for name in os.listdir(directory)
                  if name.endswith(SUFFIX))


def summarise(loaded):
    """What one run contains, without judging any of it."""
    records = loaded["records"] if isinstance(loaded, dict) else loaded
    kinds, authors, arms = {}, {}, {}
    for entry in records:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
        if "by" in entry:
            authors[entry["by"]] = authors.get(entry["by"], 0) + 1
        if "arm" in entry:
            arms[entry["arm"]] = arms.get(entry["arm"], 0) + 1

    head = next((e for e in records if e["kind"] == "provenance"), None)
    return {
        "records": len(records),
        "kinds": kinds,
        "authors": authors,
        "arms": arms,
        "provenance": head,
        "damaged": loaded.get("damaged", 0) if isinstance(loaded, dict) else 0,
    }
