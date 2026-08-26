"""Running a circuit, and the name every other module imports.

The catalogue itself is in registry.py; what is here is what happens when
someone asks for a circuit to be run. One function per kind of answer:

    simulate         the sweep or the operating point, plus the checks
    run_step         the transient, as a settled waveform
    run_datasheet    rejection and range, from the four-copy deck
    run_layout       the geometry, checked, compared, and measured again
                     with its own interconnect hung back on the nets

Everything the catalogue and the three modules behind it define is
imported into this namespace, so a caller that has always said
``circuits.something`` still can. The split is about where code lives, not
about what anyone may reach.
"""

from __future__ import annotations

import base64
import math
import os
import tempfile

from . import drc, gds, layout, lvs, runner
from .runner import _fmt  # the shared netlist number formatter

from .errors import (
    BiasError, CircuitInputError, NoDatasheetError, NoFloorplanError,
    NoStepResponseError, UnknownCircuitError,
)
from .topologies import *          # noqa: F401,F403  -- see the docstring
from .benches import *             # noqa: F401,F403
from .measure import *             # noqa: F401,F403
from .registry import *            # noqa: F401,F403
from .registry import CIRCUITS, CIRCUIT_ORDER, catalog, defaults, get_circuit
from .topologies import (          # the private ones a star import skips
    _folded_cascode_core, _microns, _opamp_core, _ota_core, _twopole_stages,
)
from .benches import (
    _netlist, _pulse_source, _rejection_instances, _step_window,
)
from .measure import _with_curves


def _timeout(circuit, default):
    """A circuit may claim a longer budget than the shared default.

    The SKY130 circuits need one: loading the model library costs 10 to 30 s
    before any solving starts.
    """
    return circuit.get("timeout_s") or default


def _run_dc(circuit, params, transform=None):
    netlist = circuit["build"](params)
    if transform is not None:
        netlist = transform(netlist)
    stdout = runner.run_netlist(
        netlist,
        timeout_s=_timeout(circuit, runner.DEFAULT_TIMEOUT_S),
    )
    return circuit["measure"](stdout, params)


def _reserve_data_path():
    """A unique name with no file at it.

    Reserving the name and then deleting the file is what lets the runner
    tell "ngspice wrote nothing" apart from "something was already there".
    """
    handle, path = tempfile.mkstemp(
        suffix=".data", prefix=runner.TEMP_PREFIX, dir=tempfile.gettempdir()
    )
    os.close(handle)
    try:
        os.unlink(path)
    except OSError:
        pass
    return path


def _run_ac(circuit, params, transform=None):
    fstart, fstop = sweep_range(
        circuit["centre"](params), circuit.get("decades")
    )

    # Most circuits write one data file and their builder takes a single
    # path. A circuit that declares "outputs" takes a list, always, even for
    # one file, and all of them come from a single sweep so its responses
    # cannot disagree about where the samples fell.
    declares = "outputs" in circuit
    count = circuit.get("outputs", 1)
    paths = [_reserve_data_path() for _ in range(count)]

    if declares:
        netlist = circuit["build"](params, fstart, fstop, paths)
    else:
        netlist = circuit["build"](params, fstart, fstop, paths[0])

    # PVT and Monte Carlo runs modify the finished netlist text: the corner
    # in the .lib line, the supply, the temperature. The circuit builders
    # stay ignorant of all of it.
    if transform is not None:
        netlist = transform(netlist)

    texts, stdout = runner.run_ac_multi(
        netlist,
        paths,
        timeout_s=_timeout(circuit, runner.AC_TIMEOUT_S),
        with_stdout=True,
    )
    bodes = [
        runner.compute_bode(runner.parse_wrdata_complex(text)) for text in texts
    ]

    return circuit["measure"](
        bodes if declares else bodes[0], params, stdout
    )


