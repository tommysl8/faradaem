"""The foundry's own checks, run by the tool that owns them.

``drc.py`` checks thirty-six rules read out of the technology file. That
is a fast loop, and it is useful for exactly that: it answers in
milliseconds while geometry is being drawn. It is not the answer. The
answer is the SKY130 runset, sixteen hundred lines of it, run by KLayout,
which has no stake in whether Faradaem's geometry is correct.

The difference is not academic. The hand checker passed geometry the
runset failed on forty counts, all of them one kind of rule -- a via needs
more metal along one axis than it needs all round -- that the fast checker
had implemented by halves. Those three rules are in the fast checker now,
because an inner loop that misses what the outer one catches is an inner
loop nobody can trust. But the reason they are known at all is that
something else looked.

Nothing here reimplements a check. It writes the GDS, hands it over, and
reads back what came out. When KLayout is not installed it says so and
declines, the same way the PDK-dependent paths do, rather than quietly
reporting a pass nobody performed.
"""

import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from . import gds, runner

#: Where KLayout keeps its batch binary. The environment variable wins, so
#: a different install does not need a code change.
KLAYOUT_ENV_VAR = "KLAYOUT_EXE"

KLAYOUT_CANDIDATES = (
    os.path.join(os.environ.get("APPDATA", ""), "KLayout", "klayout_app.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "KLayout",
                 "klayout_app.exe"),
    r"C:\Program Files\KLayout\klayout_app.exe",
    "/usr/bin/klayout",
    "/usr/local/bin/klayout",
)

#: The runset, inside the PDK, so it is the one that matches these models.
DRC_DECK_PARTS = ("libs.tech", "klayout", "drc", "sky130A_mr.drc")
LVS_DECK_PARTS = ("libs.tech", "klayout", "lvs", "sky130.lvs")

#: The sections of the deck worth running on a cell like these: the
#: front end (diffusion, poly, wells, contacts), the back end (metals and
#: their vias), and the grid every drawn edge has to land on.
DEFAULT_SECTIONS = ("feol", "beol", "offgrid")

#: A full run over a few thousand shapes takes a minute or two. Give it
#: room: a checker that times out reports a pass it did not perform.
DRC_TIMEOUT_S = 900


class KlayoutNotFoundError(RuntimeError):
    """Raised when the sign-off tool is not installed.

    Deliberately not a silent pass. Nothing in this module may report a
    clean result on a check that did not run.
    """


def find_klayout():
    """The KLayout batch binary, or None if it is not installed."""
    chosen = os.environ.get(KLAYOUT_ENV_VAR)
    if chosen and os.path.isfile(chosen):
        return chosen
    for candidate in KLAYOUT_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _deck(parts):
    root = runner.pdk_root()
    path = os.path.join(root, "sky130A", *parts)
    return path if os.path.isfile(path) else None


def drc_deck():
    return _deck(DRC_DECK_PARTS)


def lvs_deck():
    return _deck(LVS_DECK_PARTS)


def available():
    """Whether a sign-off run can be attempted at all."""
    return bool(find_klayout()) and bool(drc_deck())


def _cell_name(circuit_id):
    return circuit_id.upper()[:30]


def write_gds(shapes, circuit_id):
    """The geometry as a file, in the system temp dir, never the project."""
    name = _cell_name(circuit_id)
    handle, path = tempfile.mkstemp(suffix=".gds", prefix="faradaem-signoff-")
    os.close(handle)
    with open(path, "wb") as stream:
        stream.write(gds.library(name, name, shapes))
    return path, name


def _read_report(path):
    """The violations KLayout wrote, counted by the rule that raised them."""
    found = {}
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return found
    for item in tree.getroot().iter("item"):
        category = (item.findtext("category") or "?").strip().strip("'")
        found[category] = found.get(category, 0) + 1
    return found


def run_drc(shapes, circuit_id, sections=DEFAULT_SECTIONS,
            timeout_s=DRC_TIMEOUT_S):
    """Run the SKY130 runset over these shapes and report what it found.

    Returns the violations by rule, the sections that were run, and the
    deck it used, so a clean result can be read for exactly what it is.
    """
    executable = find_klayout()
    deck = drc_deck()
    if not executable or not deck:
        raise KlayoutNotFoundError(
            "The sign-off deck needs KLayout and the SKY130 runset. "
            + ("KLayout was not found; set " + KLAYOUT_ENV_VAR
               + " or install it." if not executable
               else "The runset was not found under " + runner.pdk_root() + ".")
        )

    path, top = write_gds(shapes, circuit_id)
    handle, report = tempfile.mkstemp(suffix=".lyrdb",
                                      prefix="faradaem-signoff-")
    os.close(handle)

    command = [executable, "-b", "-r", deck,
               "-rd", "input=" + path.replace("\\", "/"),
               "-rd", "top_cell=" + top,
               "-rd", "report=" + report.replace("\\", "/")]
    for section in sections:
        command += ["-rd", section + "=true"]

    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout_s)
        if done.returncode != 0:
            raise KlayoutNotFoundError(
                "KLayout exited with status " + str(done.returncode) + ": "
                + (done.stderr or done.stdout or "")[-600:]
            )
        violations = _read_report(report)
    except subprocess.TimeoutExpired as exc:
        raise KlayoutNotFoundError(
            "The sign-off deck did not finish within " + str(timeout_s)
            + " s. It reports nothing rather than a pass."
        ) from exc
    finally:
        for leftover in (path, report):
            if os.path.exists(leftover):
                os.remove(leftover)

    total = sum(violations.values())
    return {
        "clean": total == 0,
        "violations": violations,
        "total": total,
        "sections": list(sections),
        "deck": deck,
        "tool": executable,
        "shapes_checked": len(shapes),
        # Said in the result, so no caller can present it as more or less
        # than it is.
        "coverage": (
            "The SKY130 runset the PDK ships, run by KLayout over the "
            "sections named above. This is the foundry's own deck rather "
            "than the thirty-six rules Faradaem checks while drawing. What "
            "it does not cover is what those sections leave out, and it "
            "says nothing about whether the drawing is the right circuit, "
            "which is what layout versus schematic is for."
        ),
    }
