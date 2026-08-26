"""The tapeout packet: everything a recipient needs, built in one breath.

A foundry, a collaborator, or a shuttle wants the same short list: the
GDS, the netlist it was verified against, the verdicts, and a README
saying what everything is. Assembling that by hand is error-prone in
exactly the way that ruins tapeouts: a clean report from Tuesday zipped
next to Wednesday's GDS.

So this module refuses to assemble anything. It BUILDS: one call renders
the geometry, writes the GDS, runs the foundry's deck over those same
shapes, runs the layout-versus-schematic engine at the same sizing, and
only if both pass does a packet exist. The README records the SHA-256 of
the GDS it describes and the provenance of the run that produced it, so
the binding between the drawing and its verdicts is checkable by anyone
holding the zip.

A packet with a failing verdict is not a packet. The refusal names the
failure instead.
"""

import base64
import hashlib
import io
import json
import time
import zipfile

from . import circuits, gds, klvs, layout, ledger, signoff


class PacketRefused(Exception):
    """The geometry did not earn a packet. The message says why."""


def build(circuit_id, params):
    """Build the packet for this sizing, verifying as it goes.

    Returns {"filename", "bytes", "manifest"}. Raises PacketRefused when
    the deck or the comparison fails, because shipping a known-bad cell
    politely is still shipping a known-bad cell.
    """
    if not circuits.has_floorplan(circuit_id):
        raise PacketRefused("The circuit " + repr(circuit_id) +
                            " has no layout, so there is nothing to pack.")
    if not signoff.available():
        raise PacketRefused("KLayout is not installed here, so the "
                            "foundry's deck cannot vouch for the geometry. "
                            "A packet without sign-off is not a packet.")
    if not klvs.available():
        raise PacketRefused("KLayout's comparison engine is not available, "
                            "so layout versus schematic cannot be checked.")

    values = circuits.defaults(circuit_id)
    values.update(params or {})

    # One set of shapes. Everything below measures or ships exactly this.
    shapes = circuits.layout_shapes(circuit_id, dict(values))

    deck = signoff.run_drc(shapes, circuit_id)
    if not deck["clean"]:
        raise PacketRefused(
            "The foundry's deck found %d violations (%s). Fix the layout; "
            "a packet is not built over a failing sign-off."
            % (deck["total"],
               ", ".join(sorted(deck["violations"])) or "unknown rules"))

    compared = klvs.compare(circuit_id, dict(values), shapes=shapes)
    if not compared["match"]:
        raise PacketRefused(
            "Layout versus schematic does not match, so the drawing is "
            "not the circuit. A packet is not built over a mismatch.")

    # The GDS, from the same shapes, with its ports named.
    block = circuits.get_circuit(circuit_id)["floorplan"]
    tech = layout.tech_constants()
    layers = layout.gds_layers()
    ordered, _ = layout.matched_layout(block["devices"](values),
                                       block.get("matched"))
    plan = layout.floorplan(
        ordered, tech,
        passives=circuits.drawable_passives(circuit_id, values))
    routed = layout.route(plan, circuits.circuit_nets(circuit_id, values),
                          tech)
    stream = gds.library(circuit_id.upper(), circuit_id.upper(), shapes,
                         labels=layout.net_labels(routed, layers))
    digest = hashlib.sha256(stream).hexdigest()

    netlist = klvs.cell_netlist(circuit_id, dict(values))
    provenance = ledger.provenance()
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    manifest = {
        "circuit": circuit_id,
        "when_utc": when,
        "sizing": values,
        "gds_sha256": digest,
        "signoff": {"clean": True, "sections": deck["sections"],
                    "shapes_checked": deck["shapes_checked"]},
        "lvs": {"match": True, "engine": "klayout"},
        "ports": sorted(routed),
        "provenance": provenance,
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(circuit_id + ".gds", stream)
        bundle.writestr(circuit_id + ".spice", netlist)
        bundle.writestr("signoff.json", json.dumps(deck, indent=1))
        bundle.writestr("manifest.json", json.dumps(manifest, indent=1))
        bundle.writestr("README.md", _readme(circuit_id, values, plan,
                                             routed, digest, deck,
                                             provenance, when))

    return {
        "filename": circuit_id + "-packet.zip",
        "bytes": buffer.getvalue(),
        "manifest": manifest,
    }


def _readme(circuit_id, values, plan, routed, digest, deck, provenance,
            when):
    circuit = circuits.get_circuit(circuit_id)
    git = provenance.get("git") or {}
    lines = [
        "# " + circuit["name"],
        "",
        "Built by Faradaem on " + when + ". Every file in this packet was "
        "produced in the same run from the same sizing; nothing was "
        "assembled from earlier results.",
        "",
        "## Files",
        "",
        "| file | what it is |",
        "| --- | --- |",
        "| " + circuit_id + ".gds | The cell, ports labelled on metal 2. "
        "SHA-256 " + digest + " |",
        "| " + circuit_id + ".spice | The netlist the layout was verified "
        "against, dummies included, the one KLayout's engine matched. |",
        "| signoff.json | The full report of the foundry's own deck: 0 "
        "violations over the " + ", ".join(deck["sections"]) +
        " sections, " + str(deck["shapes_checked"]) + " shapes checked. |",
        "| manifest.json | This packet's identity: sizing, digests, "
        "provenance. |",
        "",
        "## The cell",
        "",
        "%.1f x %.1f um, %.0f um2." % (plan["width_um"], plan["height_um"],
                                       plan["area_um2"]),
        "",
        "Ports: " + ", ".join(sorted(routed)) + ".",
        "",
        "## Sizing",
        "",
        "| parameter | value |",
        "| --- | --- |",
    ]
    for key in sorted(values):
        lines.append("| %s | %g |" % (key, values[key]))
    lines.extend([
        "",
        "## Provenance",
        "",
        "- git commit: " + str(git.get("commit")) +
        (" (uncommitted changes present)"
         if git.get("clean") is False else ""),
        "- ngspice: " + str((provenance.get("ngspice") or {})
                            .get("version")),
        "- PDK: " + str((provenance.get("pdk") or {}).get("version")),
        "- KLayout: " + str((provenance.get("klayout") or {})
                            .get("module")),
        "",
        "The GDS digest above binds this README to the exact geometry the "
        "verdicts describe. If the digest does not match the file, the "
        "packet has been tampered with or mixed, and none of the verdicts "
        "apply.",
        "",
    ])
    return "\n".join(lines)
