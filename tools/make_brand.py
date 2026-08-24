"""Generate the Faradaem brand assets from the IBM Plex Sans AE ligature.

Standard library only. The glyph outline is parsed straight out of the
TrueType file and baked into SVG path data, so nothing at render time
depends on a font. One placement function positions the glyph on every
canvas, which keeps the favicon, the touch icon, and the Open Graph
image geometrically identical.

Outlines derived from IBM Plex Sans SemiBold, SIL Open Font License 1.1
(github.com/IBM/plex). The OFL permits embedding outlines in documents;
the font file itself is not vendored into this repository.

Usage:
    python tools/make_brand.py --font PATH/IBMPlexSans-SemiBold.ttf
    python tools/make_brand.py --font PATH/segoeuisb.ttf --inspect
"""

import argparse
import os
import struct
import sys
import zlib

GLYPH = "\u00c6"          # the AE ligature
FRACTION = 0.58           # glyph width as a share of the canvas short side
INK = (10, 14, 20)        # --ink, the site's black
WHITE = (255, 255, 255)


# ---- TrueType parsing -------------------------------------------------------

def read_tables(data):
    num_tables = struct.unpack_from(">H", data, 4)[0]
    tables = {}
    for i in range(num_tables):
        tag, _, off, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
        tables[tag.decode("latin-1")] = (off, length)
    return tables


def glyph_id(data, tables, char):
    off, _ = tables["cmap"]
    count = struct.unpack_from(">H", data, off + 2)[0]
    best = None
    for i in range(count):
        pid, eid, sub = struct.unpack_from(">HHI", data, off + 4 + 8 * i)
        fmt = struct.unpack_from(">H", data, off + sub)[0]
        if (pid, eid) == (3, 1) and fmt == 4:
            best = ("f4", off + sub)
        if fmt == 12 and best is None:
            best = ("f12", off + sub)
    if best is None:
        raise SystemExit("no usable cmap subtable")
    kind, sub = best
    code = ord(char)
    if kind == "f12":
        n = struct.unpack_from(">I", data, sub + 12)[0]
        for i in range(n):
            s, e, g = struct.unpack_from(">III", data, sub + 16 + 12 * i)
            if s <= code <= e:
                return g + (code - s)
        raise SystemExit("glyph not in cmap")
    seg2 = struct.unpack_from(">H", data, sub + 6)[0]
    segs = seg2 // 2
    ends = struct.unpack_from(">%dH" % segs, data, sub + 14)
    starts = struct.unpack_from(">%dH" % segs, data, sub + 16 + seg2)
    deltas = struct.unpack_from(">%dh" % segs, data, sub + 16 + 2 * seg2)
    ro_base = sub + 16 + 3 * seg2
    offsets = struct.unpack_from(">%dH" % segs, data, ro_base)
    for i in range(segs):
        if starts[i] <= code <= ends[i]:
            if offsets[i] == 0:
                return (code + deltas[i]) & 0xFFFF
            addr = ro_base + 2 * i + offsets[i] + 2 * (code - starts[i])
            gid = struct.unpack_from(">H", data, addr)[0]
            return (gid + deltas[i]) & 0xFFFF if gid else 0
    raise SystemExit("glyph not in cmap")


def glyf_offset(data, tables, gid):
    head_off = tables["head"][0]
    long_loca = struct.unpack_from(">h", data, head_off + 50)[0] == 1
    loca_off = tables["loca"][0]
    if long_loca:
        a, b = struct.unpack_from(">II", data, loca_off + 4 * gid)
    else:
        a, b = struct.unpack_from(">HH", data, loca_off + 2 * gid)
        a, b = a * 2, b * 2
    return a, b


