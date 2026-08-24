# Faradaem

Faradaem is an AI-assisted analog IC design tool. It splits the work three ways so that
each part does only what it is good at: **ngspice is the ground truth**, and every number
Faradaem reports comes out of a real simulator run; a **numerical optimizer** does the
search through component and bias space; and an **LLM layer handles strategy only**,
choosing topologies, framing trade-offs, and deciding what to try next, never computing a
circuit value itself. The goal is a tool you hand a specification and get back a verified
schematic, with the verification done by the simulator rather than asserted by a model.

## Version map

- [x] **V0.0** resistor divider via ngspice
- [x] **V0.1** RC circuit + AC sweep + Bode plot
- [x] **V0.2** SKY130 MOSFET simulates
- [x] **V0.3** auto-measurement of gain/BW/PM/power
- [x] **V0.4** hand-designed two-stage op-amp
- [x] **V0.5** parameterized netlist
- [x] **V0.6** numerical optimizer hits spec
- [ ] **V0.7** LLM layer with tool calling
- [ ] **V0.8** topology selection
- [ ] **V0.9** PVT + Monte Carlo
- [ ] **V1.0** spec in, verified schematic out

0.1.5 expanded the circuit library to five circuits behind a data-driven registry
(`spice/circuits.py`): DC divider, RC low-pass, RC high-pass, series RLC band-pass, and an
inverting amplifier on a single-pole op-amp macromodel.

0.2.0 added the sixth, and the first built on a real process: a SKY130 NFET
common-source amplifier. Its bandwidth is set by the transistor's own output
resistance, so it is the first circuit here whose answer no closed form in the
codebase predicts. It needs the SKY130 PDK installed; without one it is the only
circuit that cannot run, and its tests skip.

0.3.0 completed the measurement set the roadmap names. Gain, bandwidth and power
were already measured; phase margin was not, and could not be, because it is a
property of the loop gain rather than of the closed-loop response. The seventh
circuit is a two-pole op-amp whose netlist carries the circuit twice, once closed
and once with the loop cut at the amplifier input, so a single sweep measures both.
Phase margin agrees with the closed form to 0.001 degrees.

0.4.0 to 0.6.0 delivered the op-amp generator's core. A hand-designed two-stage
Miller op-amp on SKY130 is measured open loop through a DC servo, every size and
current a bounded form field. On top of it sits the design iterator: declare
targets for gain, bandwidth, phase margin and power, and a coordinate search
walks the component space, running a real ngspice simulation for every candidate,
until the spec is met. From a failing start it reached a fully passing op-amp in
nine simulations.

0.6.1 made the flow spec-first: enter only the targets and the system creates the
starting design itself, measures it, and iterates only when the measurement falls
short. This is the Fall 2026 milestone from the project plan: enter op-amp
specifications, and the software finds a schematic design that meets them.

## How to run

```powershell
.\.venv\Scripts\Activate.ps1
python server.py
```

Then open <http://127.0.0.1:8000>. Four pages are served:

| Page | Path | What it is |
| --- | --- | --- |
| Simulator | `/` | Every circuit in the registry, behind one tab row. Set values or click a worked example, then press Enter or **Run simulation**. The schematic redraws as you type; measured numbers appear only once a run finishes. Where a closed form exists it sits beside the result as a check, never as a substitute. |
| Manual | `/manual` | How to run a simulation, how to read the result, what each warning means, and what to do when something goes wrong. |
| About | `/about` | What Faradaem is, how it works, and the roadmap. |
| Changelog | `/changelog` | Release notes, newest first. |

Static assets are served from `/static/` through a strict route whitelist; request paths
are never joined onto the filesystem.

## How to test

```powershell
pytest
```

The integration test drives real ngspice. It skips with a clear message on machines where
`ngspice_con.exe` cannot be found, so the rest of the suite still runs there.

## Layout

| Path | Role |
| --- | --- |
| `spice/runner.py` | Finds ngspice, runs netlists, parses output, and measures Bode and loop-gain responses. The only numerical-truth boundary. |
| `spice/circuits.py` | The circuit registry: topology, sweep framing, measurement, analytic checks and design surfaces, one entry per circuit. |
| `server.py` | Standard-library HTTP server: whitelisted GET routes, `GET /api/circuits`, `POST /api/simulate`, the design-job API under `/api/design`, plus the legacy `/simulate` and `/simulate_ac`. |
| `index.html`, `manual.html`, `about.html`, `changelog.html` | The four pages, sharing one nav and footer. |
| `static/style.css` | Design tokens and layout. One dark surface; schematics drawn in light grey and cyan directly on it. |
| `static/schematic.js` | SVG symbol primitives plus one compose function per circuit. |
| `static/bodeplot.js` | SVG Bode plot: stacked magnitude and phase axes over log frequency. |
| `static/app.js` | Mode switching, form handling, simulate calls, result and error rendering. |
| `spice/design.py` | The design iterator: goals, margins, and a coordinate search with a real simulator in the loop. |
| `tests/` | Netlist, parser, validation, routing, optimizer, manual-accuracy and real-ngspice integration tests. |

## Requirements

- Python 3.12
- The SKY130 PDK, for the NFET circuit only. `PDK_ROOT` should point at the install
  root; `C:\pdk` is used as a fallback. Every other circuit runs without it.
- ngspice-47, console build (`ngspice_con.exe`) on `PATH`, or `FARADAEM_NGSPICE` set to its
  full path. `C:\ngspice\Spice64\bin\ngspice_con.exe` is used as a last-resort fallback.
