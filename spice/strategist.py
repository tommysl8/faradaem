"""The strategist: a model driving the real tools, never computing a value.

The division of labour is the project's founding rule, applied to an agent.
The model reads the user's request, decides which circuit and targets fit,
and calls tools. The tools are the same registry, simulator and iterator the
rest of Faradaem runs on, so every number that comes back was measured. The
model's prose is displayed as prose; result cards in the UI are rendered from
tool payloads, and nothing the model asserts numerically is ever presented as
a measurement.

The loop is bounded, its whole history is an event list the UI can poll, and
a model that wants to change the user's targets has one way to do it: ask.
"""

from __future__ import annotations

import json

from . import circuits, design, runner

#: The most model turns one request may take. Each turn may carry tool calls.
MAX_TURNS = 12

#: The most simulations the strategist's own design runs may spend.
DESIGN_BUDGET = 40

#: Tool results are clipped before going back to the model, so a Bode curve
#: with hundreds of points does not flood the conversation.
MAX_RESULT_CHARS = 2000

SYSTEM_PROMPT = """You are the design strategist inside Faradaem, an analog \
circuit tool in which ngspice is the sole source of numerical truth.

Your job: turn the user's plain-language request into a working, measured \
design, by driving tools.

Rules you must follow:
- Never state a circuit value or measurement you did not receive from a tool \
result in this conversation. You choose and explain; the simulator measures.
- Work with the catalogued circuits from list_circuits. Circuits that declare \
a design block can be optimized, and the two amplifier topologies can be \
seeded from targets.
- Choose the topology to fit the request. opamp_two_stage is the two-stage \
Miller op-amp: around 70 dB of gain, needs compensation. ota_5t is the \
single-stage OTA: around 37 to 43 dB, near 90 degrees of margin, and better \
bandwidth per microwatt. High gain points at the two-stage; modest gain \
with speed or power pressure points at the OTA. Say which you chose and why.
- Prefer the flow: pick the circuit, resolve targets from the request, \
seed_design if available, simulate the seed, and if any target is missed, \
run_design to iterate. Quote measured numbers from the tool results.
- If the request is impossible or targets should be relaxed, do not decide \
alone: end your reply with a clear question to the user and no tool calls.
- When the design is done, summarise what was asked, what was measured, and \
what trade-offs were made. Keep it short and plain.
- The user sees your text and, separately, cards for every tool result. Do \
not paste raw tool JSON into your prose."""

#: The exact circuit ids, baked into the schemas so the model cannot guess.
_CIRCUIT_IDS = list(circuits.CIRCUIT_ORDER)

_CIRCUIT_PROP = {"type": "string", "enum": _CIRCUIT_IDS}

#: Targets are flat numbers keyed by goal key. The op-amp's goal keys and
#: units are named right here because getting them wrong costs a real turn.
_TARGETS_PROP = {
    "type": "object",
    "description": "Flat mapping of goal key to a plain number. For "
                   "opamp_two_stage and ota_5t the keys are loop_gain_db "
                   "(dB), f_crossover (Hz), unity-gain bandwidth, "
                   "phase_margin (degrees) and power (watts). For "
                   "twopole_amp: phase_margin and f_crossover. Omitted "
                   "keys use the circuit's default targets.",
    "additionalProperties": {"type": "number"},
}

TOOLS = [
    {
        "name": "list_circuits",
        "description": "The circuit catalogue: parameters with ranges and "
                       "defaults, presets, and for designable circuits the "
                       "tunable parameters and goal targets.",
        "schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "simulate",
        "description": "Run one circuit through ngspice and return its "
                       "measurements. params must carry every parameter.",
        "schema": {
            "type": "object",
            "properties": {
                "circuit": _CIRCUIT_PROP,
                "params": {"type": "object",
                           "additionalProperties": {"type": "number"}},
            },
            "required": ["circuit", "params"],
        },
    },
    {
        "name": "seed_design",
        "description": "Generate a starting parameter set for a circuit from "
                       "spec targets alone. Only circuits with a seed rule "
                       "(the op-amp). Fixed conditions like the load go in "
                       "params; goals go in targets.",
        "schema": {
            "type": "object",
            "properties": {
                "circuit": _CIRCUIT_PROP,
                "targets": _TARGETS_PROP,
                "params": {"type": "object",
                           "additionalProperties": {"type": "number"}},
            },
            "required": ["circuit"],
        },
    },
    {
        "name": "run_design",
        "description": "Iterate a designable circuit's tunable parameters "
                       "until every target is met or the budget runs out. "
                       "Each step is a real simulation; expect PDK circuits "
                       "to take minutes. Returns the best measured point.",
        "schema": {
            "type": "object",
            "properties": {
                "circuit": _CIRCUIT_PROP,
                "params": {"type": "object",
                           "additionalProperties": {"type": "number"}},
                "targets": _TARGETS_PROP,
            },
            "required": ["circuit", "params"],
        },
    },
]

#: Errors a tool may raise that are the candidate's fault, not the server's.
TOOL_ERRORS = (
    circuits.UnknownCircuitError,
    circuits.CircuitInputError,
    design.DesignError,
    runner.NgspiceParseError,
    runner.NgspiceRunError,
    runner.PdkNotFoundError,
    ValueError,
    KeyError,
    TypeError,
)


