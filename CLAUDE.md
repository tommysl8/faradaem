# Faradaem standing rules

- ngspice_con.exe only, never ngspice.exe.
- ngspice is the sole source of numerical truth. The LLM layer never computes a circuit value.
- Simulation temp files go to the system temp dir, never the project folder (OneDrive).
- Standard library only for the server. No new dependencies without asking.
- Run pytest before starting work and report the baseline. Report pass counts between phases. Never git commit.
- Netlist files are written with ascii encoding.
- Every version bump adds a changelog entry and ticks the roadmap on about.html.
- A version number is minted when a state ships, not when a change is made. Work in an uncommitted tree is one release however many separate things it contains, and it gets one changelog entry written at the end. Three entries in one session for work that never shipped separately is a record of edits, not of releases.
- The frontend stays zero-dependency: no frameworks, no CDNs, system fonts only.
- New circuits are added through the registry in spice/circuits.py, never as one-off routes.
- All spacing and type must come from the token scales in static/style.css. No ad hoc px values.
- UI copy is sentence case and directive: say what to do, not just what went wrong.
- Presets live in the registry alongside the circuit they demonstrate.

## The studies (added for 1.13.0)
- The studies (`spice/atlas.py`, `spice/adversary.py`, `spice/forensics.py`, `spice/priors.py`, `spice/arena.py`, run by `study.py`) exist to measure folklore, so their copy makes only the claims the evidence supports. The atlas says "met" with a stored sizing or "not found within budget"; it never says impossible. The adversary's "survived" always carries its simulation count. Forensics names the hypothesis space it searched with every answer.
- Verdict sets are closed on purpose: atlas cells are met/not_found/not_run, attacks are broken/survived/unmeasurable, explanations are explained/unexplained. Add to them deliberately, never ad hoc.
- Every study's cost is counted by `runner.SimObserver` at the subprocess, like everything else. Atlas charts store beside the ledger at `<ledger-root>/atlas/`, same pattern as charact.
- A warm start from `spice/priors.py` is a measured past point chosen by re-scoring stored measurements; nothing is fitted, nothing interpolates, and the search that follows still measures everything fresh. In the learning curve the warm arm at spec N may only use experience from specs before N.
- Mismatch hypotheses and attacks run at the typical corner only, because the PDK carries no mismatch statistics at skewed corners (same limit as pvt.py). Forensics treats the die as a joint coordinate with the environment, because a mismatched typical die reads as a mildly skewed corner if you let it.
- The arena scores the design a contestant declares, re-simulated by the referee, never the best point it touched. The registry defaults are withheld from contestants: they are a designer's answer.

## The workbench (added for 1.12.0)
- `spice/charact.py` runs the whole bench in one pass; its store lives at `<ledger-root>/charact/`, beside the ledger, never in the project. `charact.describes()` is the exact staleness check.
- Pins (`spice/pins.py`) freeze sizing AND numbers; a check re-simulates the pinned sizing, never the form's. History never crosses a re-pin. Pins and history live beside the ledger.
- Datasheet extremes are labelled "worst observed", never min/max; the note says how many corners measured. The sweep is a one-knob slice and its copy must never say Pareto.
- Autopsy headroom is |vds| - |vdsat|, which is correct in either sign convention: SKY130 pfet primitives report magnitude-positive values (measured on the diode-tied mirror input), classical conventions report both negative. Never branch on device kind for this. Instances are read from the emitted deck (`autopsy._instances`), never guessed from the registry. A vector the model does not expose renders "not exposed", never 0.
- The packet (`spice/packet.py`) builds and verifies in one call and refuses over any failing verdict; the README carries the GDS SHA-256. Never assemble a packet from cached results.
- One workbench job per circuit at a time, enforced in `workbench.py` where it cannot be raced. Job sim counts come from `runner.SimObserver` at the subprocess boundary, reported as observed.
- The doctor resolves every dependency through the same functions the tool uses. Optional pieces are "absent", not failures.

