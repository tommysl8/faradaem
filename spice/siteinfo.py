r"""The facts the pages state about themselves, counted from the code.

Every page here makes numeric claims. Ten circuits. Eleven PVT corners.
Thirty-six design rules. Three topologies the strategist chooses between.
They were written by hand into four HTML files, and hand-written numbers
drift: at the time this module was added the site said the fast checker
knew thirty-two rules on the home page, thirty-five in the README, and
thirty-six in the manual, and the code knew thirty-six. Three of the four
were wrong, and nothing could have noticed.

So the numbers live here, counted from the thing they describe, and the
pages carry a token instead. deployment.render() substitutes at build time,
for both the local server and the published site, so a page can no longer
disagree with the code it is describing. A count that cannot be derived
does not belong in this module and should not be on a page.

What is deliberately NOT here: the test count. A number of tests that
passed is a fact about a particular run on a particular machine with
particular tooling installed, not a property of the source, and this module
can only tell the truth about the source. It stays where it is.
"""

import io
import os
import re

#: Where the tool lives. One definition: the hero call to action, the
#: static notice, the footer, the About page, the notebook's empty state
#: and every install link in the manual all resolve to this, so a rename
#: is one edit and a link check can prove it reachable.
REPO_URL = "https://github.com/tommysl8/faradaem"

#: The published origin, for canonical URLs and the absolute image URLs
#: that link previews require.
SITE_ORIGIN = "https://www.faradaem.com"

#: Where the version is declared. It drives the wordmark, so it is already
#: the one place a release is minted; this reads it rather than repeating it.
VERSION_FILE = os.path.join("static", "style.css")
VERSION_PATTERN = r'--app-version:\s*"([^"]+)"'

#: Small numbers spelled out, because the prose does. Anything outside this
#: range is rendered as digits rather than guessed at.
WORDS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 20: "twenty", 32: "thirty-two",
    34: "thirty-four", 35: "thirty-five", 36: "thirty-six",
    37: "thirty-seven", 38: "thirty-eight", 39: "thirty-nine",
    40: "forty",
}


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def word(number):
    """The number as the prose would spell it, or as digits if it is large."""
    return WORDS.get(number, str(number))


def version(root=None):
    """The version the stylesheet declares, e.g. 'v1.14.0'."""
    path = os.path.join(root or _root(), VERSION_FILE)
    with io.open(path, encoding="utf-8") as stream:
        found = re.search(VERSION_PATTERN, stream.read())
    if not found:
        raise ValueError("No --app-version in " + path)
    return found.group(1)


def counts():
    """Every countable claim the pages make, counted from the code now.

    Imported lazily so this module can be read by tooling that has no
    business importing the simulator.
    """
    from . import circuits, drc, pvt

    catalogue = circuits.catalog()
    pdk = [item for item in catalogue if item.get("pdk")]
    # The strategist picks between the SKY130 amplifiers that declare a
    # design block: those are the ones it can actually size. nfet_cs_amp is
    # a SKY130 amplifier too, which is exactly why "three SKY130 circuits"
    # was wrong and "three topologies it designs" is not.
    topologies = [item for item in pdk if item.get("design")]

    return {
        "circuits": len(catalogue),
        "pdk_circuits": len(pdk),
        "topologies": len(topologies),
        "pvt_corners": len(pvt.PVT_CONDITIONS),
        "mc_runs": pvt.MC_DEFAULT_RUNS,
        "drc_rules": len(drc.CHECKED_RULES),
    }


def tokens(root=None):
    """The substitutions a page may carry, as {token: replacement}.

    Digits and spelled-out forms both, because a headline tile wants "36"
    and a sentence wants "thirty-six", and neither should be hand-written.
    """
    found = counts()
    table = {
        "{{repo_url}}": REPO_URL,
        "{{site_origin}}": SITE_ORIGIN,
        "{{version}}": version(root),
    }
    for name, number in found.items():
        table["{{" + name + "}}"] = str(number)
        table["{{" + name + "_word}}"] = word(number)
    return table


#: Any {{...}} left in a rendered page is a token nobody defined, which
#: would ship as literal braces. Tests scan for this.
LEFTOVER = re.compile(r"\{\{[a-z_]+\}\}")


def substitute(text, table=None, root=None):
    """Replace every known token, and refuse to ship an unknown one."""
    table = table if table is not None else tokens(root)
    for token, value in table.items():
        text = text.replace(token, value)
    stray = LEFTOVER.search(text)
    if stray:
        line = text.count("\n", 0, stray.start()) + 1
        raise ValueError(
            "Line " + str(line) + " carries " + stray.group(0) + ", which is "
            "not a defined token. Add it to siteinfo.tokens() or remove it.")
    return text
