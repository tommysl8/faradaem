"""A GDSII writer, in the standard library.

GDSII is the format every layout tool reads, and it is documented well
enough to write directly: a stream of records, each a two byte length, a
one byte record type, a one byte data type, and its payload. Writing it
here rather than taking a dependency keeps the rule this project has kept
since the beginning, and it means the geometry Faradaem computes can leave
Faradaem and be opened in KLayout, Magic or anything else.

Only what a floorplan needs is implemented: a library, one structure, and
rectangles on numbered layers. No hierarchy, no paths, no text.

The coordinates are integers in database units. The convention here is the
usual one for a nanometre process: one user unit is a micron, one database
unit is a nanometre.
"""

import struct
import time

#: Record types, from the GDSII specification.
HEADER = 0x0002
BGNLIB = 0x0102
LIBNAME = 0x0206
UNITS = 0x0305
BGNSTR = 0x0502
STRNAME = 0x0606
BOUNDARY = 0x0800
LAYER = 0x0D02
DATATYPE = 0x0E02
XY = 0x1003
ENDEL = 0x1100
ENDSTR = 0x0700
ENDLIB = 0x0400

#: One user unit is a micron; one database unit is a nanometre.
USER_UNIT_M = 1e-6
DB_UNIT_M = 1e-9
DB_PER_MICRON = int(round(USER_UNIT_M / DB_UNIT_M))

#: Version 6, which is what every modern tool expects to see.
GDS_VERSION = 600


def _record(kind, payload=b""):
    length = len(payload) + 4
    if length > 0xFFFF:
        raise ValueError("A GDSII record cannot exceed 65535 bytes.")
    return struct.pack(">HH", length, kind) + payload


def _ascii(text):
    """GDSII strings are even length, padded with a null."""
    raw = text.encode("ascii", "replace")
    if len(raw) % 2:
        raw += b"\x00"
    return raw


def real8(value):
    """One GDSII real: sign, excess-64 base-16 exponent, 56 bit mantissa.

    Not IEEE 754. The format predates it, and a writer that emits IEEE here
    produces a file that opens with the wrong scale everywhere.
    """
    if value == 0:
        return b"\x00" * 8

    sign = 0x80 if value < 0 else 0x00
    magnitude = abs(float(value))

    exponent = 0
    while magnitude >= 1.0:
        magnitude /= 16.0
        exponent += 1
    while magnitude < 1.0 / 16.0:
        magnitude *= 16.0
        exponent -= 1

    mantissa = int(round(magnitude * (1 << 56)))
    if mantissa >= (1 << 56):          # rounding pushed it over
        mantissa >>= 4
        exponent += 1

    return bytes([sign | (exponent + 64)]) + mantissa.to_bytes(7, "big")


def _timestamp(when=None):
    """Twelve shorts: modification time and access time, both the same."""
    stamp = time.localtime(when if when is not None else time.time())
    fields = (stamp.tm_year, stamp.tm_mon, stamp.tm_mday,
              stamp.tm_hour, stamp.tm_min, stamp.tm_sec)
    return struct.pack(">12h", *(fields + fields))


def rectangle(layer, datatype, x1, y1, x2, y2):
    """One boundary, in database units, closed as GDSII requires."""
    left, right = sorted((int(round(x1)), int(round(x2))))
    bottom, top = sorted((int(round(y1)), int(round(y2))))
    points = [(left, bottom), (right, bottom), (right, top),
              (left, top), (left, bottom)]
    coordinates = b"".join(struct.pack(">ii", x, y) for x, y in points)

    return (_record(BOUNDARY)
            + _record(LAYER, struct.pack(">h", layer))
            + _record(DATATYPE, struct.pack(">h", datatype))
            + _record(XY, coordinates)
            + _record(ENDEL))


def library(name, structure, shapes, when=None):
    """A complete GDSII stream holding one structure of rectangles.

    shapes is a list of (layer, datatype, x1, y1, x2, y2) in microns; they
    are converted to database units here so callers can work in the units
    the rest of this project uses.
    """
    body = b"".join(
        rectangle(layer, datatype,
                  x1 * DB_PER_MICRON, y1 * DB_PER_MICRON,
                  x2 * DB_PER_MICRON, y2 * DB_PER_MICRON)
        for layer, datatype, x1, y1, x2, y2 in shapes
    )

    stamp = _timestamp(when)
    return (
        _record(HEADER, struct.pack(">h", GDS_VERSION))
        + _record(BGNLIB, stamp)
        + _record(LIBNAME, _ascii(name))
        + _record(UNITS, real8(DB_UNIT_M / USER_UNIT_M) + real8(DB_UNIT_M))
        + _record(BGNSTR, stamp)
        + _record(STRNAME, _ascii(structure))
        + body
        + _record(ENDSTR)
        + _record(ENDLIB)
    )
