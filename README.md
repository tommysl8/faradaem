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
- [x] **V0.7** LLM layer with tool calling
- [x] **V0.8** topology selection
- [x] **V0.9** PVT + Monte Carlo
- [x] **V1.0** spec in, verified schematic out
- [x] **V1.2** slew rate and settling, measured on a real step
- [x] **V1.3** swing, common-mode range, CMRR and PSRR
- [x] **V1.5** floorplan area, GDS output, and the specs measured again under the interconnect
- [x] **V1.6** the drawn geometry checked against the PDK rather than assumed
- [x] **V1.7** the p-channel devices drawn in an n-well, and the well checked
- [x] **V1.8** the nets routed, so the interconnect is measured off drawn metal
- [x] **V1.9** contacts, local interconnect and implants
- [x] **V1.10** gate contacts, two metal layers, taps, and layout versus schematic
- [x] **V1.11** matched pairs drawn common centroid, the loop closed on the drawn circuit, and the four-way comparison
- [x] **V1.12** the workbench: the self-writing datasheet, pins, blame, the corner autopsy, the tapeout packet, the notebook, and the doctor
- [x] **V1.13** the studies: the capability atlas, signoff as a budgeted attack, forensics, warm starts priced against cold, and the arena
- [x] **V1.14** the toolchain installs itself in one command, and the published site stops pretending to be the tool: explicit deployments, a real notebook and 404, validated imports, remembered designs, and every number counted from the code
- [ ] **V2.0** a real parasitic extraction in place of the priced wires, a second circuit family, and silicon through Tiny Tapeout

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

0.7.0 added the LLM layer the architecture was built for. A strategist takes a
plain-language request and drives the real tools over the Anthropic or OpenAI
API, both spoken through the standard library alone. It chooses, explains and
asks; ngspice measures. The four-way comparison harness (`compare.py`) runs
human, optimizer, LLM, and LLM-plus-optimizer against the same spec.

0.8.0 made topology a real decision: a five-transistor OTA joined the two-stage
op-amp, and the strategist chooses between them by the gain, speed and power the
request asks for. A netlist viewer shows the exact deck every simulation runs.

0.9.0 added the harder questions: a PVT suite across process corners, supply and
temperature, and Monte Carlo mismatch from the PDK's statistical models, both as
a Robustness panel and as a strategist tool, so a finished design gets verified
across conditions rather than demonstrated once.

1.0.0 closed the loop. In the acceptance run, one plain-English sentence became
a two-stage op-amp that meets every target at every one of eleven PVT
conditions, designed by a model driving the seed, simulate, iterate and corner
tools, with every number measured by ngspice.

1.10.0 closed the other one. The devices are drawn as a real stack, the nets are
routed on two metal layers joined by vias, the wells and substrate have their
taps, and the geometry is checked twice: against thirty-six rules read from the
PDK, and against the netlist ngspice actually ran. That second check is the one
that matters, because geometry can satisfy every spacing rule in the deck while
connecting a gate to the wrong net, and nothing about the picture would look
wrong.

The foundry's own deck runs over the result. The first time it did, it failed
geometry the hand checker had called clean, forty violations across three
rules, all of them a via needing more metal along one axis than it needs all
round. That is the case for installing real tools rather than writing more
checks, made as an experiment rather than an argument. What is still missing
is device sizes compared against the schematic, a field-solver extraction, and
the parts of each circuit that no layout here draws: the bias current, the
load, and the compensation network.

0.6.1 made the flow spec-first: enter only the targets and the system creates the
starting design itself, measures it, and iterates only when the measurement falls
short. This is the Fall 2026 milestone from the project plan: enter op-amp
specifications, and the software finds a schematic design that meets them.

## How to run

On a machine that has never seen Faradaem, once:

```powershell
python install.py
```

That fetches the two things Faradaem needs and is not: the console build of
ngspice, and the SKY130 technology files. Both land in `~/.faradaem/tools`,
where the tool looks for them on its own, so nothing goes on `PATH` and no
environment variable needs setting. Both downloads are pinned by SHA-256;
`python install.py --list` prints the URLs, sizes and hashes and fetches
nothing, if you would rather read before trusting. Nothing is installed with
pip: the archives are unpacked with the `tar` the operating system already
ships, and the server stays standard-library-only.

The PDK download is 21 MB rather than the 2.2 GB a full install costs, because
Faradaem is an analog tool: it needs the technology files (the ngspice model
library, the magic dimensions, the KLayout sign-off decks) and the primitive
devices, and none of the standard cells, IO pads or SRAM macros that the
gigabytes are.

Then, every time:

```powershell
.\.venv\Scripts\Activate.ps1
python server.py
```