def build_netlist_preview(circuit_id, params):
    """The exact netlist these values produce, without running it.

    Data-file paths are shown as placeholder names, because the real ones are
    throwaway temp files chosen at run time.
    """
    circuit = get_circuit(circuit_id)
    values = dict(params)

    if circuit["analysis"] == "dc":
        return circuit["build"](values)

    fstart, fstop = sweep_range(
        circuit["centre"](values), circuit.get("decades")
    )
    count = circuit.get("outputs", 1)
    placeholders = [
        "response.data" if count == 1 else "response%d.data" % (index + 1)
        for index in range(count)
    ]
    if "outputs" in circuit:
        return circuit["build"](values, fstart, fstop, placeholders)
    return circuit["build"](values, fstart, fstop, placeholders[0])


def has_step(circuit_id):
    """True when this circuit declares a step response."""
    return "step" in get_circuit(circuit_id)


def _decimate(points, limit):
    """Thin a series for drawing, keeping the first and last sample.

    Plain stride sampling: the waveform is already smooth at the timestep
    the run used, and anything cleverer would be a picture of a filter
    rather than a picture of the output.
    """
    if len(points) <= limit:
        return [[t, v] for t, v in points]
    stride = len(points) / float(limit)
    kept = [points[int(i * stride)] for i in range(limit)]
    kept[-1] = points[-1]
    return [[t, v] for t, v in kept]


def run_step(circuit_id, params, transform=None):
    """Run one circuit's step response and return what it measured.

    The waveform comes back with the numbers, thinned for drawing, because
    a slew rate without the edge it came from is a number nobody can check.
    """
    circuit = get_circuit(circuit_id)
    step = circuit.get("step")
    if step is None:
        raise NoStepResponseError(
            "The circuit " + repr(circuit_id) + " has no step response. "
            "The two SKY130 amplifiers do; pick one of those."
        )

    values = dict(params)
    window = step["window"](values)
    paths = [_reserve_data_path()]
    netlist = step["build"](values, window, paths)
    if transform is not None:
        netlist = transform(netlist)

    texts, stdout = runner.run_data_netlist(
        netlist, paths, timeout_s=runner.TRAN_TIMEOUT_S, with_stdout=True
    )
    points = runner.parse_wrdata_real(texts[0])
    measured = measure_step_response(points, values, window)
    measured["waveform"] = _decimate(points, WAVEFORM_POINTS)
    return measured


def has_datasheet(circuit_id):
    """True when this circuit declares a rejection and range run."""
    return "datasheet" in get_circuit(circuit_id)


def run_datasheet(circuit_id, params, transform=None):
    """Measure rejection and range, and return the transfer curve with them.

    Four copies of the amplifier go into one deck so that a single library
    load answers four questions. The curve comes back for drawing, because
    a range without the curve it was read off is a number nobody can check.
    """
    circuit = get_circuit(circuit_id)
    sheet = circuit.get("datasheet")
    if sheet is None:
        raise NoDatasheetError(
            "The circuit " + repr(circuit_id) + " has no rejection testbench. "
            "The two SKY130 amplifiers do; pick one of those."
        )

    values = dict(params)
    paths = [_reserve_data_path() for _ in range(4)]
    netlist = sheet["build"](values, paths)
    if transform is not None:
        netlist = transform(netlist)

    texts = runner.run_data_netlist(
        netlist, paths, timeout_s=runner.DATASHEET_TIMEOUT_S
    )
    transfer = runner.parse_wrdata_real(texts[0])
    bodes = [
        runner.compute_bode(runner.parse_wrdata_complex(text))
        for text in texts[1:]
    ]

    measured = measure_datasheet(bodes, transfer, values)
    measured["transfer"] = _decimate(transfer, WAVEFORM_POINTS)
    return measured


def has_floorplan(circuit_id):
    """True when this circuit can be floorplanned."""
    return "floorplan" in get_circuit(circuit_id)


def layout_shapes(circuit_id, params):
    """Just the geometry, without measuring anything.

    run_layout draws and then simulates twice, which is the right thing
    when the question is what the interconnect costs. When the question is
    whether the geometry is legal, those two simulations are a minute of
    work nobody asked for.
    """
    circuit = get_circuit(circuit_id)
    block = circuit.get("floorplan")
    if block is None:
        raise NoFloorplanError(
            "The circuit " + repr(circuit_id) + " has no layout to check."
        )

    values = dict(params)
    try:
        tech = layout.tech_constants()
    except layout.LayoutDataError as exc:
        raise NoFloorplanError(str(exc)) from None

    layers = layout.gds_layers()
    plan = layout.floorplan(block["devices"](values), tech)
    routed = layout.route(plan, circuit_nets(circuit_id, values), tech)
    return (layout.floorplan_shapes(plan, layers, tech)
            + layout.routing_shapes(routed, layers))


