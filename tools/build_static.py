"""Build the published site: the pages, with no simulator behind them.

Faradaem's whole point is that ngspice measures every number, and ngspice
is a native binary that reads a 2 GB process design kit and thinks for
minutes at a time. None of that fits a serverless host. What does fit is
the part that is honestly static: the pages, the catalogue, and the
schematics, which are drawn in the browser and redraw as values change.

So this writes a site that shows the tool and draws real circuits, tells
the reader plainly that measuring one needs the local app, and never
shows a number nothing measured.

    python tools/build_static.py --out dist

The host configuration is not written here. It lives in vercel.json at the
repository root, where the command line and the Git integration read the
same file.

Standard library only, like everything else here.
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spice import circuits  # noqa: E402  (after the path fix)

#: Pages copied as they are. The nav links to them without .html, which the
#: host is told about in vercel.json.
PAGES = ("index.html", "manual.html", "about.html", "changelog.html")

#: Everything the pages load. Listed rather than globbed so a stray file in
#: static/ cannot end up published by accident.
ASSETS = (
    "style.css", "app.js", "schematic.js", "bodeplot.js", "stepplot.js",
    "layoutplot.js", "hero-layout.svg", "panel-datasheet.js",
    "panel-step.js", "panel-sheet.js", "panel-robust.js", "panel-layout.js",
    "icon.svg", "icon-32.png", "apple-touch-icon.png", "og.png",
    "favicon.svg",
)


def build(out_dir, root=None):
    """Write the static site into out_dir and return the files written."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "static"))

    written = []
    for page in PAGES:
        shutil.copyfile(os.path.join(root, page), os.path.join(out_dir, page))
        written.append(page)

    for asset in ASSETS:
        shutil.copyfile(
            os.path.join(root, "static", asset),
            os.path.join(out_dir, "static", asset),
        )
        written.append("static/" + asset)

    # The catalogue the running server would have served. The page asks for
    # /api/circuits first, falls back to this file, and on finding it puts
    # away every panel that would need a measured number.
    catalogue_path = os.path.join(out_dir, "catalogue.json")
    with open(catalogue_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"circuits": circuits.catalog()}, handle, indent=1)
        handle.write("\n")
    written.append("catalogue.json")

    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dist")
    args = parser.parse_args()
    out_dir = os.path.abspath(args.out)
    written = build(out_dir)
    total = sum(os.path.getsize(os.path.join(out_dir, name)) for name in written)
    print("wrote %d files, %.1f kB, into %s"
          % (len(written), total / 1024.0, out_dir))
    for name in written:
        print("  " + name)


if __name__ == "__main__":
    main()
