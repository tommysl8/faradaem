# Faradaem V0.3 - auto-measurement of gain, bandwidth, phase margin and power

Slice 1 of V0.3. Follow it in order. Do not start any later slice.

## Goal

Measure phase margin from a real loop gain sweep, and generalise power so it is
reported wherever a supply exists. Gain and bandwidth are already measured; this
slice completes the set the roadmap names.

## Why phase margin is different from everything measured so far

Every measurement in Faradaem today comes from a closed loop response. Phase
margin does not live there. It is a property of the loop gain, and no amount of
looking at a closed loop curve recovers it. The loop has to be opened and the
return ratio measured.

ngspice has no built-in stability analysis, so this slice builds the measurement
rather than calling for it.

## The approach, and why this one

For a linear circuit the loop can be broken exactly, with no probe and no
approximation. Break it at the amplifier input, which draws no current, so
breaking there loads nothing and introduces no error.

Both networks go in one netlist:

- the closed loop circuit, driven at its input, giving gain and bandwidth,
- a second, electrically separate copy with the loop broken, driven at the
  amplifier input, giving the loop gain.

They share only ground, so superposition keeps them independent and one AC sweep
measures both. Two `wrdata` calls write two files from that single run. This
costs one ngspice invocation, not two, and the two responses are guaranteed to
come from the same sweep grid.

The netlist emits the return ratio directly through a unity source, so the sign
convention lives in the netlist where it can be read, not in Python where it
would have to be remembered.

## Why a two-pole amplifier is needed

The existing inverting amplifier is a single-pole macromodel. A single-pole loop
has a phase margin of about 90 degrees no matter what you do to it, so it cannot
tell a correct phase margin measurement from one that simply returns 90. It is
not a test.

This slice adds a two-pole op-amp macromodel. Its phase margin is a real
closed form, it varies over a wide range as the second pole moves, and it is the
model the two-stage op-amp at V0.4 will be checked against.

## Scope

1. **Multi-output AC runs.** A circuit may declare more than one output file.
   One ngspice run, one sweep, several `wrdata` files, one bode per file.
   Circuits that declare nothing keep the current single-output path unchanged.
2. **`measure_loop`** in `spice/runner.py`: a pure function over a loop gain
   bode, returning the DC loop gain, the crossover frequency, the phase at
   crossover, and the phase margin. Provable against synthetic transfer
   functions with no simulator, like every other measurement in that module.
3. **A `twopole_amp` circuit**: two-pole op-amp macromodel in an inverting
   configuration, measuring closed loop gain and bandwidth from one network and
   phase margin from the other.
4. **Analytic checks** for it: closed loop gain, crossover frequency and phase
   margin all have exact closed forms. The crossover solves a quadratic in
   omega squared, not a numerical search.
5. **Power, generalised.** The SKY130 circuit already reports it from an
   operating point. Do the same for the DC divider, so power is a measurement
   the framework provides rather than something one circuit happens to have.
6. **Manual and docs.** The manual gains the new circuit and an explanation of
   what phase margin is and why it needs the loop opened. Changelog entry,
   version bump, roadmap tick on about.html and the README.

## Deliberately out of scope

- Gain margin. A two-pole loop approaches 180 degrees of phase shift but never
  reaches it, so its gain margin is infinite. Reporting a number there would be
  reporting an artifact of the sweep's upper limit. Gain margin waits for a
  model that can actually have one.
- Transistor-level phase margin. That is V0.4, and it is exactly why the probe
  is validated against a known closed form first.
- Any change to the SKY130 circuit's measurements.
- Automatic sweep refinement near crossover.

## Standing rules that still apply

ngspice_con only. ngspice is the sole source of numerical truth; the closed
forms are checks and are never substituted for a measurement. Temp files to the
system temp dir. Standard library only. Netlists written as ASCII. Circuits
enter through the registry. Spacing and type from the token scales. UI copy is
sentence case and directive, and uses no em dashes.
