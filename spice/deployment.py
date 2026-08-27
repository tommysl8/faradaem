r"""Which Faradaem this page is: the local tool, or the published demo.

Faradaem ships as two things from one set of files. Run `python server.py`
and you get the tool: a real simulator behind every panel. Visit
faradaem.com and you get a demo: the same pages and the same live
schematics, with nothing behind them that can measure.

The page used to work out which one it was by *asking*. It fetched
/api/circuits, and if that 404'd it concluded there was no server and put
the server-only panels away. Three things were wrong with that. The static
site made a request that could only ever fail. The reader saw a working
tool for a moment -- "Ask for a design", a Notebook link, a Run button --
and then watched it be taken away, which reads as a broken page rather than
an honest one. And the controls were only *hidden*: still in the document,
still focusable by keyboard, still read by a screen reader, still there for
anyone who pressed Tab.

So the mode is decided before the page is written, not after it is loaded.
This module is the whole of that decision:

  * FARADAEM_DEPLOYMENT names the mode, or the caller's default stands.
    server.py defaults to local; tools/build_static.py defaults to static.
  * A page marks the parts that belong to one mode with HTML comments.
    render() deletes the other mode's parts outright, so they are not in
    the shipped bytes at all -- not hidden, not disabled, gone.
  * render() stamps the mode onto <html>, so app.js knows what it is on its
    first line, without a request and without a guess.

Comments rather than a parser on purpose. The stdlib's HTMLParser would
have to re-emit the whole document to delete one subtree, and every
re-emission is a chance to mangle markup that was fine. A comment pair is
exact, greppable, inert in the browser, and cannot corrupt what it does not
match. The cost is that the markers must nest correctly, which nest() checks
and refuses.
"""

import os
import re

from . import siteinfo

#: The two deployments. Closed on purpose: a third would be a third set of
#: claims about what the page can do, and every claim here has to be true.
LOCAL = "local"
STATIC = "static"
MODES = (LOCAL, STATIC)

#: Names the mode explicitly, for a build or a server that wants to say so.
ENV_VAR = "FARADAEM_DEPLOYMENT"

#: What <html> carries, so the first line of script can read the mode.
BODY_ATTR = "data-deployment"

#: The marker pairs. `<!--local-only-->...<!--/local-only-->` survives only
#: in the local build; `<!--static-only-->...<!--/static-only-->` only in
#: the published one.
MARKERS = {
    LOCAL: ("<!--local-only-->", "<!--/local-only-->"),
    STATIC: ("<!--static-only-->", "<!--/static-only-->"),
}


class DeploymentError(ValueError):
    """Raised when a page's mode markers do not pair up."""


def resolve(default):
    """The mode to build for: $FARADAEM_DEPLOYMENT, or the caller's default.

    An unrecognised value is refused rather than quietly treated as one of
    the two, because guessing here ships the wrong page.
    """
    if default not in MODES:
        raise DeploymentError(
            "Unknown default deployment " + repr(default) + ". Choose one "
            "of: " + ", ".join(MODES) + ".")
    chosen = os.environ.get(ENV_VAR, "").strip().lower()
    if not chosen:
        return default
    if chosen not in MODES:
        raise DeploymentError(
            "$" + ENV_VAR + " is " + repr(chosen) + ", which is not a "
            "deployment. Choose one of: " + ", ".join(MODES) + ".")
    return chosen


def other(mode):
    """The mode that is not this one."""
    return STATIC if mode == LOCAL else LOCAL


def _cut(text, opener, closer):
    """Delete every opener..closer block, refusing markers that do not pair.

    Scanned rather than matched with a regex so that an unclosed opener is
    an error with a position, not a silent match to the end of the file or
    a silent no-match that ships the block anyway.
    """
    out = []
    at = 0
    while True:
        start = text.find(opener, at)
        if start < 0:
            break
        end = text.find(closer, start)
        if end < 0:
            line = text.count("\n", 0, start) + 1
            raise DeploymentError(
                opener + " on line " + str(line) + " is never closed with "
                + closer + ".")
        nested = text.find(opener, start + len(opener), end)
        if nested >= 0:
            line = text.count("\n", 0, nested) + 1
            raise DeploymentError(
                opener + " on line " + str(line) + " opens inside another "
                "block of the same kind; these do not nest.")
        out.append(text[at:start])
        at = end + len(closer)
    stray = text.find(closer, at)
    if stray >= 0:
        line = text.count("\n", 0, stray) + 1
        raise DeploymentError(
            closer + " on line " + str(line) + " closes a block that was "
            "never opened.")
    out.append(text[at:])
    return "".join(out)


def _strip_hidden(text):
    """Drop the `hidden` class from what survived into the static build.

    A block kept for static mode is a block that should be visible on
    arrival: the static notice, the run-locally link. They carry `hidden`
    in the source only because the local page has no use for them, and a
    static page that ships them still hidden has just moved the flash.
    """
    def fix(match):
        classes = [name for name in match.group(1).split() if name != "hidden"]
        if not classes:
            return ""
        return 'class="' + " ".join(classes) + '"'

    return re.sub(r'class="([^"]*\bhidden\b[^"]*)"', fix, text)


def count_markers(text):
    """How many blocks of each mode a page declares. For tests and doctors."""
    return {mode: text.count(pair[0]) for mode, pair in MARKERS.items()}


def render(text, mode, tokens=None):
    """Return the page as this deployment ships it.

    Deletes the other mode's blocks, unhides this mode's, substitutes the
    shared facts from siteinfo, and stamps the mode on <html>.

    tokens is passed through to siteinfo.substitute; None means "look them
    up", and an empty dict means "this page carries none", which is how a
    test renders markup without importing the simulator.
    """
    if mode not in MODES:
        raise DeploymentError(
            "Unknown deployment " + repr(mode) + ". Choose one of: "
            + ", ".join(MODES) + ".")

    gone = MARKERS[other(mode)]
    text = _cut(text, gone[0], gone[1])

    keep = MARKERS[mode]
    kept = []
    at = 0
    while True:
        start = text.find(keep[0], at)
        if start < 0:
            kept.append(text[at:])
            break
        end = text.find(keep[1], start)
        if end < 0:
            line = text.count("\n", 0, start) + 1
            raise DeploymentError(
                keep[0] + " on line " + str(line) + " is never closed with "
                + keep[1] + ".")
        kept.append(text[at:start])
        kept.append(_strip_hidden(text[start + len(keep[0]):end]))
        at = end + len(keep[1])
    text = "".join(kept)

    text = siteinfo.substitute(text, tokens)
    return _stamp(text, mode)


def _stamp(text, mode):
    """Put data-deployment on <html>, replacing any stamp already there."""
    stamped = re.sub(
        r'(<html\b[^>]*?)\s+' + re.escape(BODY_ATTR) + r'="[^"]*"',
        r"\1", text, count=1)
    found = re.search(r"<html\b[^>]*>", stamped)
    if not found:
        raise DeploymentError("This page has no <html> tag to stamp.")
    opening = found.group(0)
    replaced = opening[:-1].rstrip() + ' ' + BODY_ATTR + '="' + mode + '">'
    return stamped[:found.start()] + replaced + stamped[found.end():]