def glyph_contours(data, tables, gid, dx=0, dy=0):
    """Return a list of contours, each a list of (x, y, on_curve)."""
    a, b = glyf_offset(data, tables, gid)
    if a == b:
        return []
    off = tables["glyf"][0] + a
    n_contours = struct.unpack_from(">h", data, off)[0]
    if n_contours < 0:
        return composite_contours(data, tables, off + 10, dx, dy)
    ends = struct.unpack_from(">%dH" % n_contours, data, off + 10)
    n_pts = ends[-1] + 1
    p = off + 10 + 2 * n_contours
    ins_len = struct.unpack_from(">H", data, p)[0]
    p += 2 + ins_len

    flags = []
    while len(flags) < n_pts:
        f = data[p]; p += 1
        flags.append(f)
        if f & 8:
            rep = data[p]; p += 1
            flags.extend([f] * rep)

    xs, x = [], 0
    for f in flags:
        if f & 2:
            d = data[p]; p += 1
            x += d if f & 16 else -d
        elif not f & 16:
            x += struct.unpack_from(">h", data, p)[0]; p += 2
        xs.append(x)
    ys, y = [], 0
    for f in flags:
        if f & 4:
            d = data[p]; p += 1
            y += d if f & 32 else -d
        elif not f & 32:
            y += struct.unpack_from(">h", data, p)[0]; p += 2
        ys.append(y)

    pts = [(xs[i] + dx, ys[i] + dy, bool(flags[i] & 1)) for i in range(n_pts)]
    contours, start = [], 0
    for e in ends:
        contours.append(pts[start:e + 1])
        start = e + 1
    return contours


def composite_contours(data, tables, p, dx, dy):
    out = []
    while True:
        flags, gi = struct.unpack_from(">HH", data, p)
        p += 4
        if flags & 1:  # words
            a1, a2 = struct.unpack_from(">hh", data, p); p += 4
        else:
            a1, a2 = struct.unpack_from(">bb", data, p); p += 2
        if flags & 8:
            p += 2
        elif flags & 0x40:
            p += 4
        elif flags & 0x80:
            p += 8
        if flags & 2:  # args are xy offsets
            out.extend(glyph_contours(data, tables, gi, dx + a1, dy + a2))
        if not flags & 0x20:
            break
    return out


def font_metrics(data, tables):
    upm = struct.unpack_from(">H", data, tables["head"][0] + 18)[0]
    os2 = tables.get("OS/2")
    cap = None
    if os2:
        version = struct.unpack_from(">H", data, os2[0])[0]
        if version >= 2:
            cap = struct.unpack_from(">h", data, os2[0] + 88)[0]
    return upm, cap


# ---- outline geometry -------------------------------------------------------

def contour_segments(contour):
    """Expand TrueType points into (start, ctrl_or_None, end) segments."""
    pts = list(contour)
    if not pts:
        return []
    if not pts[0][2]:  # start on an implied midpoint if first point is off
        if pts[-1][2]:
            pts.insert(0, pts.pop())
        else:
            first = ((pts[0][0] + pts[-1][0]) / 2.0,
                     (pts[0][1] + pts[-1][1]) / 2.0, True)
            pts.insert(0, first)
    pts.append(pts[0])
    segs, i = [], 0
    while i < len(pts) - 1:
        p0, p1 = pts[i], pts[i + 1]
        if p1[2]:
            segs.append(((p0[0], p0[1]), None, (p1[0], p1[1])))
            i += 1
        else:
            nxt = pts[i + 2] if i + 2 < len(pts) else pts[0]
            if nxt[2]:
                end, step = (nxt[0], nxt[1]), 2
            else:
                end, step = ((p1[0] + nxt[0]) / 2.0, (p1[1] + nxt[1]) / 2.0), 1
                pts.insert(i + 2, (end[0], end[1], True))
                step = 2
            segs.append(((p0[0], p0[1]), (p1[0], p1[1]), end))
            i += step
    return segs


def bbox(contours):
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    return min(xs), min(ys), max(xs), max(ys)


