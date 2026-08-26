"""V0.7: providers, the strategist loop, and the advise API.

Nothing here touches the network. Request building is pinned as pure data,
the loop is driven by a scripted client, and the live-tool tests use the same
skip rules as everything else that needs ngspice.
"""

import json
import time

import pytest

import server
from spice import circuits, llm, strategist
from spice.runner import find_ngspice
from tests.test_routes import address, fetch  # noqa: F401 - shared fixture


def no_keys(monkeypatch):
    monkeypatch.setattr(llm, "read_setting", lambda name: "")


# ---- provider request building ---------------------------------------------


def test_anthropic_request_shape():
    client = llm.AnthropicClient(key="k", model="m")
    body = client.build_request(
        "SYS",
        [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "thinking",
             "tool_calls": [{"id": "t1", "name": "simulate", "arguments": {"a": 1}}]},
            {"role": "tool", "call_id": "t1", "content": "{\"ok\": true}"},
        ],
        strategist.TOOLS,
    )
    assert body["system"] == "SYS"
    assert body["model"] == "m"
    assert body["messages"][0] == {"role": "user", "content": "hello"}
    turn = body["messages"][1]
    assert turn["content"][0] == {"type": "text", "text": "thinking"}
    assert turn["content"][1]["type"] == "tool_use"
    assert turn["content"][1]["input"] == {"a": 1}
    result = body["messages"][2]
    assert result["content"][0]["type"] == "tool_result"
    assert result["content"][0]["tool_use_id"] == "t1"
    assert body["tools"][0]["input_schema"]["type"] == "object"


def test_openai_request_shape():
    client = llm.OpenAIClient(key="k", model="m")
    body = client.build_request(
        "SYS",
        [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "",
             "tool_calls": [{"id": "t1", "name": "simulate", "arguments": {"a": 1}}]},
            {"role": "tool", "call_id": "t1", "content": "{}"},
        ],
        strategist.TOOLS,
    )
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "hello"}
    call = body["messages"][2]["tool_calls"][0]
    assert call["function"]["name"] == "simulate"
    assert json.loads(call["function"]["arguments"]) == {"a": 1}
    assert body["messages"][3]["role"] == "tool"
    assert body["tools"][0]["function"]["parameters"]["type"] == "object"


def test_model_defaults_and_overrides(monkeypatch):
    no_keys(monkeypatch)
    assert llm.AnthropicClient(key="k").model == llm.ANTHROPIC_DEFAULT_MODEL
    assert llm.OpenAIClient(key="k").model == llm.OPENAI_DEFAULT_MODEL
    monkeypatch.setattr(llm, "read_setting",
                        lambda name: "custom" if name == llm.ANTHROPIC_MODEL_VAR else "")
    assert llm.AnthropicClient(key="k").model == "custom"


def test_get_client_names_the_missing_key(monkeypatch):
    no_keys(monkeypatch)
    with pytest.raises(llm.LlmError) as excinfo:
        llm.get_client("anthropic")
    assert llm.ANTHROPIC_KEY_VAR in str(excinfo.value)
    with pytest.raises(llm.LlmError) as excinfo:
        llm.get_client("openai")
    assert llm.OPENAI_KEY_VAR in str(excinfo.value)
    with pytest.raises(llm.LlmError):
        llm.get_client("gemini")


def test_available_providers_reports_only_keyed_ones(monkeypatch):
    monkeypatch.setattr(
        llm, "read_setting",
        lambda name: "k" if name == llm.OPENAI_KEY_VAR else "",
    )
    listing = llm.available_providers()
    assert [item["name"] for item in listing] == ["openai"]


# ---- the strategist loop, scripted -----------------------------------------


def events_of(client, run_tool_fn=None, messages=None):
    seen = []
    state = strategist.advise(
        client,
        messages if messages is not None else [{"role": "user", "text": "design"}],
        seen.append,
        run_tool_fn=run_tool_fn or (lambda name, args: ({"ok": True}, {"ok": True})),
    )
    return state, seen


def test_a_tool_driving_conversation_reaches_done():
    client = llm.FakeClient([
        {"text": "Looking at the catalogue.",
         "tool_calls": [{"id": "1", "name": "list_circuits", "arguments": {}}]},
        {"tool_calls": [{"id": "2", "name": "simulate",
                         "arguments": {"circuit": "divider", "params": {}}}]},
        {"text": "Done. The measured numbers are in the cards above."},
    ])
    state, seen = events_of(client)
    assert state == "done"
    kinds = [event["kind"] for event in seen]
    assert kinds == ["text", "tool", "tool", "done"]
    # The tool results went back into the conversation the model sees.
    last_request = client.requests[-1]
    assert any(item["role"] == "tool" for item in last_request["messages"])


