"""Serve the published build the way the host serves it, for checking.

The static site is not a directory of files you can open with file://. It
is a set of clean URLs (/manual, not /manual.html), a 404 page the host
substitutes for anything missing, and headers declared in vercel.json.
Testing the published behaviour means serving it under those rules, and
guessing at them from a plain file server would test something else.

    python tools/serve_static.py --port 8020 [--dist dist]

Builds first if the directory is not there, so there is one command
between a source edit and looking at what the world would get. Read-only,
localhost, no simulator: this is the published site, and the published
site cannot measure anything.

Standard library only, like everything else here.
"""

import argparse
import io
import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_static  # noqa: E402
from spice import deployment  # noqa: E402

#: The headers vercel.json declares. Served here too, so what is checked
#: locally is what the host sends rather than a subset of it.
HEADERS_FILE = "vercel.json"


def _vercel_headers(root):
    """Every header vercel.json applies to every path, as a list of pairs.

    Only the catch-all source is read: a per-path rule is the host's job
    to match, and guessing at its matching here would be a second
    implementation to keep in step.
    """
    path = os.path.join(root, HEADERS_FILE)
    try:
        with io.open(path, encoding="utf-8") as stream:
            config = json.load(stream)
    except (OSError, ValueError):
        return []
    out = []
    for rule in config.get("headers", []):
        if rule.get("source") in ("/(.*)", "/:path*", "/(.*)?"):
            for header in rule.get("headers", []):
                if "key" in header and "value" in header:
                    out.append((header["key"], header["value"]))
    return out


class Handler(SimpleHTTPRequestHandler):
    """Clean URLs, a real 404 page, and the declared headers."""

    dist = "dist"
    extra_headers = ()

    def end_headers(self):
        for key, value in self.extra_headers:
            self.send_header(key, value)
        SimpleHTTPRequestHandler.end_headers(self)

    def translate_path(self, path):
        # Strip query and fragment, then resolve inside dist only.
        clean = path.split("?", 1)[0].split("#", 1)[0]
        clean = clean.lstrip("/")
        if clean in ("", "/"):
            clean = "index.html"
        full = os.path.normpath(os.path.join(self.dist, clean))
        root = os.path.abspath(self.dist)
        if not os.path.abspath(full).startswith(root):
            return os.path.join(root, "index.html")
        if os.path.isdir(full):
            return os.path.join(full, "index.html")
        if not os.path.exists(full) and os.path.exists(full + ".html"):
            return full + ".html"
        return full

    def send_error(self, code, message=None, explain=None):
        """Answer a missing path with the site's own 404 page."""
        page = os.path.join(self.dist, "404.html")
        if code == 404 and os.path.isfile(page):
            with io.open(page, "rb") as stream:
                body = stream.read()
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        SimpleHTTPRequestHandler.send_error(self, code, message, explain)

    def log_message(self, fmt, *args):
        sys.stderr.write("  " + (fmt % args) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--no-build", action="store_true",
                        help="serve what is there rather than rebuilding")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dist = os.path.abspath(args.dist)
    if not args.no_build:
        build_static.build(dist, root=root, mode=deployment.STATIC)

    Handler.dist = dist
    Handler.extra_headers = tuple(_vercel_headers(root))
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print("Published build at http://127.0.0.1:%d (from %s)"
          % (args.port, dist), flush=True)
    print("This is the static deployment: no simulator, and nothing here "
          "can measure.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