def place(contours, canvas_w, canvas_h, snap=False):
    """Scale and centre: width = FRACTION of the short side, bbox centred.

    The AE is wider than tall, so fitting the width and centring the
    cap-height band vertically is the optical centring the mark needs.
    With snap=True the top and bottom edges land on whole pixels so the
    crossbar and counters survive small-size downsampling better.
    """
    x0, y0, x1, y1 = bbox(contours)
    scale = FRACTION * min(canvas_w, canvas_h) / (x1 - x0)
    if snap:
        h = round((y1 - y0) * scale) or 1
        scale = h / float(y1 - y0)
    gw, gh = (x1 - x0) * scale, (y1 - y0) * scale
    tx = (canvas_w - gw) / 2.0 - x0 * scale
    ty = (canvas_h - gh) / 2.0 + y1 * scale  # y flips: font up, screen down
    if snap:
        ty = round(ty - y1 * scale) + y1 * scale
        tx = round(tx - x0 * scale) + x0 * scale

    def tr(p):
        return (p[0] * scale + tx, -p[1] * scale + ty)

    placed = []
    for c in contours:
        placed.append([(seg[0], seg[1], seg[2]) for seg in [
            (tr(s), tr(ctl) if ctl else None, tr(e))
            for s, ctl, e in contour_segments(c)]])
    return placed, scale


# ---- SVG --------------------------------------------------------------------

def svg_path(placed):
    parts = []
    for segs in placed:
        if not segs:
            continue
        parts.append("M%.2f %.2f" % segs[0][0])
        for s, ctl, e in segs:
            if ctl is None:
                parts.append("L%.2f %.2f" % e)
            else:
                parts.append("Q%.2f %.2f %.2f %.2f" % (ctl + e))
        parts.append("Z")
    return "".join(parts)


def write_svg(path, placed, size):
    body = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d">\n'
        '<rect width="%d" height="%d" fill="#%02X%02X%02X"/>\n'
        '<path d="%s" fill="#FFFFFF"/>\n</svg>\n'
        % (size, size, size, size, INK[0], INK[1], INK[2], svg_path(placed))
    )
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write(body)


# ---- rasterising ------------------------------------------------------------

def flatten(placed, steps=24):
    """Bezier segments to straight edges for the scanline fill."""
    edges = []
    for segs in placed:
        for s, ctl, e in segs:
            if ctl is None:
                edges.append((s, e))
            else:
                prev = s
                for i in range(1, steps + 1):
                    t = i / float(steps)
                    u = 1.0 - t
                    p = (u * u * s[0] + 2 * u * t * ctl[0] + t * t * e[0],
                         u * u * s[1] + 2 * u * t * ctl[1] + t * t * e[1])
                    edges.append((prev, p))
                    prev = p
    return edges


