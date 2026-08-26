"""The GDSII writer: is what comes out actually the format it claims to be.

A writer can only be trusted if something else can read what it wrote, so
these parse the stream back with an independent reader written here and
compare against the geometry that went in. Nothing checks itself with the
same code that produced it.
"""

import struct

import pytest

from spice import circuits, gds, layout


# ---------------------------------------------------------------------------
# an independent reader, deliberately not sharing code with the writer
# ---------------------------------------------------------------------------


def records(stream):
    """Every record as (type, payload), by walking the length prefixes."""
    out = []
    index = 0
    while index < len(stream):
        length, kind = struct.unpack(">HH", stream[index:index + 4])
        assert length >= 4, "a record cannot be shorter than its header"
        out.append((kind, stream[index + 4:index + length]))
        index += length
    return out


def decode_real8(raw):
    """The excess-64 base-16 real, read back by hand."""
    sign = -1 if raw[0] & 0x80 else 1
    exponent = (raw[0] & 0x7F) - 64
    mantissa = int.from_bytes(raw[1:8], "big") / float(1 << 56)
    return sign * mantissa * (16.0 ** exponent)


def boundaries(stream):
    """Every boundary as (layer, datatype, points)."""
    found = []
    layer = datatype = None
    for kind, payload in records(stream):
        if kind == gds.LAYER:
            layer = struct.unpack(">h", payload)[0]
        elif kind == gds.DATATYPE:
            datatype = struct.unpack(">h", payload)[0]
        elif kind == gds.XY:
            points = [struct.unpack(">ii", payload[i:i + 8])
                      for i in range(0, len(payload), 8)]
            found.append((layer, datatype, points))
    return found


# ---------------------------------------------------------------------------
# the format itself
# ---------------------------------------------------------------------------


def test_the_stream_opens_and_closes_as_gdsii_must():
    stream = gds.library("LIB", "CELL", [(65, 20, 0.0, 0.0, 1.0, 2.0)])
    kinds = [kind for kind, _ in records(stream)]
    assert kinds[0] == gds.HEADER
    assert kinds[1] == gds.BGNLIB
    assert kinds[2] == gds.LIBNAME
    assert kinds[3] == gds.UNITS
    assert kinds[-1] == gds.ENDLIB
    assert kinds[-2] == gds.ENDSTR
    assert gds.BGNSTR in kinds and gds.STRNAME in kinds


def test_the_version_is_the_one_tools_expect():
    stream = gds.library("LIB", "CELL", [])
    kind, payload = records(stream)[0]
    assert kind == gds.HEADER
    assert struct.unpack(">h", payload)[0] == 600


def test_units_say_a_micron_and_a_nanometre():
    """Get this wrong and the file opens at the wrong scale everywhere."""
    stream = gds.library("LIB", "CELL", [])
    payload = [p for k, p in records(stream) if k == gds.UNITS][0]
    assert decode_real8(payload[0:8]) == pytest.approx(1e-3, rel=1e-9)
    assert decode_real8(payload[8:16]) == pytest.approx(1e-9, rel=1e-9)


def test_the_real_format_is_not_ieee():
    """One is not 0x3FF0... here. A writer that emits IEEE produces a file
    every tool misreads."""
    assert gds.real8(1.0) == b"\x41\x10\x00\x00\x00\x00\x00\x00"
    assert gds.real8(0.0) == b"\x00" * 8
    assert gds.real8(-1.0)[0] & 0x80


def test_reals_survive_a_round_trip():
    for value in (1e-9, 1e-3, 1.0, 0.5, 1234.5, -0.25):
        assert decode_real8(gds.real8(value)) == pytest.approx(value, rel=1e-12)


def test_every_record_length_is_even_and_declared():
    """An odd length is malformed, and a wrong one desynchronises a reader."""
    stream = gds.library("LIB", "CELL", [(65, 20, 0.0, 0.0, 1.5, 2.5)])
    index = 0
    while index < len(stream):
        length = struct.unpack(">H", stream[index:index + 2])[0]
        assert length % 2 == 0, "record length must be even"
        index += length
    assert index == len(stream), "the records must exactly fill the stream"


# ---------------------------------------------------------------------------
# the geometry
# ---------------------------------------------------------------------------


def test_a_rectangle_lands_where_it_was_asked_to():
    stream = gds.library("LIB", "CELL", [(65, 20, 0.0, 0.0, 1.0, 10.0)])
    layer, datatype, points = boundaries(stream)[0]
    assert (layer, datatype) == (65, 20)
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    # Microns in, nanometres out.
    assert min(xs) == 0 and max(xs) == 1000
    assert min(ys) == 0 and max(ys) == 10000


def test_a_boundary_is_closed():
    stream = gds.library("LIB", "CELL", [(65, 20, 0.0, 0.0, 1.0, 2.0)])
    _, _, points = boundaries(stream)[0]
    assert len(points) == 5
    assert points[0] == points[-1]


def test_coordinates_given_in_any_order_come_out_sorted():
    stream = gds.library("LIB", "CELL", [(65, 20, 2.0, 3.0, 1.0, 1.0)])
    _, _, points = boundaries(stream)[0]
    xs = [x for x, _ in points]
    assert min(xs) == 1000 and max(xs) == 2000


def test_a_record_too_large_is_refused():
    with pytest.raises(ValueError):
        gds._record(gds.XY, b"\x00" * 70000)


# ---------------------------------------------------------------------------
# the floorplan as geometry
# ---------------------------------------------------------------------------