def _clip(payload):
    """A tool result as text for the model, clipped to a sane size."""
    text = json.dumps(payload)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + '... (clipped)"}'


def _strip_curves(result):
    """Measurements without the plotted curves: the model needs numbers."""
    slim = {
        key: value
        for key, value in result.items()
        if key not in ("freq", "mag_db", "phase_deg")
    }
    return slim


def _full_params(circuit_id, given):
    """The circuit's defaults with the model's values merged over them.

    A model reasonably passes only what matters, like the load capacitance.
    Every tool therefore treats params as overrides, never as a form that
    must be complete.
    """
    params = circuits.defaults(circuit_id)
    for key, value in (given or {}).items():
        if key not in params:
            raise circuits.CircuitInputError(
                "Unknown parameter " + repr(key) + " for circuit "
                + repr(circuit_id) + ". Its parameters are: "
                + ", ".join(sorted(params)) + "."
            )
        params[key] = float(value)
    return params


def run_tool(name, arguments, on_progress=None):
    """Execute one tool call against the real machinery.

    Returns (payload, display): payload goes back to the model, display is
    the JSON-ready version the UI renders as a card.
    """
    if name == "list_circuits":
        listing = circuits.catalog()
        for entry in listing:
            entry.pop("readout", None)
        return listing, {"circuits": [entry["id"] for entry in listing]}

    if name == "simulate":
        params = _full_params(arguments["circuit"], arguments.get("params"))
        result = circuits.simulate(arguments["circuit"], params)
        slim = _strip_curves(result)
        return slim, {"circuit": arguments["circuit"],
                      "params": params,
                      "measured": slim}

    if name == "seed_design":
        seeded, targets = design.seed_params(
            arguments["circuit"],
            arguments.get("targets") or {},
            _full_params(arguments["circuit"], arguments.get("params")),
        )
        return ({"params": seeded, "targets": targets},
                {"circuit": arguments["circuit"], "params": seeded,
                 "targets": targets})

    if name == "run_design":
        start = _full_params(arguments["circuit"], arguments.get("params"))
        result = design.run_design(
            arguments["circuit"],
            start,
            arguments.get("targets") or {},
            DESIGN_BUDGET,
            on_eval=on_progress,
        )
        best = result["best"]
        # The iterator reports only the tunables it moved. The winning design
        # is those tunables over the full start, and that complete set is what
        # goes on the card: applying it must reproduce the measurement.
        full_best = None
        if best is not None:
            full_best = dict(start)
            full_best.update(best["params"])
        payload = {
            "feasible": result["feasible"],
            "evals": result["evals"],
            "reason": result["reason"],
            "targets": result["targets"],
            "best": None if best is None else {
                "params": full_best,
                "measured": best["measured"],
                "margins": best["margins"],
            },
        }
        return payload, dict(payload, circuit=arguments["circuit"])

    raise ValueError("Unknown tool " + repr(name))


def advise(client, messages, on_event, should_stop=None, run_tool_fn=run_tool,
           tools=None):
    """Drive one strategist session to an end state.

    messages is the provider-neutral history so far; the caller owns it and
    passes it back in for a continued conversation. on_event receives each
    step: {"kind": "text" | "tool" | "question" | "done" | "error", ...}.

    Returns the final state string: "done", "question", "stopped" or "error".
    """
    if tools is None:
        tools = TOOLS

    for _ in range(MAX_TURNS):
        if should_stop is not None and should_stop():
            on_event({"kind": "error", "message": "Stopped on request."})
            return "stopped"

        try:
            turn = client.complete(SYSTEM_PROMPT, messages, tools)
        except Exception as exc:  # noqa: BLE001 - provider boundary
            on_event({"kind": "error", "message": str(exc)})
            return "error"

        if turn["tool_calls"]:
            if turn["text"]:
                on_event({"kind": "text", "text": turn["text"]})
            messages.append({
                "role": "assistant",
                "text": turn["text"],
                "tool_calls": turn["tool_calls"],
            })
            for call in turn["tool_calls"]:
                try:
                    payload, display = run_tool_fn(call["name"], call["arguments"])
                    on_event({"kind": "tool", "tool": call["name"],
                              "ok": True, "display": display})
                    content = _clip(payload)
                except TOOL_ERRORS as exc:
                    # A KeyError's str() wraps the message in quotes.
                    message = (exc.args[0] if isinstance(exc, KeyError)
                               and exc.args else str(exc))
                    on_event({"kind": "tool", "tool": call["name"],
                              "ok": False, "display": {"error": message}})
                    content = json.dumps({"error": message})
                messages.append({
                    "role": "tool",
                    "call_id": call["id"],
                    "content": content,
                })
            continue

        # No tool calls: the model is either done or asking the user.
        text = turn["text"].strip()
        messages.append({"role": "assistant", "text": text})
        if text.rstrip().endswith("?"):
            on_event({"kind": "question", "text": text})
            return "question"
        on_event({"kind": "done", "text": text})
        return "done"

    on_event({
        "kind": "error",
        "message": "The strategist hit its turn limit without finishing. "
                   "Ask again with a narrower request.",
    })
    return "error"
