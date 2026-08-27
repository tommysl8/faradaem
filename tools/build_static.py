"""Build the published site: the pages, with no simulator behind them.

Faradaem's whole point is that ngspice measures every number, and ngspice
is a native binary that reads a process design kit and thinks for minutes
at a time. None of that fits a serverless host. What does fit is the part
that is honestly static: the pages, the catalogue, and the schematics,
which are drawn in the browser and redraw as values change.

So this writes a site that shows the tool and draws real circuits, tells
the reader plainly that measuring one needs the local app, and never shows
a number nothing measured.

    python tools/build_static.py --out dist

What "tells the reader plainly" means here is stronger than it used to be.
The page no longer discovers it has no simulator by asking for one and
being refused; spice/deployment.py renders each page for this deployment
before it is written, deleting every control that would need a server.
They are not hidden, not disabled: not in the file. Nothing can flash,
nothing focusable lies, and the static site makes no request that can only
fail.

The host configuration is not written here. It lives in vercel.json at the
repository root, where the command line and the Git integration read the
same file.

Standard library only, like everything else here.
"""

import argparse
import json
import io
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spice import circuits, deployment, siteinfo  # noqa: E402

#: Pages rendered for the deployment and written out. The nav links to them
#: without .html, which the host is told about in vercel.json.
#:
#: notebook.html is here because it must be: leaving it out is what made
#: /notebook answer with the host's own raw 404, from a link in the header
#: and the footer of every other page.
PAGES = ("index.html", "manual.html", "about.html", "changelog.html",
         "notebook.html")

#: Rendered like a page but never linked: the host serves it for anything
#: that does not resolve, in place of its own unbranded 404.
NOT_FOUND = "404.html"

#: Everything the pages load. Listed rather than globbed so a stray file in
#: static/ cannot end up published by accident.
ASSETS = (
    "style.css", "app.js", "import-validate.js",
    "schematic.js", "theme.js",
    "hero-layout.svg", "icon.svg", "icon-32.png", "apple-touch-icon.png",
    "og.png", "favicon.svg",
)

#: Loaded only by controls the static build deletes. Copying them would
#: publish code no page can reach, which is a slower download and a bigger
#: thing to audit for no benefit.
LOCAL_ONLY_ASSETS = (
    "bodeplot.js", "stepplot.js", "layoutplot.js", "notebook.js",
    "panel-datasheet.js", "panel-step.js", "panel-sheet.js",
    "panel-robust.js", "panel-layout.js",
)

#: What the sitemap lists, as paths under the origin. Every one of these is
#: a page the build writes and the host serves without .html.
INDEXED = ("/", "/manual", "/about", "/changelog", "/notebook")

ROBOTS = """User-agent: *
Allow: /

Sitemap: {origin}/sitemap.xml
"""

SITEMAP_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
SITEMAP_URL = "  <url><loc>{origin}{path}</loc></url>\n"
SITEMAP_TAIL = "</urlset>\n"


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def robots_txt(origin=siteinfo.SITE_ORIGIN):
    """What crawlers are told: everything is fair game, here is the map."""
    return ROBOTS.format(origin=origin)


def sitemap_xml(origin=siteinfo.SITE_ORIGIN, paths=INDEXED):
    """The published pages, and only those.

    404.html is deliberately absent and carries noindex of its own: a
    sitemap that advertises the not-found page is asking for it to be
    indexed.
    """
    body = "".join(SITEMAP_URL.format(origin=origin, path=path)
                   for path in paths)
    return SITEMAP_HEAD + body + SITEMAP_TAIL


def build(out_dir, root=None, mode=None):
    """Write the static site into out_dir and return the files written."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mode = mode or deployment.resolve(deployment.STATIC)
    tokens = siteinfo.tokens(root)

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "static"))

    written = []
    for page in PAGES + (NOT_FOUND,):
        with io.open(os.path.join(root, page), encoding="utf-8") as handle:
            source = handle.read()
        _write(os.path.join(out_dir, page),
               deployment.render(source, mode, tokens))
        written.append(page)

    for asset in ASSETS:
        shutil.copyfile(
            os.path.join(root, "static", asset),
            os.path.join(out_dir, "static", asset),
        )
        written.append("static/" + asset)

    # The catalogue the running server would have served. In static mode
    # the page reads this and nothing else; it never asks /api for it.
    catalogue_path = os.path.join(out_dir, "catalogue.json")
    with io.open(catalogue_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"circuits": circuits.catalog()}, handle, indent=1)
        handle.write("\n")
    written.append("catalogue.json")

    _write(os.path.join(out_dir, "robots.txt"), robots_txt())
    written.append("robots.txt")
    _write(os.path.join(out_dir, "sitemap.xml"), sitemap_xml())
    written.append("sitemap.xml")

    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dist")
    parser.add_argument("--mode", default=None, choices=deployment.MODES,
                        help="which deployment to render for; defaults to "
                             "static, or $" + deployment.ENV_VAR)
    args = parser.parse_args()
    out_dir = os.path.abspath(args.out)
    mode = args.mode or deployment.resolve(deployment.STATIC)
    written = build(out_dir, mode=mode)
    total = sum(os.path.getsize(os.path.join(out_dir, name)) for name in written)
    print("wrote %d files, %.1f kB, into %s (deployment: %s)"
          % (len(written), total / 1024.0, out_dir, mode))
    for name in written:
        print("  " + name)


if __name__ == "__main__":
    main()
