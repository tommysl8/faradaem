"""faradaem doctor: what this machine has, what it lacks, how to fix it.

Setting up ngspice, a PDK and KLayout on Windows used to be the worst
afternoon of the whole project, and the errors arrived one at a time, each
after a failed run. This runs every check at once and says, per check,
exactly what to do. It resolves everything through the same functions the
tool itself uses, so a green check here is the tool's own opinion, not a
parallel guess.

Most of the fixes are now one command: install.py fetches the simulator and
the technology files and puts them where these checks look.

Three states, because two would lie:

  ok       the tool will use this
  absent   optional, not present; the features that need it say so
  missing  required for part of the tool; the fix is printed

Usage: python doctor.py            (exit code = number of missing checks)
"""

import io
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spice import ledger, llm, runner, signoff  # noqa: E402
from spice import layout as layout_module  # noqa: E402

OK, ABSENT, MISSING = "ok", "absent", "missing"


def checks():
    """Every check, run now, in reading order. Returns a list of dicts."""
    found = []

    def add(name, state, detail, fix=None):
        found.append({"name": name, "state": state, "detail": detail,
                      "fix": fix})

    # ---- python ---------------------------------------------------------
    version = "%d.%d.%d" % sys.version_info[:3]
    if sys.version_info >= (3, 9):
        add("Python", OK, version)
    else:
        add("Python", MISSING, version,
            "Install Python 3.9 or newer; the server uses only its "
            "standard library.")

    # ---- ngspice --------------------------------------------------------
    try:
        exe = runner.find_ngspice()
        banner = subprocess.run([exe, "--version"], capture_output=True,
                                text=True, timeout=30).stdout
        version_line = next((line.strip() for line in banner.splitlines()
                             if "ngspice" in line.lower()), "version unknown")
        add("ngspice", OK, exe + " (" + version_line + ")")
    except Exception as exc:  # noqa: BLE001 - each attempt is in the message
        add("ngspice", MISSING, str(exc).splitlines()[0],
            "Run 'python install.py', which fetches the console build and "
            "unpacks it where the tool looks. To use one you already have, "
            "set FARADAEM_NGSPICE to its full path instead. The console "
            "binary, never ngspice.exe.")

    # ---- the PDK --------------------------------------------------------
    lib = runner.sky130_lib_path()
    if os.path.isfile(lib):
        add("SKY130 PDK", OK, lib)
    else:
        add("SKY130 PDK", MISSING, "no model library at " + lib,
            "Run 'python install.py', which fetches the technology files and "
            "device models (21 MB, not the 2.2 GB a full install costs) and "
            "unpacks them where the tool looks. To use a PDK you already "
            "have, set "
            "PDK_ROOT to its root instead, outside any synced folder.")

    # ---- the technology data -------------------------------------------
    if layout_module.tech_available():
        add("Layout technology data", OK,
            "the tech file's dimensions are readable")
    else:
        add("Layout technology data", MISSING,
            "the PDK's technology file was not found",
            "Fix the SKY130 PDK check above; the layout reads its "
            "dimensions from the PDK itself.")

    # ---- KLayout, both halves ------------------------------------------
    try:
        import klayout.db  # noqa: F401
        add("KLayout Python package", OK, "klayout.db imports")
    except Exception:  # noqa: BLE001 - any import failure means absent
        add("KLayout Python package", MISSING, "klayout.db does not import",
            "pip install klayout==0.30.11 into the project's environment. "
            "It is machine tooling and never goes in requirements.txt.")

    app = signoff.find_klayout()
    if app:
        add("KLayout application", OK, app)
    else:
        add("KLayout application", MISSING,
            "no klayout_app.exe found",
            "Install KLayout 0.30.11 (the Windows installer puts it in "
            "%APPDATA%\\KLayout) or set KLAYOUT_EXE to the binary. The "
            "foundry's deck runs through it.")

    if signoff.available():
        add("Sign-off deck", OK, "the PDK's own runset is reachable")
    else:
        add("Sign-off deck", MISSING,
            "KLayout and the PDK runset are not both available",
            "Fix the KLayout and PDK checks above; the deck is read from "
            "the PDK, never copied into the project.")

    # ---- writable places ------------------------------------------------
    for label, path in (("Ledger directory", ledger.root()),
                        ("System temp directory", tempfile.gettempdir())):
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".faradaem-doctor")
            with io.open(probe, "w", encoding="ascii") as stream:
                stream.write("ok")
            os.remove(probe)
            add(label, OK, path)
        except OSError as exc:
            add(label, MISSING, "%s (%s)" % (path, exc),
                "Make this directory writable; simulations and records "
                "go there, never into the project folder.")

    # ---- the strategist's keys, optional -------------------------------
    for label, var in (("Anthropic key", llm.ANTHROPIC_KEY_VAR),
                       ("OpenAI key", llm.OPENAI_KEY_VAR)):
        if llm.read_setting(var):
            add(label, OK, var + " is set")
        else:
            add(label, ABSENT, var + " is not set",
                "Optional. Set it to use that provider in 'Ask for a "
                "design'; everything else works without it.")

    return found


def main():
    found = checks()
    width = max(len(item["name"]) for item in found)
    marks = {OK: "ok", ABSENT: "--", MISSING: "XX"}
    failures = 0
    for item in found:
        print("[%s] %-*s  %s" % (marks[item["state"]], width,
                                 item["name"], item["detail"]))
        if item["state"] == MISSING:
            failures += 1
            print(" " * (width + 7) + "fix: " + item["fix"])
    print()
    if failures:
        print("%d check(s) missing. The fixes above are exact." % failures)
    else:
        print("Everything the tool can use is present.")
    return failures


if __name__ == "__main__":
    sys.exit(main())
