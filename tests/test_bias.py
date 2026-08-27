"""The bias annotation's backend, and the small convenience contracts
shipped beside it: the datasheet's units, the workbench's plain words.
"""

import pytest

import workbench
from spice import autopsy, charact, pvt
from spice.runner import find_ngspice, sky130_available


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(), reason="a real ngspice is needed",
)

requires_live_pdk = pytest.mark.skipif(
    ngspice_missing() or not sky130_available(),
    reason="a real ngspice and the SKY130 model library are both needed",
)


def test_bias_refuses_non_pdk_circuits():
    with pytest.raises(pvt.PvtError):
        autopsy.bias("twopole_amp", {})


def test_the_busy_refusal_speaks_words_not_tokens():
    for kind, word in (("charact", "characterization"),
                       ("blame", "sensitivity run"),
                       ("sweep", "bias sweep"),
                       ("autopsy", "corner autopsy")):
        job = {"circuit": "x", "kind": kind, "status": "running"}
        with workbench.LOCK:
            workbench.JOBS["fake-" + kind] = job
        try:
            with pytest.raises(workbench.Busy) as refusal:
                workbench.start("x", {}, "charact")
            assert word in str(refusal.value)
            assert "charact for this circuit" not in str(refusal.value) \
                or kind != "charact"
        finally:
            with workbench.LOCK:
                del workbench.JOBS["fake-" + kind]


@requires_ngspice
def test_the_stored_document_carries_units_for_its_sizing():
    found = charact.characterize("twopole_amp", {}, include=("bench",))
    assert found["sizing_units"]["rin"] == "Ω"
    assert found["sizing_units"]["gbw"] == "Hz"
    assert set(found["sizing_units"]) == set(found["sizing"])


@requires_live_pdk
def test_bias_reads_the_whole_operating_point_in_one_simulation():
    found = autopsy.bias("ota_5t", {})
    assert found["sims"] == 1
    assert set(found["device_order"]) == set(found["devices"])
    m1 = found["devices"]["M1"]
    for key in ("vds", "vdsat", "vgs", "gm", "id", "headroom"):
        assert key in m1
    # The input pair carries about half the tail: a number, and a sane one.
    assert m1["id"] is None or 0 < abs(m1["id"]) < 1e-3
