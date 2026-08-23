"""Faradaem ngspice integration package.

Everything that touches a real simulator lives here.  V0.0 added the
resistor-divider DC path; V0.1 adds the RC low-pass AC sweep alongside it.
Later versions add further builders, parsers and measurements in
``spice.runner`` without changing the process-invocation contract.
"""

from .runner import (  # noqa: F401
    NgspiceNotFoundError,
    NgspiceParseError,
    NgspiceRunError,
    build_divider_netlist,
    build_rc_lowpass_netlist,
    compute_bode,
    find_ngspice,
    measure_lowpass,
    parse_vout,
    parse_wrdata_complex,
    run_ac_netlist,
    run_netlist,
    simulate_divider,
    simulate_rc_lowpass,
    unwrap_degrees,
)

__all__ = [
    "NgspiceNotFoundError",
    "NgspiceParseError",
    "NgspiceRunError",
    "build_divider_netlist",
    "build_rc_lowpass_netlist",
    "compute_bode",
    "find_ngspice",
    "measure_lowpass",
    "parse_vout",
    "parse_wrdata_complex",
    "run_ac_netlist",
    "run_netlist",
    "simulate_divider",
    "simulate_rc_lowpass",
    "unwrap_degrees",
]