def test_a_trailing_question_pauses_for_the_user():
    client = llm.FakeClient([
        {"text": "Power under 10 uW cannot hold 60 degrees here. "
                 "Relax power to 30 uW, or accept 45 degrees?"},
    ])
    state, seen = events_of(client)
    assert state == "question"
    assert seen[-1]["kind"] == "question"


def test_tool_failures_are_reported_and_the_loop_continues():
    def failing(name, args):
        raise circuits.CircuitInputError("that bias does not settle")

    client = llm.FakeClient([
        {"tool_calls": [{"id": "1", "name": "simulate",
                         "arguments": {"circuit": "opamp_two_stage", "params": {}}}]},
        {"text": "That candidate could not be measured, so I stopped."},
    ])
    state, seen = events_of(client, run_tool_fn=failing)
    assert state == "done"
    tool_event = [event for event in seen if event["kind"] == "tool"][0]
    assert tool_event["ok"] is False
    assert "does not settle" in tool_event["display"]["error"]


def test_the_turn_limit_is_a_named_error():
    endless = llm.FakeClient([
        {"tool_calls": [{"id": str(i), "name": "list_circuits", "arguments": {}}]}
        for i in range(strategist.MAX_TURNS + 2)
    ])
    state, seen = events_of(endless)
    assert state == "error"
    assert "turn limit" in seen[-1]["message"]


def test_should_stop_ends_the_session():
    client = llm.FakeClient([
        {"tool_calls": [{"id": "1", "name": "list_circuits", "arguments": {}}]},
        {"text": "never reached"},
    ])
    seen = []
    state = strategist.advise(
        client, [{"role": "user", "text": "x"}], seen.append,
        should_stop=lambda: True,
        run_tool_fn=lambda n, a: ({}, {}),
    )
    assert state == "stopped"


def test_provider_errors_surface_as_events():
    class Exploding:
        model = "x"

        def complete(self, system, messages, tools):
            raise llm.LlmError("The model provider returned HTTP 529.")

    seen = []
    state = strategist.advise(Exploding(), [{"role": "user", "text": "x"}], seen.append)
    assert state == "error"
    assert "529" in seen[-1]["message"]


# ---- the real tools ---------------------------------------------------------


def test_list_circuits_tool_drops_ui_only_fields():
    payload, display = strategist.run_tool("list_circuits", {})
    assert display["circuits"] == circuits.CIRCUIT_ORDER
    assert all("readout" not in entry for entry in payload)


def test_seed_design_tool_is_pure_arithmetic():
    payload, display = strategist.run_tool("seed_design", {
        "circuit": "opamp_two_stage",
        "targets": {"power": 5e-5},
    })
    assert payload["params"]["ibias"] == pytest.approx(5e-5 / 12.0)
    assert display["targets"]["phase_margin"] == 60.0


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(),
    reason="ngspice is not available, so live strategist tools cannot run",
)


@requires_ngspice
def test_simulate_tool_measures_and_strips_curves():
    payload, display = strategist.run_tool("simulate", {
        "circuit": "divider",
        "params": circuits.defaults("divider"),
    })
    assert payload["vout"] == pytest.approx(2.5, abs=1e-6)
    assert "freq" not in payload
    assert display["measured"]["vout"] == payload["vout"]


@requires_ngspice
def test_run_design_tool_returns_the_best_point():
    start = dict(circuits.defaults("twopole_amp"), fp2=1e4)
    payload, display = strategist.run_tool("run_design", {
        "circuit": "twopole_amp",
        "params": start,
        "targets": {},
    })
    assert payload["feasible"] is True
    assert payload["best"]["measured"]["phase_margin"] >= 60.0
    assert display["circuit"] == "twopole_amp"


@requires_ngspice
def test_scripted_session_against_real_tools_end_to_end():
    """The full loop with real measurements, no network."""
    start = dict(circuits.defaults("twopole_amp"), fp2=1e4)
    client = llm.FakeClient([
        {"text": "I will fix the phase margin.",
         "tool_calls": [{"id": "1", "name": "run_design",
                         "arguments": {"circuit": "twopole_amp",
                                       "params": start, "targets": {}}}]},
        {"text": "The iterator reports every target met; the measured "
                 "margins are in the card above."},
    ])
    seen = []
    state = strategist.advise(client, [{"role": "user", "text": "fix it"}], seen.append)
    assert state == "done"
    tool_event = [e for e in seen if e["kind"] == "tool"][0]
    assert tool_event["ok"] is True
    assert tool_event["display"]["best"]["measured"]["phase_margin"] >= 60.0