def run_layout(circuit_id, params):
    """Area, interconnect, and the specs measured again with it loading them.

    Two simulations: the circuit as drawn on the schematic, and the same
    circuit with each net's wire capacitance hung on it. The difference is
    what the interconnect costs, and it is measured rather than asserted.
    """
    circuit = get_circuit(circuit_id)
    block = circuit.get("floorplan")
    if block is None:
        raise NoFloorplanError(
            "The circuit " + repr(circuit_id) + " has no floorplan. The two "
            "SKY130 amplifiers do; pick one of those."
        )

    values = dict(params)
    try:
        tech = layout.tech_constants()
    except layout.LayoutDataError as exc:
        raise NoFloorplanError(str(exc)) from None

    plan = layout.floorplan(block["devices"](values), tech)

    # Route the nets, then take the capacitance off the metal that was
    # actually drawn. The bounding-box estimate this replaces was
    # optimistic by nearly half, because it counted the run across the row
    # and not the stubs down onto devices tens of microns tall.
    routed = layout.route(plan, circuit_nets(circuit_id, values), tech)
    parasitics = layout.routed_parasitics(routed, tech)

    clean = simulate(circuit_id, values)
    loaded = simulate(circuit_id, values,
                      transform=layout.parasitic_transform(parasitics))

    keys = [item["key"] for item in
            [circuit["readout"]["headline"]] + list(circuit["readout"]["stats"])]
    comparison = []
    for key in keys:
        before = clean.get(key)
        after = loaded.get(key)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        comparison.append({
            "key": key,
            "before": before,
            "after": after,
            "change": after - before,
        })

    # The geometry, in the format every layout tool reads, checked against
    # the rules it was drawn to satisfy. It is the same placement the area
    # was measured over, so the file, the picture and the numbers cannot
    # disagree with each other.
    checked = None
    compared = None
    try:
        layers = layout.gds_layers()
        shapes = (layout.floorplan_shapes(plan, layers, tech)
                  + layout.routing_shapes(routed, layers))
        pmos = [(item["x"], item["y"],
                 item["x"] + item["width"], item["y"] + item["height"])
                for item in plan["devices"] if item.get("kind") == "pfet"]
        checked = drc.check(shapes, layers, tech, pmos=pmos)
        # And the question no rule check asks: is this the right circuit.
        compared = lvs.compare(
            shapes, layers, circuit_devices(circuit_id, values),
            [item["name"] for item in plan["devices"]],
            undrawn=circuit_elements(circuit_id, values)
        )
        stream = gds.library(circuit_id.upper(), circuit_id.upper(),
                             shapes)
        encoded = base64.b64encode(stream).decode("ascii")
    except layout.LayoutDataError:
        encoded = None

    return {
        "floorplan": plan,
        "routing": routed,
        "gds_base64": encoded,
        "gds_bytes": len(stream) if encoded else 0,
        "drc": checked,
        "lvs": compared,
        "parasitics": parasitics,
        "total_parasitic_f": sum(item["capacitance_f"]
                                 for item in parasitics.values()),
        "comparison": comparison,
        "tech": {name: tech[name] for name in sorted(tech)},
    }


def simulate(circuit_id, params, transform=None):
    """Run one catalogue circuit and return its measurements plus the checks.

    The returned dict is the measurement, with an "analytic" object alongside
    holding what each check expected.  Comparing them is the caller's job; this
    function never reconciles the two.

    transform, when given, edits the finished netlist text before it runs.
    The PVT and Monte Carlo machinery lives on this hook.
    """
    circuit = get_circuit(circuit_id)
    values = dict(params)

    if circuit["analysis"] == "dc":
        result = _run_dc(circuit, values, transform)
    else:
        result = _run_ac(circuit, values, transform)

    result["analytic"] = analytic_values(circuit_id, values)
    return result
