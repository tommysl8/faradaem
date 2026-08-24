# Faradaem V0.2 — SKY130 MOSFET simulates

Slice 1 of V0.2. Follow it in order. Do not start any later slice.

## Goal

Put a real SKY130 device into the circuit library and prove it simulates
end to end through the existing stack: registry entry, ngspice run, Bode
measurement, schematic, result strip. One circuit, no framework.

The circuit is an **NFET common-source amplifier**: `sky130_fd_pr__nfet_01v8`
with a drain resistor to VDD, a gate biased by a DC source that also carries
the 1 V AC excitation, and an explicit load capacitor setting the output pole.

## Why this circuit

- It reuses the whole AC sweep, Bode plot and measurement path already built
  for V0.1. New surface is one registry entry, one schematic symbol and the
  PDK plumbing.
- Its bandwidth is set by the device's own output resistance in parallel with
  RD, which no closed form in the codebase predicts. Measured 10.5 MHz against
  a naive 1/(2*pi*RD*CL) of 7.96 MHz — a 32 percent gap that is physics, not
  arithmetic. This is the first result in Faradaem that cannot be obtained
  without a simulator.
- It is the first stage of the two-stage op-amp at V0.4.

## Scope

1. **PDK resolution** in `spice/runner.py`: resolve the SKY130 model library
   from `PDK_ROOT` with `C:\pdk` as fallback, forward slashes for netlists,
   `PdkNotFoundError` naming both attempts when the library file is absent.
2. **Timeouts**: a per-circuit `timeout_s` honoured by both run paths. The PDK
   circuit gets 90 s, because the first library load costs 10 to 30 s.
3. **Operating point capture**: `run_ac_netlist` can return stdout alongside the
   wrdata text, so an `op` in the same run reports Vds and Id.
4. **One registry circuit** `nfet_cs_amp` with W, L, Vgs, RD, CL and VDD, three
   simulator-verified presets, and a readout of midband gain, bandwidth, drain
   current, drain voltage and power.
5. **Bias handling**: a device that is off produces no −3 dB crossing at all, so
   the sweep cannot be measured. That must surface as a directive message, not a
   parse error. A device bottomed out in triode still measures, and is reported
   with a caution rather than refused.
6. **Schematic**: an `nmos` symbol primitive and a compose function.
7. **Tests** covering netlist generation, PDK resolution, bias classification and
   a real-ngspice integration test that skips cleanly when the PDK is absent.
8. **Docs**: tick V0.2 in `about.html` and the README version map, add a 0.2.0
   changelog entry, bump the server version.

## Deliberately out of scope

- Corner selection in the UI. The library is loaded at `tt` only; ss/ff/sf/fs
  are documented in CLAUDE.md and wired in a later slice.
- PFETs, and any second device circuit.
- Phase margin, power sweep, or an auto-measurement framework — that is V0.3.
- Parameterisation aimed at an optimiser — that is V0.5.
- Anything touching an LLM layer.

## No analytic check

Every circuit so far ships a closed-form check beside the measurement. This one
must not. Square law does not describe a 150 nm short-channel device, and a
formula that is permanently 30 percent off would read as a fault in the
simulator rather than as the known limit of the model. The registry entry
carries an empty `checks` list, and the readout shows the operating point
instead. Not shipping a wrong check is the point of the slice.

## Standing rules that still apply

`ngspice_con` only. ngspice is the sole source of numerical truth. Temp files to
the system temp dir. Standard library only. Netlists written as ASCII. Circuits
enter through the registry, never as a route. Spacing and type from the token
scales. UI copy sentence case and directive.
