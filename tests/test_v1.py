"""V1.0: the whole story, spec in, verified schematic out.

The strategist is scripted, because a canned model makes the test
deterministic. Everything else is real: the seed rule, ngspice on every
candidate, the iterator, and the PVT verification. This is the flow the
project was named for, pinned as a test.
"""

import pytest

from spice import circuits, llm, pvt, strategist
from spice.runner import find_ngspice, sky130_available


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

SPEC = ("An op-amp driving 5 pF: at least 60 dB, 60 degrees of margin, "
        "3 MHz of bandwidth, under 100 microwatts, verified across corners.")

TARGETS = {"loop_gain_db": 60.0, "f_crossover": 3e6,
           "phase_margin": 60.0, "power": 1e-4}


@requires_live_pdk
def test_spec_in_verified_schematic_out(monkeypatch):
    # Two conditions keep the corner check to half a minute; the full suite
    # is exercised by its own tests.
    monkeypatch.setattr(pvt, "PVT_CONDITIONS", [
        {"label": "tt nominal", "corner": "tt", "vdd": 1.8, "temp": 27},
        {"label": "worst slow", "corner": "ss", "vdd": 1.62, "temp": 125},
    ])

    script = [
        {"text": "Choosing the two-stage op-amp for the 60 dB requirement.",
         "tool_calls": [{"id": "1", "name": "seed_design",
                         "arguments": {"circuit": "opamp_two_stage",
                                       "targets": TARGETS,
                                       "params": {"cl": 5e-12}}}]},
        {"tool_calls": [{"id": "2", "name": "run_design",
                         "arguments": {"circuit": "opamp_two_stage",
                                       "params": "SEEDED",
                                       "targets": TARGETS}}]},
        {"tool_calls": [{"id": "3", "name": "run_corners",
                         "arguments": {"circuit": "opamp_two_stage",
                                       "params": "BEST"}}]},
        {"text": "Done. The design meets every target at the typical corner "
                 "and the worst case across corners is in the card above."},
    ]

    # The scripted client cannot know the seeded or optimized parameters in
    # advance, so the placeholders are resolved as the session produces them.
    state_box = {"seeded": None, "best": None}

    class Client:
        model = "scripted"

        def __init__(self):
            self.turns = list(script)

        def complete(self, system, messages, tools):
            turn = dict(self.turns.pop(0))
            calls = []
            for call in turn.get("tool_calls") or []:
                call = dict(call)
                arguments = dict(call["arguments"])
                if arguments.get("params") == "SEEDED":
                    arguments["params"] = state_box["seeded"]
                if arguments.get("params") == "BEST":
                    arguments["params"] = state_box["best"]
                call["arguments"] = arguments
                calls.append(call)
            return {"text": turn.get("text") or "", "tool_calls": calls,
                    "stop": "tool_use" if calls else "end"}

    events = []

    def on_event(event):
        events.append(event)
        if event["kind"] == "tool" and event["ok"]:
            display = event["display"]
            if event["tool"] == "seed_design":
                state_box["seeded"] = display["params"]
            if event["tool"] == "run_design" and display.get("best"):
                state_box["best"] = display["best"]["params"]

    state = strategist.advise(
        Client(), [{"role": "user", "text": SPEC}], on_event
    )

    assert state == "done"
    tools_used = [e["tool"] for e in events if e["kind"] == "tool" and e["ok"]]
    assert tools_used == ["seed_design", "run_design", "run_corners"]

    design_card = [e for e in events if e["kind"] == "tool"
                   and e.get("tool") == "run_design"][0]
    assert design_card["display"]["feasible"] is True
    best = design_card["display"]["best"]
    # The design's own load condition survived into the winning params.
    assert best["params"]["cl"] == pytest.approx(5e-12)

    corners_card = [e for e in events if e["kind"] == "tool"
                    and e.get("tool") == "run_corners"][0]
    worst = corners_card["display"]["worst"]
    # Verified across corners: the worst case still meets every target.
    assert worst["loop_gain_db"]["value"] >= TARGETS["loop_gain_db"]
    assert worst["f_crossover"]["value"] >= TARGETS["f_crossover"]
    assert worst["phase_margin"]["value"] >= TARGETS["phase_margin"]
    assert worst["power"]["value"] <= TARGETS["power"]

    # And the winning schematic is reproducible: rerunning the exact params
    # lands on the exact numbers.
    replay = circuits.simulate("opamp_two_stage", dict(best["params"]))
    assert replay["phase_margin"] == pytest.approx(
        best["measured"]["phase_margin"], abs=1e-9
    )