Then open <http://127.0.0.1:8000>. Five pages are served:

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
| `static/stepplot.js` | SVG plots with a linear x axis: the step response over time, and the DC transfer curve over input voltage. |
| `static/layoutplot.js` | The layout drawn to scale in microns: devices, wells, taps and both metal layers, with a scale bar. |
| `static/app.js` | Mode switching, form handling, simulate calls, result and error rendering. |
| `spice/design.py` | The design iterator: goals, margins, and a coordinate search with a real simulator in the loop. |
| `spice/llm.py`, `spice/strategist.py` | The LLM layer: Anthropic and OpenAI clients over urllib, and the tool-driving strategist that never computes a value. |
| `spice/pvt.py` | PVT corners and Monte Carlo mismatch, done by editing the finished netlist text. |
| `spice/layout.py` | Placement, the device stack, the taps and the router: every dimension read from the PDK's own technology file. |
| `spice/gds.py` | A GDSII writer in the standard library, so the geometry can be opened in a layout tool. |
| `spice/drc.py` | Thirty-six design rules read from the PDK, checked while the geometry is drawn. The fast loop, not the answer. |
| `spice/signoff.py` | The answer: the SKY130 runset the PDK ships, run by KLayout over the same GDS. Reimplements nothing. |
| `spice/klvs.py` | Layout versus schematic by KLayout's engine: devices recognised from geometry, values measured from it, matched by topology. Plus the wires priced in ohms. |
| `spice/closeloop.py` | When the drawn wiring breaks a target, size against the wiring and draw again. |
| `spice/ledger.py` | Every attempt, measurement and decision, with the provenance that makes a run evidence. |
| `spice/experiment.py` | The four-way comparison, built so its answer would survive review. |
| `spice/charact.py` | The whole bench in one pass: the datasheet's numbers, stamped with what measured them. |
| `spice/pins.py` | Pin a sizing and its numbers; a later check says whether the stack drifted underneath. |
| `spice/blame.py` | Which knob moves the failing number, measured by finite differences, never intuited. |
| `spice/triage.py` | Is the spec feasible at this sizing, and the one-knob trade the bias current buys. |
| `spice/autopsy.py` | Which transistor gave up at which corner, in millivolts of saturation headroom. |
| `spice/packet.py` | The tapeout zip that verifies as it builds and refuses over a failing verdict. |
| `workbench.py` | One background job per circuit, counts observed at the simulator boundary. |
| `doctor.py` | Every dependency checked at once, with the exact fix for anything missing. |
| `install.py` | The one command a new machine needs: the simulator and the technology files, pinned, unpacked by the OS's own tar, into the one place the tool looks. |
| `spice/toolchain.py` | Where installed tools live. One definition, shared by the installer that writes and the resolvers that read. |
| `spice/lvs.py` | Layout versus schematic: what the drawing connects, worked out from the geometry, compared against the netlist that was simulated. |
| `compare.py` | The research harness: four ways to the same spec, measured head to head. |
| `spice/atlas.py` | The capability atlas: a grid over two spec axes, the shipped search at every cell, and the measured frontier of what was met within budget. |
| `spice/adversary.py` | Signoff as an attack: a budget spent trying to break the design across corners, supply, temperature and mismatch. The verdict carries its simulation count. |
| `spice/forensics.py` | The simulator run backwards: which condition explains an observed set of numbers, searched over a declared hypothesis space and validated on blind trials. |
| `spice/priors.py` | Warm starts harvested from the ledger at zero simulations, and the learning curve that prices experience in simulations saved. |
| `spice/arena.py` | The referee: any design method as one callable, run under the comparison's rules with the budget enforced at the subprocess. |
| `study.py` | The studies' command line: atlas, adversary, forensics, curve and arena, each end to end with every simulation in the ledger. |
| `tools/make_brand.py` | The mark: the AE ligature lifted out of a font file and baked into the favicon, the touch icon and the Open Graph card, all from one geometry. |
| `tools/build_static.py` | The published site: the pages, the assets and the catalogue, for a host that cannot run a simulator. |
| `tests/` | Netlist, parser, validation, routing, optimizer, manual-accuracy and real-ngspice integration tests. |

## Publishing

`python tools/build_static.py --out dist` writes the site a static host can
serve: the four pages, their assets, and the circuit catalogue. The pages ask
for the simulator, do not find one, and drop into a static mode that keeps the
live schematics and puts away everything that would need a measured number.
The simulator itself needs a host that runs a container with a disk, since it
needs ngspice and the technology files. `DEPLOY.md` covers both.

## Requirements

Python 3.12 and nothing else you have to arrange yourself: `python install.py`
supplies the rest, and `python doctor.py` says what this machine has, what it
lacks, and the exact fix for anything missing.

The two pieces, and where each is looked for, in order:

- **ngspice-47, console build** (`ngspice_con.exe`, never `ngspice.exe`).
  `FARADAEM_NGSPICE`, then `~/.faradaem/tools/ngspice`, then `PATH`, then
  `C:\ngspice\Spice64\bin\ngspice_con.exe`. The installed copy outranks `PATH`
  because it is the one Faradaem pinned; name your own with the environment
  variable, which outranks everything. Only Windows has an official prebuilt
  binary, so elsewhere the installer says so and points at your package
  manager.
- **The SKY130 PDK**, for the four SKY130 circuits and every layout check.
  `PDK_ROOT`, then `~/.faradaem/tools/pdk`, then `C:\pdk`. The other six
  circuits run without it.

Optional, and reported as absent rather than missing when it is not there:
KLayout 0.30.11, for the foundry sign-off deck and layout-versus-schematic,
and an `FARADAEM_ANTHROPIC_KEY` or `FARADAEM_OPENAI_KEY` for the strategist.
