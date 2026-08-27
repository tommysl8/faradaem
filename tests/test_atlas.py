"""The atlas: honest cells, a measured frontier, a store beside the ledger.

The grid logic and the claims are proved on fabricated charts; the live
chart runs on the two-pole macromodel, which costs milliseconds per
simulation, so a real frontier is drawn inside a test budget.
"""

import pytest

from spice import atlas
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


def test_axis_is_geometric_and_bounded():
    found = atlas.axis("phase_margin", 40.0, 80.0, 3)
    assert found["values"][0] == pytest.approx(40.0)
    assert found["values"][-1] == pytest.approx(80.0)
    ratio = found["values"][1] / found["values"][0]
    # The values are rounded to six significant figures on purpose, so the
    # ratios agree to the rounding, not to machine precision.
    assert found["values"][2] / found["values"][1] == pytest.approx(
        ratio, rel=1e-4)


def test_axis_refuses_bad_ranges_and_step_counts():
    with pytest.raises(atlas.AtlasError):
        atlas.axis("x", 10.0, 5.0, 3)
    with pytest.raises(atlas.AtlasError):
        atlas.axis("x", 1.0, 2.0, 1)
    with pytest.raises(atlas.AtlasError):
        atlas.axis("x", 1.0, 2.0, atlas.MAX_STEPS + 1)


def test_chart_refuses_an_axis_that_is_not_a_goal():
    with pytest.raises(atlas.AtlasError):
        atlas.chart("twopole_amp",
                    atlas.axis("no_such_goal", 1.0, 2.0, 2),
                    atlas.axis("phase_margin", 40.0, 80.0, 2))


def test_chart_refuses_one_goal_on_both_axes():
    with pytest.raises(atlas.AtlasError):
        atlas.chart("twopole_amp",
                    atlas.axis("phase_margin", 40.0, 80.0, 2),
                    atlas.axis("phase_margin", 40.0, 80.0, 2))


def test_chart_refuses_seed_policy_without_a_seed_rule():
    with pytest.raises(atlas.AtlasError):
        atlas.chart("twopole_amp",
                    atlas.axis("f_crossover", 5e4, 2e5, 2),
                    atlas.axis("phase_margin", 40.0, 80.0, 2),
                    start="seed")


def _fake_chart():
    return {
        "axes": {
            "x": {"key": "f_crossover", "op": ">=",
                  "values": [1e5, 2e5, 4e5]},
            "y": {"key": "phase_margin", "op": ">=",
                  "values": [40.0, 60.0, 90.0]},
        },
        "cells": [
            {"ix": 0, "iy": 0, "x": 1e5, "y": 40.0, "verdict": "met"},
            {"ix": 1, "iy": 0, "x": 2e5, "y": 40.0, "verdict": "met"},
            {"ix": 2, "iy": 0, "x": 4e5, "y": 40.0, "verdict": "not_found"},
            {"ix": 0, "iy": 1, "x": 1e5, "y": 60.0, "verdict": "met"},
            {"ix": 1, "iy": 1, "x": 2e5, "y": 60.0, "verdict": "not_found"},
            {"ix": 2, "iy": 1, "x": 4e5, "y": 60.0, "verdict": "not_found"},
            {"ix": 0, "iy": 2, "x": 1e5, "y": 90.0, "verdict": "not_found"},
            {"ix": 1, "iy": 2, "x": 2e5, "y": 90.0, "verdict": "not_run"},
            {"ix": 2, "iy": 2, "x": 4e5, "y": 90.0, "verdict": "not_run"},
        ],
    }


def test_frontier_reports_the_hardest_met_target_per_column():
    found = atlas.frontier(_fake_chart())
    assert found[0]["hardest_y_met"] == 60.0
    assert found[1]["hardest_y_met"] == 40.0
    assert found[2]["hardest_y_met"] is None


def test_render_draws_hardest_first_and_marks_all_three_verdicts():
    text = atlas.render(_fake_chart())
    lines = text.splitlines()
    assert lines[0].strip().startswith("90")
    assert "#" in text and "." in text
    assert "not found within budget" in text


def test_store_load_and_listing_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("FARADAEM_LEDGER", str(tmp_path))
    chart = dict(_fake_chart(), circuit="twopole_amp", met=4, not_found=4,
                 not_run=1, sims=120, when_utc="2026-08-26 00:00:00")
    ident = atlas.store(chart)
    loaded = atlas.load(ident)
    assert loaded["circuit"] == "twopole_amp"
    assert loaded["id"] == ident
    rows = atlas.listing("twopole_amp")
    assert rows and rows[0]["id"] == ident
    assert rows[0]["grid"] == [3, 3]
    assert atlas.load("../escape") is None


@requires_ngspice
def test_a_stop_mid_cell_is_not_run_never_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("FARADAEM_LEDGER", str(tmp_path))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 1

    found = atlas.chart("twopole_amp",
                        atlas.axis("f_crossover", 1e5, 1e7, 2),
                        atlas.axis("phase_margin", 85.0, 88.0, 2),
                        per_cell=20, should_stop=stop)
    assert all(cell["verdict"] == "not_run" for cell in found["cells"])
    assert found["not_found"] == 0


@requires_ngspice
def test_a_live_chart_on_the_macromodel_finds_its_frontier(tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv("FARADAEM_LEDGER", str(tmp_path))
    # Around the defaults: 54 degrees and 314 kHz are the reference
    # measurements, so the easy corner of this grid must be met and the
    # grid must also contain cells the budget cannot reach.
    axis_x = atlas.axis("f_crossover", 1e5, 1e7, 3)
    axis_y = atlas.axis("phase_margin", 45.0, 88.0, 3)
    found = atlas.chart("twopole_amp", axis_x, axis_y, per_cell=20)
    assert len(found["cells"]) == 9
    assert found["met"] >= 1
    assert found["sims"] > 0
    assert found["met"] + found["not_found"] + found["not_run"] == 9
    for cell in found["cells"]:
        if cell["verdict"] == "met":
            assert cell["params"] is not None
            assert cell["worst_margin"] >= 0.0
        if cell["verdict"] == "not_found":
            assert cell["params"] is None
    ident = atlas.store(found)
    assert atlas.load(ident)["met"] == found["met"]
