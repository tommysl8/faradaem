# Faradaem V0.7 - the LLM layer, with tool calling

Slice 1 of V0.7. Follow it in order. Do not start V0.8.

## Goal

A chat panel on the simulator page takes a plain-English request and a
strategist drives the real tools: it chooses a circuit and targets, calls the
seed, simulate, and design machinery, iterates when measurements fall short,
and explains the outcome. The user types what they want; every number they see
was measured by ngspice.

## Hard rules

- The LLM never produces a circuit value. It proposes tool calls and explains
  tool results. Result cards in the UI are rendered from tool payloads, never
  from model prose.
- If the strategist concludes a target should be relaxed or changed, it asks
  the user. It never decides alone.
- Both the Anthropic and OpenAI APIs are supported, over stdlib urllib. No new
  dependencies; pytest stays the only one.
- Keys come from FARADAEM_ANTHROPIC_KEY and FARADAEM_OPENAI_KEY. On Windows
  the server also reads the user-scope registry through winreg, so a key set
  with setx works without restarting the shell that launched the server. Keys
  never appear in the repo, in logs, or in any response body.
- Model ids are overridable through FARADAEM_ANTHROPIC_MODEL and
  FARADAEM_OPENAI_MODEL, with sensible defaults in one place.
- The whole layer is testable without a network: a fake client scripted with
  canned model turns drives the same strategist loop the real clients do.

## Scope

1. `spice/llm.py`: provider clients with one shared interface,
   complete(messages, tools) returning text plus tool calls. Anthropic
   Messages API and OpenAI Chat Completions, each in its own class, plus the
   fake. Request building is pure and unit tested; only the send is live.
2. `spice/strategist.py`: the agent loop. System prompt encoding the rules,
   tool schemas for list_circuits, simulate, seed_design, and run_design, a
   bounded number of steps, and an event log the UI can poll: model text,
   tool calls with their measured results, questions to the user, and a final
   summary with the winning circuit and parameters.
3. Server: POST /api/advise starts a session job, GET /api/advise/status
   polls it, POST /api/advise/reply continues the conversation, and
   GET /api/advise/providers reports which providers have keys. Same job
   pattern as /api/design.
4. UI: an "Ask for a design" panel on the simulator page. A text box, a
   provider picker when more than one key exists, the event stream rendered
   in the house style, and an Apply button on the final result that loads the
   circuit and parameters into the form and runs one confirming simulation.
5. The comparison harness from the research plan: a script that runs the same
   spec through human design, optimizer only, LLM only, and LLM plus
   optimizer, per provider, and writes a measured table. Rows that need a
   missing key are marked pending, not faked.
6. Tests for all of it against the fake client. Manual section, changelog
   entry, roadmap tick, README.

## Deliberately out of scope

- Topology invention. The strategist picks from the registry; new topologies
  are V0.8.
- Streaming responses. Polling is enough.
- Conversation persistence across server restarts.

## Standing rules that still apply

ngspice_con only. ngspice is the sole source of numerical truth. Temp files to
the system temp dir. Netlists ASCII. Circuits enter through the registry.
Spacing and type from the token scales. UI copy sentence case and directive,
no em dashes.
