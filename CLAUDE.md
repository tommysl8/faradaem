# Faradaem standing rules

- ngspice_con.exe only, never ngspice.exe.
- ngspice is the sole source of numerical truth. The LLM layer never computes a circuit value.
- Simulation temp files go to the system temp dir, never the project folder (OneDrive).
- Standard library only for the server. No new dependencies without asking.
- Run pytest before starting work and report the baseline. Report pass counts between phases. Never git commit.
- Netlist files are written with ascii encoding.
- Every version bump adds a changelog entry.
- The frontend stays zero-dependency: no frameworks, no CDNs, system fonts only.
- New circuits are added through the registry in spice/circuits.py, never as one-off routes.
- All spacing and type must come from the token scales in static/style.css. No ad hoc px values.
- UI copy is sentence case and directive: say what to do, not just what went wrong.
- Presets live in the registry alongside the circuit they demonstrate.