# ---- the API over a live socket ---------------------------------------------


def test_providers_endpoint_lists_what_has_keys(address, monkeypatch):
    monkeypatch.setattr(llm, "read_setting", lambda name: "")
    status, _, body = fetch(address, "/api/advise/providers")
    assert status == 200
    assert json.loads(body)["providers"] == []


def test_advise_without_a_key_is_a_400_naming_the_variable(address, monkeypatch):
    monkeypatch.setattr(llm, "read_setting", lambda name: "")
    status, _, body = fetch(address, "/api/advise", "POST",
                            json.dumps({"message": "design an op-amp"}))
    assert status == 400
    assert llm.ANTHROPIC_KEY_VAR in json.loads(body)["error"]


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"message": ""}, "'message'"),
        ({"message": "   "}, "'message'"),
        ({"message": "x" * 5000}, "characters"),
        ({}, "'message'"),
    ],
)
def test_advise_rejects_unusable_messages(address, body, fragment):
    status, _, payload = fetch(address, "/api/advise", "POST", json.dumps(body))
    assert status == 400
    assert fragment in json.loads(payload)["error"]


def test_advise_status_of_unknown_session_is_404(address):
    status, _, body = fetch(address, "/api/advise/status?job=nope")
    assert status == 404


def test_advise_reply_to_unknown_session_is_404(address):
    status, _, body = fetch(address, "/api/advise/reply", "POST",
                            json.dumps({"job": "nope", "message": "hi"}))
    assert status == 404


def test_full_session_over_http_with_a_scripted_model(address, monkeypatch):
    script = [
        {"text": "Working on it.",
         "tool_calls": [{"id": "1", "name": "list_circuits", "arguments": {}}]},
        {"text": "Here is the plan. Shall I relax the power target?"},
        {"text": "Understood. Everything is done."},
    ]
    fake = llm.FakeClient(script)
    monkeypatch.setattr(server.llm, "get_client", lambda provider: fake)

    status, _, body = fetch(address, "/api/advise", "POST",
                            json.dumps({"message": "make me an amplifier",
                                        "provider": "anthropic"}))
    assert status == 200
    job = json.loads(body)["job"]

    def wait_for(states, deadline=10.0):
        end = time.time() + deadline
        while time.time() < end:
            _, _, raw = fetch(address, "/api/advise/status?job=" + job)
            snap = json.loads(raw)
            if snap["status"] in states:
                return snap
            time.sleep(0.05)
        raise AssertionError("timed out waiting for " + repr(states))

    snap = wait_for({"question"})
    kinds = [event["kind"] for event in snap["events"]]
    assert kinds[0] == "user"
    assert "tool" in kinds
    assert snap["events"][-1]["kind"] == "question"

    # Replying while paused continues the same conversation.
    status, _, _ = fetch(address, "/api/advise/reply", "POST",
                         json.dumps({"job": job, "message": "yes, relax it"}))
    assert status == 200
    snap = wait_for({"done"})
    assert snap["events"][-1]["kind"] == "done"
    assert "done" in snap["events"][-1]["text"].lower()


@requires_ngspice
def test_run_design_card_params_reproduce_the_measurement():
    """Applying a card must land on the numbers the card shows.

    The iterator moves only the tunables, but the card carries the complete
    parameter set including fixed conditions like the load, because that is
    what the Apply button loads into the form.
    """
    start = {"fp2": 1e4, "gbw": 2e6}
    payload, display = strategist.run_tool("run_design", {
        "circuit": "twopole_amp",
        "params": start,
        "targets": {},
    })
    best = payload["best"]
    assert set(best["params"]) == set(circuits.defaults("twopole_amp"))
    replay = circuits.simulate("twopole_amp", dict(best["params"]))
    assert replay["phase_margin"] == pytest.approx(
        best["measured"]["phase_margin"], abs=1e-9
    )


# ---------------------------------------------------------------------------
# the loop closes: the strategist can lay a design out and have it checked
# ---------------------------------------------------------------------------