def fake_tech():
    return {
        "poly_width": 0.15, "diff_overhang": 0.25, "diff_spacing": 0.27,
        "contact_width": 0.17, "metal1_width": 0.14,
        "metal1_area": 25.78e-18, "metal1_edge": 44.0e-18,
        "li_area": 36.99e-18, "li_edge": 25.5e-18, "poly_area": 106.13e-18,
        "poly_endcap": 0.13, "diff_width": 0.15,
        "contact_spacing": 0.17,
        "contact_surround": 0.04,
        "contact_to_gate": 0.055,
        "li_width": 0.17,
        "li_spacing": 0.17,
        "li_surround": 0.08,
        "implant_surround": 0.185,
        "nwell_width": 0.84, "nwell_spacing": 1.27, "nwell_surround": 0.18,
        "poly_contact_surround": 0.05,
        "poly_contact_to_diff": 0.19,
        "npc_surround": 0.1,
        "via_width": 0.17,
        "via_spacing": 0.19,
        "metal1_via_surround": 0.03,
        "metal2_width": 0.14,
        "metal2_spacing": 0.14,
        "via1_width": 0.15,
        "via1_spacing": 0.17,
        "via1_surround": 0.055,
        "metal1_spacing": 0.14,
        "ndiff_to_nwell": 0.34,
        "ptap_to_nwell": 0.13,
        "nwell_tap_surround": 0.18,
    }


def test_each_device_becomes_a_diffusion_and_a_gate():
    plan = layout.floorplan([("M1", 10e-6, 0.5e-6)], fake_tech())
    shapes = layout.floorplan_shapes(plan, {"DIFF": (65, 20), "POLY": (66, 20)}, fake_tech())
    assert len(shapes) == 2
    assert shapes[0][0] == 65
    assert shapes[1][0] == 66


def test_the_gate_sits_in_the_channel_and_overhangs_it():
    """A gate that does not cross the diffusion is not a transistor, and one
    that stops at its edge would fail the rule that says it must not."""
    plan = layout.floorplan([("M1", 10e-6, 0.5e-6)], fake_tech())
    shapes = layout.floorplan_shapes(plan, {"DIFF": (65, 20), "POLY": (66, 20)}, fake_tech())
    _, _, dx1, dy1, dx2, dy2 = shapes[0]
    _, _, gx1, gy1, gx2, gy2 = shapes[1]

    assert gx2 - gx1 == pytest.approx(0.5)          # the gate length
    assert dx1 < gx1 and gx2 < dx2                  # inside the diffusion
    assert gy1 < dy1 and gy2 > dy2                  # overhanging its ends
    # Centred: the same diffusion either side of the gate.
    assert (gx1 - dx1) == pytest.approx(dx2 - gx2)


def test_devices_keep_their_spacing_in_the_geometry():
    plan = layout.floorplan(
        [("M1", 10e-6, 0.5e-6), ("M2", 10e-6, 0.5e-6)], fake_tech()
    )
    shapes = layout.floorplan_shapes(plan, {"DIFF": (65, 20), "POLY": (66, 20)}, fake_tech())
    first = shapes[0]
    second = shapes[2]
    assert second[2] - first[4] == pytest.approx(0.27)


# ---------------------------------------------------------------------------
# live: the real layer numbers, and a real file
# ---------------------------------------------------------------------------


requires_pdk = pytest.mark.skipif(
    not layout.tech_available(),
    reason="the SKY130 technology file is needed for the layer numbers",
)


@requires_pdk
def test_the_layer_numbers_are_the_published_ones():
    """Read from the technology file, and checked against the SKY130 map:
    a wrong number produces a file that looks right and means nothing."""
    layers = layout.gds_layers()
    assert layers["DIFF"] == (65, 20)
    assert layers["POLY"] == (66, 20)
    assert layers["NWELL"] == (64, 20)
    assert layers["LI"] == (67, 20)
    assert layers["MET1"] == (68, 20)


@requires_pdk
def test_a_real_floorplan_writes_a_readable_file():
    tech = layout.tech_constants()
    plan = layout.floorplan(
        circuits.opamp_devices(circuits.defaults("opamp_two_stage")), tech
    )
    stream = layout.floorplan_gds(plan)

    shapes = boundaries(stream)

    # The whole stack, counted by layer rather than by a single total, so a
    # failure says which part of the device went missing.
    counts = {}
    for layer, datatype, _ in shapes:
        counts[(layer, datatype)] = counts.get((layer, datatype), 0) + 1

    assert counts[(65, 20)] == 8               # eight diffusions
    # Two poly shapes a device: the gate stripe, and the wider pad above
    # the diffusion that the gate contact lands on. Poly cannot be
    # contacted over the channel, so the pad is not decoration.
    assert counts[(66, 20)] == 16
    assert counts[(64, 20)] == 1               # one well, shared by the PMOS
    assert counts[(93, 44)] == 1               # the n-type implant
    assert counts[(94, 20)] == 1               # and the p-type one
    # Three terminals a device, each a piece of local interconnect a wire
    # can land on: the source, the drain, and now the gate.
    # Twenty-four for the devices, plus one on each of the two taps.
    assert counts[(67, 20)] == 26
    assert counts[(65, 44)] == 2               # the well tap and the substrate tap
    assert counts[(95, 20)] == 8               # the cut around each gate contact
    assert counts[(66, 44)] > 8                # and the contacts under it

    # Every contact sits under a piece of local interconnect, so there can
    # never be fewer strips than devices have sources and drains.
    assert counts[(66, 44)] >= counts[(67, 20)]

    # The widest device is forty microns, so some boundary has to be.
    tallest = max(max(y for _, y in points) - min(y for _, y in points)
                  for _, _, points in shapes)
    assert tallest >= 40000                     # nanometres
