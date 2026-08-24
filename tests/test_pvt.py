"""V0.9: PVT corners and Monte Carlo mismatch.

The transforms are proved as pure text edits; the live suites run a reduced
condition set so the tests stay minutes, not hours. Everything PDK-gated
skips cleanly as usual.
"""

import json

import pytest

import server
from spice import circuits, pvt, strategist
from spice.runner import find_ngspice, sky130_available
from tests.test_routes import address, fetch  # noqa: F401 - shared fixture

requires_pdk = pytest.mark.skipif(
    not sky130_available(),
    reason="the SKY130 model library is not installed",
)


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_live_pdk = pytest.mark.skipif(
    ngspice_missing() or not sky130_available(),
    reason="a real ngspice and the SKY130 model library are both needed",
)


# ---- the transform is a pure text edit -------------------------------------


@requires_pdk
def test_transform_edits_corner_supply_temperature_and_seed():
    net = circuits.build_netlist_preview("ota_5t", circuits.defaults("ota_5t"))
    out = pvt.make_transform("ss", 1.62, 125, 7)(net)
    assert "sky130.lib.spice ss" in out
    assert "sky130.lib.spice tt" not in out
    assert "Vdd vdd 0 DC 1.62" in out
    assert ".temp 125.0" in out
    assert ".options seed=7" in out
    assert out.index(".temp") < out.index(".control")


@requires_pdk
def test_transform_without_conditions_changes_nothing():
    net = circuits.build_netlist_preview("ota_5t", circuits.defaults("ota_5t"))
    assert pvt.make_transform()(net) == net


@requires_pdk
def test_transform_leaves_tt_alone():
    net = circuits.build_netlist_preview("ota_5t", circuits.defaults("ota_5t"))
    assert pvt.make_transform(corner="tt")(net) == net


def test_transform_refuses_a_netlist_without_the_anchor():
    with pytest.raises(pvt.PvtError) as excinfo:
        pvt.make_transform(corner="ss")("* nothing here\n.control\n.endc\n.end\n")
    assert "no tt library line" in str(excinfo.value)


def test_only_pdk_circuits_are_supported():
    assert pvt.supported("ota_5t")
    assert pvt.supported("opamp_two_stage")
    assert not pvt.supported("divider")
    with pytest.raises(pvt.PvtError):
        pvt.require_supported("twopole_amp")


def test_the_condition_set_is_labelled_and_bounded():
    labels = [item["label"] for item in pvt.PVT_CONDITIONS]
    assert len(labels) == len(set(labels))
    assert "tt nominal" in labels
    for item in pvt.PVT_CONDITIONS:
        assert item["corner"] in ("tt", "ss", "ff", "sf", "fs")
        assert 1.6 <= item["vdd"] <= 2.0
        assert -40 <= item["temp"] <= 125


# ---- live, on a reduced set -------------------------------------------------


@requires_live_pdk
def test_a_reduced_pvt_suite_measures_every_condition(monkeypatch):
    reduced = [
        {"label": "tt nominal", "corner": "tt", "vdd": 1.8, "temp": 27},
        {"label": "worst slow", "corner": "ss", "vdd": 1.62, "temp": 125},
    ]
    monkeypatch.setattr(pvt, "PVT_CONDITIONS", reduced)
    result = pvt.run_pvt("ota_5t", circuits.defaults("ota_5t"))
    assert len(result["rows"]) == 2
    assert all(row["error"] is None for row in result["rows"])
    nominal, worst = result["rows"]
    # Slow, hot and starved must not be faster than nominal.
    assert worst["measured"]["f_crossover"] < nominal["measured"]["f_crossover"]
    at = result["worst"]["f_crossover"]
    assert at["at"] == "worst slow"


@requires_live_pdk
def test_monte_carlo_samples_actually_vary():
    result = pvt.run_monte_carlo("ota_5t", circuits.defaults("ota_5t"), runs=4)
    assert len(result["rows"]) == 4
    assert all(row["error"] is None for row in result["rows"])
    stats = result["stats"]["loop_gain_db"]
    assert stats["samples"] == 4
    assert stats["sigma"] > 0.0
    assert stats["min"] <= stats["mean"] <= stats["max"]
    # Mismatch at tt should stay near the nominal measurement.
    assert stats["mean"] == pytest.approx(37.0, abs=2.0)


def test_monte_carlo_run_count_is_bounded():
    with pytest.raises(pvt.PvtError):
        pvt.run_monte_carlo("ota_5t", circuits.defaults("ota_5t"), runs=1)


# ---- the API ----------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda b: b.update(circuit="divider",
                            params=server.circuits.defaults("divider")),
         "no process corners"),
        (lambda b: b.update(mode="everything"), "'mode'"),
        (lambda b: b.update(mode="mc", runs=1), "between"),
        (lambda b: b.update(mode="mc", runs="many"), "whole number"),
    ],
)
def test_robust_requests_are_validated(address, mutate, fragment):
    body = {"circuit": "ota_5t", "params": circuits.defaults("ota_5t"),
            "mode": "pvt"}
    mutate(body)
    status, _, payload = fetch(address, "/api/robust", "POST", json.dumps(body))
    assert status == 400
    assert fragment in json.loads(payload)["error"]


def test_robust_status_of_unknown_job_is_404(address):
    status, _, _ = fetch(address, "/api/robust/status?job=nope")
    assert status == 404


# ---- the strategist's corners tool ------------------------------------------


def test_run_corners_is_a_declared_tool():
    names = [tool["name"] for tool in strategist.TOOLS]
    assert "run_corners" in names
    assert "corner" in strategist.SYSTEM_PROMPT


@requires_live_pdk
def test_run_corners_tool_reports_the_worst_case(monkeypatch):
    reduced = [
        {"label": "tt nominal", "corner": "tt", "vdd": 1.8, "temp": 27},
        {"label": "worst slow", "corner": "ss", "vdd": 1.62, "temp": 125},
    ]
    monkeypatch.setattr(pvt, "PVT_CONDITIONS", reduced)
    payload, display = strategist.run_tool("run_corners", {
        "circuit": "ota_5t", "params": {},
    })
    assert len(payload["rows"]) == 2
    assert payload["worst"]["f_crossover"]["at"] == "worst slow"
    assert display["circuit"] == "ota_5t"