def test_the_strategist_can_lay_a_design_out():
    """A tool that turns a sentence into a sized schematic is a sizing
    tool. Laying it out and having a foundry's runset pass it is the other
    half of the claim, and it has to be reachable by the model."""
    names = [item["name"] for item in strategist.TOOLS]
    assert "lay_out" in names

    tool = [item for item in strategist.TOOLS if item["name"] == "lay_out"][0]
    properties = tool["schema"]["properties"]
    assert set(properties) == {"circuit", "params", "signoff"}
    assert properties["signoff"]["type"] == "boolean"


def test_the_prompt_describes_all_three_topologies():
    """It described two for as long as there were two. A model cannot
    choose a topology nobody told it about."""
    prompt = strategist.SYSTEM_PROMPT
    for name in ("opamp_two_stage", "ota_5t", "folded_cascode"):
        assert name in prompt, name
    assert "three SKY130 amplifiers" in prompt


def test_the_prompt_tells_it_to_lay_the_design_out():
    prompt = strategist.SYSTEM_PROMPT
    assert "lay_out" in prompt
    assert "half an answer" in prompt
    # And never to pass the fast check off as the deck.
    assert "never present the fast check as" in prompt
    # And to name what stays outside the cell.
    assert "What stays outside" in prompt
    assert "compensation network included" in prompt


def test_laying_out_a_circuit_that_has_no_layout_is_refused():
    with pytest.raises(circuits.NoFloorplanError):
        strategist.run_tool("lay_out", {
            "circuit": "divider",
            "params": circuits.defaults("divider"),
        })


def test_a_missing_signoff_tool_is_reported_and_never_passed():
    """The one result that would matter if it were wrong: a check that did
    not run must never come back looking like one that passed."""
    source = open(strategist.__file__, encoding="utf-8").read()
    assert '"ran": False' in source
    assert "was not run" in source
    # There is no branch that sets a clean signoff without running it.
    assert source.count('result["signoff"] = signoff.run_drc') == 1


@requires_ngspice
def test_lay_out_reports_geometry_checks_and_what_is_not_drawn():
    slim, card = strategist.run_tool("lay_out", {
        "circuit": "ota_5t",
        "params": circuits.defaults("ota_5t"),
    })
    assert slim["area_um2"] > 0
    assert slim["interconnect_f"] > 0
    assert slim["drc"]["clean"] is True
    assert slim["drc"]["rules_checked"] >= 35
    assert slim["lvs"]["match"] is True
    # The parts of the circuit no layout here draws, by name.
    assert slim["lvs"]["undrawn"]
    assert any("current source" in item for item in slim["lvs"]["undrawn"])
    # And the specs measured again with the drawn wiring loading them.
    assert slim["after_wiring"]
    assert card["circuit"] == "ota_5t"


# ---------------------------------------------------------------------------
# the third topology can be designed to a spec, like the other two
# ---------------------------------------------------------------------------


def test_all_three_amplifiers_are_designable():
    """The strategist can only choose among topologies it can size."""
    designable = {item["id"] for item in circuits.catalog()
                  if item.get("design")}
    assert {"opamp_two_stage", "ota_5t", "folded_cascode"} <= designable


def test_the_folded_cascode_seed_moves_with_the_targets():
    """Bias is what trades gain against speed in this topology, measured
    across a decade. A seed that ignored the targets would be a constant."""
    block = circuits.get_circuit("folded_cascode")["design"]
    base = circuits.defaults("folded_cascode")

    high_gain = block["seed"](
        {"loop_gain_db": 60.0, "f_crossover": 5e6, "phase_margin": 60.0,
         "power": 3e-4}, base)
    fast = block["seed"](
        {"loop_gain_db": 45.0, "f_crossover": 4e7, "phase_margin": 60.0,
         "power": 6e-4}, base)

    # More gain wants less current; more bandwidth wants more.
    assert high_gain["ibias"] < fast["ibias"]


def test_the_folded_cascode_seed_stays_inside_the_declared_range():
    spec = {item["key"]: item
            for item in circuits.get_circuit("folded_cascode")["params"]}
    block = circuits.get_circuit("folded_cascode")["design"]
    base = circuits.defaults("folded_cascode")

    for gain, speed, power in ((80.0, 1e5, 1e-6), (20.0, 1e9, 1.0)):
        seeded = block["seed"](
            {"loop_gain_db": gain, "f_crossover": speed,
             "phase_margin": 60.0, "power": power}, base)
        for key, value in seeded.items():
            assert spec[key]["min"] <= value <= spec[key]["max"], (key, value)