## Matching and the experiment (added for 1.11.0)
- Matched pairs are declared per circuit in the registry as `"matched": [["M1","M2"], ...]`, and drawn common centroid: two fingers each, interleaved A B B A, with a dummy at each end. The test is exact: both centroids must be equal.
- A finger is named `M1@1`. `layout.device_of()` maps it back; `layout.is_dummy()` spots a dummy. Dummies are drawn, tied to their body rail, and declared in the LVS netlist, because the layout has them.
- Fingering makes one device into N in parallel. Both sides of the LVS comparison are combined with KLayout's `combine_devices`, never one side only.
- Simulations are counted at the two `subprocess.run` sites in `spice/runner.py`, never at the caller. Use `runner.SimObserver` + `runner.observing()`. A budget is enforced there too.
- The ledger (`spice/ledger.py`) writes to `~/.faradaem/ledger` or `FARADAEM_LEDGER`, never the project folder. `KINDS` and `AUTHORS` are closed sets on purpose.
- The comparison spec must be proved infeasible at the reference sizing before it is run. At the registry defaults every circuit already meets its own targets, which makes the experiment vacuous.

## Sign-off tooling (installed for 1.10.0)
- KLayout 0.30.11 is machine tooling, both as the `klayout` pip package (for reading GDS) and as the application at %APPDATA%\KLayout\klayout_app.exe (for running decks). Neither goes in requirements.txt. pytest stays the only dependency.
- The sign-off deck is the PDK's own: sky130A/libs.tech/klayout/drc/sky130A_mr.drc. Never copy a runset into the project; a copy drifts from the models it is supposed to match.
- spice/drc.py is the fast loop and must say so. spice/signoff.py is the answer. If they ever disagree, the deck is right and the fast loop has a rule missing.
- Never reimplement a check that a real tool performs. The point of installing them is to delete code, not to add a second opinion.
- Resolve the KLayout binary from the KLAYOUT_EXE env var first, then the known install paths. Tests that need it skip cleanly when it is absent, same pattern as ngspice and the PDK.
- Netgen has no Windows build and WSL is not installed. Real layout-versus-schematic runs through KLayout's in-process engine (spice/klvs.py): LayoutToNetlist + NetlistComparer, with Faradaem declaring only what the layers mean. The declaration is the trust boundary and the module says so.
- The generic poly resistor marker is 66/13 (POLYRES), read from the tech file like every other layer. The marker region is what LVS measures L/W over, so size the marker, not the strip.
- Drawn geometry must land on the manufacturing grid (gridlimit, 5 nm). Any dimension computed from ohms or farads gets snapped, and the drawn value is recomputed from the snapped geometry and reported.
- Wide metal (>3 um) is owed 0.28 um of spacing (met1.3b/met2.3b). A drawn plate capacitor is wide metal.

## SKY130 PDK (installed for 0.2.0)
- PDK installed at C:\pdk via ciel, pinned version 7b70722e33c03fcb5dabcf4d479fb0822d9251c9
- PDK_ROOT environment variable is set to C:\pdk (User scope)
- Model library: C:/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice, corners tt ss ff sf fs
- Resolve the PDK path from the PDK_ROOT env var with C:\pdk as fallback. Never hard-code the path anywhere else.
- SKY130 devices are subcircuits. Instantiate with an X prefix: XM1 d g s b sky130_fd_pr__nfet_01v8 W=1 L=0.15. A plain M line will fail.
- Use forward slashes in .lib paths inside netlists.
- The first simulation that loads the library takes 10 to 30 seconds. Subprocess timeouts for PDK circuits must allow at least 60 seconds.
- Integration tests that need the PDK must skip cleanly when PDK_ROOT or the library file is absent, same pattern as the existing ngspice skip.
- ciel is machine tooling. Never add it to requirements.txt. pytest stays the only dependency.
- The PDK must never live inside OneDrive.
