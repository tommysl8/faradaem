"""Priors: experience harvested from the ledger, and the curve that
measures what it is worth.

The harvest and the pick are proved against a ledger written by the test,
so what should be found is known exactly. The live curve runs on the
two-pole macromodel with a seeded spec sequence, and asserts only what
the machinery guarantees: counted costs, a growing library, and a warm
arm that starts from it. Whether warm beats cold is the experiment's
finding, not the test's assumption.
"""

import random

import pytest

from spice import circuits, ledger, priors
from spice.runner import find_ngspice


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(), reason="a real ngspice is needed",
)


def _write_run(directory, circuit, entries):
    book = ledger.Ledger(directory=str(directory), stamp_provenance=False)
    for params, measured, loaded in entries:
        book.record("attempt", by="optimizer", circuit=circuit,
                    params=params, measured=measured, loaded=loaded)
    return book


GOOD = {"gbw": 2e6, "fp2": 8e5}
GOOD_MEASURED = {"phase_margin": 75.0, "f_crossover": 2e5}
POOR = {"gbw": 1e6, "fp2": 1e4}
POOR_MEASURED = {"phase_margin": 20.0, "f_crossover": 9e4}


def test_harvest_collects_measured_attempts_and_drops_loaded_ones(tmp_path):
    _write_run(tmp_path, "twopole_amp", [
        (GOOD, GOOD_MEASURED, None),
        (POOR, POOR_MEASURED, None),
        ({"gbw": 3e6, "fp2": 3e5}, {"phase_margin": 50.0,
                                    "f_crossover": 3e5}, True),
        ({"gbw": 4e6, "fp2": 4e5}, None, None),
    ])
    library = priors.harvest("twopole_amp", directory=str(tmp_path))
    assert len(library) == 2
    assert all(entry["measured"] for entry in library)


def test_harvest_ignores_other_circuits_and_duplicates(tmp_path):
    _write_run(tmp_path, "twopole_amp", [
        (GOOD, GOOD_MEASURED, None),
        (GOOD, GOOD_MEASURED, None),
    ])
    _write_run(tmp_path, "ota_5t", [
        ({"ibias": 2e-5, "l": 5e-7, "wpair": 1e-5, "wload": 1e-5},
         {"loop_gain_db": 40.0, "f_crossover": 6e6,
          "phase_margin": 80.0, "power": 7e-5}, None),
    ])
    library = priors.harvest("twopole_amp", directory=str(tmp_path))
    assert len(library) == 1


def test_pick_chooses_by_margin_under_the_new_targets():
    library = [
        {"params": POOR, "measured": POOR_MEASURED},
        {"params": GOOD, "measured": GOOD_MEASURED},
    ]
    found = priors.pick("twopole_amp",
                        {"phase_margin": 60.0, "f_crossover": 1e5}, library)
    assert found["params"] == GOOD
    assert found["stored_margin"] > 0.0


def test_warm_start_is_none_on_an_empty_ledger(tmp_path):
    assert priors.warm_start("twopole_amp", {}, directory=str(tmp_path)) \
        is None


def test_warm_start_completes_the_parameter_set(tmp_path):
    _write_run(tmp_path, "twopole_amp", [(GOOD, GOOD_MEASURED, None)])
    found = priors.warm_start("twopole_amp", {"phase_margin": 60.0},
                              directory=str(tmp_path))
    assert found["params"]["gbw"] == GOOD["gbw"]
    # The untuned parameters arrive from the registry defaults.
    assert "rin" in found["params"] and "a0" in found["params"]
    assert found["library_size"] == 1


def test_random_specs_scatter_inside_the_span():
    rng = random.Random(3)
    specs = priors.random_specs("twopole_amp", 20, rng, span=(0.7, 1.4))
    assert len(specs) == 20
    block = circuits.get_circuit("twopole_amp")["design"]
    for spec in specs:
        for item in block["goals"]:
            assert item["default"] * 0.7 <= spec[item["key"]] \
                <= item["default"] * 1.4


@requires_ngspice
def test_the_curve_ledger_tells_cold_from_warm(tmp_path, monkeypatch):
    monkeypatch.setenv("FARADAEM_LEDGER", str(tmp_path))
    book = ledger.Ledger(directory=str(tmp_path), stamp_provenance=False)
    rng = random.Random(2)
    specs = priors.random_specs("twopole_amp", 2, rng)
    priors.learning_curve("twopole_amp", specs, per_spec=10, book=book)
    arms = {record["arm"]
            for record in ledger.read(book.path)["records"]
            if "arm" in record}
    assert {"cold", "warm"} <= arms


@requires_ngspice
def test_a_live_curve_counts_both_arms_and_grows_its_library(tmp_path,
                                                             monkeypatch):
    monkeypatch.setenv("FARADAEM_LEDGER", str(tmp_path))
    rng = random.Random(11)
    specs = priors.random_specs("twopole_amp", 3, rng)
    found = priors.learning_curve("twopole_amp", specs, per_spec=15)
    assert len(found["rows"]) == 3
    first, later = found["rows"][0], found["rows"][1:]
    assert first["warm"]["start"].startswith("centre")
    for row in later:
        assert row["warm"]["start"] == "library"
        assert row["library_size_before"] > 0
    for row in found["rows"]:
        assert row["cold"]["sims"] >= 1
        assert row["warm"]["sims"] >= 1
    summary = found["summary"]
    assert summary["cold_sims_total"] == sum(
        row["cold"]["sims"] for row in found["rows"])
