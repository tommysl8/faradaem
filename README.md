# Faradaem

Faradaem is an AI-assisted analog IC design tool. It splits the work three ways so that
each part does only what it is good at: **ngspice is the ground truth**, and every number
Faradaem reports comes out of a real simulator run; a **numerical optimizer** does the
search through component and bias space; and an **LLM layer handles strategy only** —
choosing topologies, framing trade-offs, deciding what to try next — never computing a
circuit value itself. The goal is a tool you hand a specification and get back a verified
schematic, with the verification done by the simulator rather than asserted by a model.

## Version map

- [x] **V0.0** resistor divider via ngspice
- [x] **V0.1** RC circuit + AC sweep + Bode plot
- [ ] **V0.2** SKY130 MOSFET simulates
- [ ] **V0.3** auto-measurement of gain/BW/PM/power
- [ ] **V0.4** hand-designed two-stage op-amp
- [ ] **V0.5** parameterized netlist
- [ ] **V0.6** numerical optimizer hits spec
- [ ] **V0.7** LLM layer with tool calling
- [ ] **V0.8** topology selection
- [ ] **V0.9** PVT + Monte Carlo
- [ ] **V1.0** spec in, verified schematic out

0.1.5 expanded the circuit library to five circuits behind a data-driven registry
(`spice/circuits.py`): DC divider, RC low-pass, RC high-pass, series RLC band-pass, and an
inverting amplifier on a single-pole op-amp macromodel.

## How to run

```powershell
.\.venv\Scripts\Activate.ps1
python server.py
```

Then open <http://127.0.0.1:8000>. Three pages are served:

| Page | Path | What it is |
| --- | --- | --- |
| Simulator | `/` | Two circuits behind a mode switcher. **DC divider**: enter VDD, R1, R2 and get `v(out)` from a DC operating point. **RC low-pass**: enter R and C and get an AC sweep, a Bode plot, and the measured −3 dB corner. Either way the schematic redraws as you type, and pressing Enter or **Run simulation** runs ngspice. The analytic value sits beside the result as a check. |
| Changelog | `/changelog` | Release notes, newest first. |
| About | `/about` | What Faradaem is, how it works, and the roadmap. |

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
| `spice/runner.py` | Finds ngspice, runs netlists, parses output, and measures Bode responses. The only numerical-truth boundary. |
| `spice/circuits.py` | The circuit registry: topology, sweep framing, measurement and analytic checks, one entry per circuit. |
| `server.py` | Standard-library HTTP server: whitelisted GET routes, `GET /api/circuits`, `POST /api/simulate`, plus the legacy `/simulate` and `/simulate_ac`. |
| `index.html`, `about.html`, `changelog.html` | The three pages, sharing one nav and footer. |
| `static/style.css` | Design tokens and layout. Dark instrument chrome, paper datasheet figure. |
| `static/schematic.js` | SVG symbol primitives plus one compose function per circuit. |
| `static/bodeplot.js` | SVG Bode plot: stacked magnitude and phase axes over log frequency. |
| `static/app.js` | Mode switching, form handling, simulate calls, result and error rendering. |
| `tests/` | Netlist, parser, validation, routing and real-ngspice integration tests. |

## Requirements

- Python 3.12
- ngspice-47, console build (`ngspice_con.exe`) on `PATH`, or `FARADAEM_NGSPICE` set to its
  full path. `C:\ngspice\Spice64\bin\ngspice_con.exe` is used as a last-resort fallback.
