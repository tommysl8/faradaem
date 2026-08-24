"""End-to-end check against a real ngspice process.

This is the test that makes the whole project honest: it proves the number the
UI shows came out of the simulator, not out of a formula. It skips cleanly on
machines without ngspice so the rest of the suite still runs there.
"""

import sys

import pytest

from spice.runner import find_ngspice, simulate_divider

try:
    NGSPICE_PATH = find_ngspice()
    SKIP_REASON = ""
except Exception as exc:  # noqa: BLE001 - any discovery failure means "skip"
    NGSPICE_PATH = ""
    SKIP_REASON = "ngspice is not available on this machine, so the real "
    SKIP_REASON += "simulation test cannot run: " + str(exc).splitlines()[0]

requires_ngspice = pytest.mark.skipif(not NGSPICE_PATH, reason=SKIP_REASON)


@requires_ngspice
def test_equal_divider_halves_the_supply():
    assert simulate_divider(5, 10000, 10000) == pytest.approx(2.5, abs=1e-6)


@requires_ngspice
@pytest.mark.skipif(sys.platform != "win32",
                    reason="the GUI-build hazard exists only on Windows")
def test_uses_the_console_build_not_the_gui_build():
    # ngspice.exe opens a GUI window and never returns; only ngspice_con.exe is safe.
    assert NGSPICE_PATH.lower().endswith("ngspice_con.exe")