def rasterise(placed, w, h, ss=8):
    """Non-zero winding scanline fill, supersampled, box downsampled.

    Returns a bytearray of coverage 0..255, row major, w*h.
    """
    W, H = w * ss, h * ss
    edges = []
    for (x0, y0), (x1, y1) in flatten(placed):
        if y0 == y1:
            continue
        edges.append((x0 * ss, y0 * ss, x1 * ss, y1 * ss))
    cover = bytearray(w * h)
    row = [0] * W
    acc = [[0] * w for _ in range(h)]
    for Y in range(H):
        yc = Y + 0.5
        crossings = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                t = (yc - y0) / (y1 - y0)
                crossings.append((x0 + t * (x1 - x0), 1 if y1 > y0 else -1))
        if not crossings:
            continue
        crossings.sort()
        for i in range(W):
            row[i] = 0
        winding, prev_x = 0, None
        for x, d in crossings:
            if winding != 0 and prev_x is not None:
                a = max(0, int(prev_x + 0.5))
                b = min(W, int(x + 0.5))
                for i in range(a, b):
                    row[i] = 1
            winding += d
            prev_x = x
        out = acc[Y // ss]
        for i in range(W):
            if row[i]:
                out[i // ss] += 1
    total = ss * ss
    for y in range(h):
        line = acc[y]
        base = y * w
        for x in range(w):
            cover[base + x] = line[x] * 255 // total
    return cover


def png_bytes(w, h, rgb_rows):
    def chunk(tag, payload):
        c = struct.pack(">I", len(payload)) + tag + payload
        return c + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + row for row in rgb_rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def compose(w, h, cover):
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            a = cover[y * w + x]
            row += bytes(((INK[i] * (255 - a) + WHITE[i] * a) // 255)
                         for i in range(3))
        rows.append(bytes(row))
    return rows


def write_png(path, w, h, cover):
    with open(path, "wb") as f:
        f.write(png_bytes(w, h, compose(w, h, cover)))


def contact_sheet(path, tiles):
    """Nearest-neighbour upscales side by side, for shape inspection."""
    gap, pad = 24, 16
    scaled = []
    for w, h, cover, mag in tiles:
        rows = compose(w, h, cover)
        big = []
        for y in range(h):
            row = rows[y]
            wide = b"".join(row[3 * x:3 * x + 3] * mag for x in range(w))
            big.extend([wide] * mag)
        scaled.append((w * mag, h * mag, big))
    W = sum(t[0] for t in scaled) + gap * (len(scaled) - 1) + 2 * pad
    H = max(t[1] for t in scaled) + 2 * pad
    grey = b"\x60\x66\x70"
    out_rows = []
    for y in range(H):
        row = bytearray(grey * W)
        x_at = pad
        for tw, th, big in scaled:
            if pad <= y < pad + th:
                row[3 * x_at:3 * (x_at + tw)] = big[y - pad]
            x_at += tw + gap
        out_rows.append(bytes(row))
    with open(path, "wb") as f:
        f.write(png_bytes(W, H, out_rows))


# ---- entry ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", required=True)
    ap.add_argument("--out", default="static")
    ap.add_argument("--preview", help="write a contact sheet PNG here")
    ap.add_argument("--inspect", action="store_true",
                    help="print cap height and trademark metrics, then exit")
    args = ap.parse_args()

    data = open(args.font, "rb").read()
    tables = read_tables(data)
    upm, cap = font_metrics(data, tables)

    if args.inspect:
        for ch, label in ((GLYPH, "AE"), ("\u2122", "TM"), ("M", "M")):
            gid = glyph_id(data, tables, ch)
            cs = glyph_contours(data, tables, gid)
            if cs:
                x0, y0, x1, y1 = bbox(cs)
                print("%s gid=%d bbox=(%d,%d)-(%d,%d) h=%d" %
                      (label, gid, x0, y0, x1, y1, y1 - y0))
        print("unitsPerEm=%d capHeight=%s" % (upm, cap))
        return

    gid = glyph_id(data, tables, GLYPH)
    contours = glyph_contours(data, tables, gid)
    if not contours:
        raise SystemExit("empty outline for " + repr(GLYPH))
    x0, y0, x1, y1 = bbox(contours)
    print("AE bbox %dx%d units, cap %s, ratio %.3f wide"
          % (x1 - x0, y1 - y0, cap, FRACTION))

    os.makedirs(args.out, exist_ok=True)
    placed512, _ = place(contours, 512, 512)
    write_svg(os.path.join(args.out, "icon.svg"), placed512, 512)

    jobs = [("icon-32.png", 32, 32, True),
            ("apple-touch-icon.png", 180, 180, False),
            ("og.png", 1200, 630, False)]
    tiles = []
    for name, w, h, snap in jobs:
        placed, _ = place(contours, w, h, snap=snap)
        cover = rasterise(placed, w, h, ss=8 if w <= 200 else 4)
        write_png(os.path.join(args.out, name), w, h, cover)
        print("wrote", name)

    if args.preview:
        p16, _ = place(contours, 16, 16, snap=True)
        p32, _ = place(contours, 32, 32, snap=True)
        p180, _ = place(contours, 180, 180)
        tiles = [(16, 16, rasterise(p16, 16, 16), 12),
                 (32, 32, rasterise(p32, 32, 32), 6),
                 (180, 180, rasterise(p180, 180, 180), 1)]
        contact_sheet(args.preview, tiles)
        print("wrote", args.preview)


if __name__ == "__main__":
    main()
