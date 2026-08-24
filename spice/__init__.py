"""Faradaem ngspice integration package.

Everything that touches a real simulator lives here.  V0.0 added the
resistor-divider DC path; V0.1 adds the RC low-pass AC sweep alongside it;
V0.2 adds the SKY130 PDK and the first real device.  Later versions add further
builders, parsers and measurements in ``spice.runner`` without changing the
process-invocation contract.
"""

from .runner import (  # noqa: F401
    NgspiceNotFoundError,
    NgspiceParseError,
    NgspiceRunError,
    PdkNotFoundError,
    build_divider_netlist,
    build_rc_lowpass_netlist,
    compute_bode,
    find_ngspice,
    find_sky130_lib,
    measure_lowpass,
    parse_op_values,
    parse_vout,
    parse_wrdata_complex,
    pdk_root,
    run_ac_netlist,
    run_netlist,
    simulate_divider,
    simulate_rc_lowpass,
    sky130_available,
    sky130_lib_path,
    unwrap_degrees,
)

__all__ = [
    "NgspiceNotFoundError",
    "NgspiceParseError",
    "NgspiceRunError",
    "PdkNotFoundError",
    "build_divider_netlist",
    "build_rc_lowpass_netlist",
    "compute_bode",
    "find_ngspice",
    "find_sky130_lib",
    "measure_lowpass",
    "parse_op_values",
    "parse_vout",
    "parse_wrdata_complex",
    "pdk_root",
    "run_ac_netlist",
    "run_netlist",
    "simulate_divider",
    "simulate_rc_lowpass",
    "sky130_available",
    "sky130_lib_path",
    "unwrap_degrees",
]
