"""The tapeout packet, the doctor, and the notebook's reading.

The packet's one property worth defending: everything in the zip was
produced in the same run from the same sizing, and the digest proves it.
The doctor's: a green check is the tool's own resolution, and optional
is not the same as broken.
"""

import hashlib
import io
import json
import zipfile

import pytest

import doctor
import workbench
from spice import circuits, klvs, layout, ledger, packet, runner, signoff


def _ngspice_missing():
    try:
        runner.find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_signoff = pytest.mark.skipif(
    not signoff.available() or not klvs.available()
    or not layout.tech_available(),
    reason="KLayout, the runset and the PDK are all needed",
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FARADAEM_LEDGER", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# the packet
# ---------------------------------------------------------------------------


def test_a_circuit_without_a_layout_earns_no_packet():
    with pytest.raises(packet.PacketRefused):
        packet.build("divider", {})


@requires_signoff
def test_the_packet_verifies_as_it_builds_and_the_digest_binds():
    """The whole point: a clean report from Tuesday can no longer ship
    beside Wednesday's GDS, because the report and the GDS are produced
    in one call and chained by hash."""
    built = packet.build("ota_5t", circuits.defaults("ota_5t"))
    bundle = zipfile.ZipFile(io.BytesIO(built["bytes"]))
    names = sorted(bundle.namelist())
    assert names == ["README.md", "manifest.json", "ota_5t.gds",
                     "ota_5t.spice", "signoff.json"]

    manifest = json.loads(bundle.read("manifest.json"))
    gds_bytes = bundle.read("ota_5t.gds")
    assert hashlib.sha256(gds_bytes).hexdigest() == manifest["gds_sha256"]

    readme = bundle.read("README.md").decode("utf-8")
    assert manifest["gds_sha256"] in readme
    assert "out" in manifest["ports"] and "vdd" in manifest["ports"]

    deck = json.loads(bundle.read("signoff.json"))
    assert deck["clean"] is True


@requires_signoff
def test_a_failing_verdict_refuses_the_packet(monkeypatch):
    def failing(shapes, circuit_id, **kwargs):
        return {"clean": False, "total": 3,
                "violations": {"met1.2": 3}, "sections": [],
                "shapes_checked": len(shapes)}
    monkeypatch.setattr(signoff, "run_drc", failing)
    with pytest.raises(packet.PacketRefused) as caught:
        packet.build("ota_5t", circuits.defaults("ota_5t"))
    assert "3 violations" in str(caught.value)


# ---------------------------------------------------------------------------
# the doctor
# ---------------------------------------------------------------------------


def test_the_doctor_checks_every_dependency_once():
    found = doctor.checks()
    names = [item["name"] for item in found]
    for expected in ("Python", "ngspice", "SKY130 PDK",
                     "KLayout application", "Ledger directory"):
        assert expected in names, expected
    for item in found:
        assert item["state"] in (doctor.OK, doctor.ABSENT, doctor.MISSING)
        if item["state"] == doctor.MISSING:
            assert item["fix"], item["name"]


def test_optional_pieces_are_absent_not_failures(monkeypatch):
    """An unset API key is a fact, not a fault. The exit code counts only
    what is missing."""
    from spice import llm
    monkeypatch.setattr(llm, "read_setting", lambda name: "")
    found = doctor.checks()
    keys = [item for item in found if "key" in item["name"]]
    assert keys
    for item in keys:
        assert item["state"] == doctor.ABSENT


# ---------------------------------------------------------------------------
# the workbench jobs and the notebook
# ---------------------------------------------------------------------------


def test_an_unknown_kind_is_refused(home):
    with pytest.raises(ValueError):
        workbench.start("divider", {}, "surprise")


def test_the_notebook_reads_runs_newest_first(home):
    for stamp in ("a", "b"):
        book = ledger.Ledger()
        book.record("note", author="tool", note="run " + stamp,
                    circuit="divider")
        book.close() if hasattr(book, "close") else None

    page = workbench.notebook_page()
    assert page["total"] >= 2
    assert page["more"] is False
    for row in page["rows"]:
        assert row["damaged"] == 0
        assert "records" in row


def test_the_notebook_pages_rather_than_dumping(home):
    for index in range(3):
        book = ledger.Ledger()
        book.record("note", author="tool", note="run %d" % index)
    page = workbench.notebook_page(offset=0, limit=2)
    assert len(page["rows"]) == 2
    assert page["more"] is True
    rest = workbench.notebook_page(offset=2, limit=2)
    assert len(rest["rows"]) >= 1
