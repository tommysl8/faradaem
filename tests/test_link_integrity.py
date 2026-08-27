"""Links that leave the site, checked against the internet.

Internal routes, hash anchors and asset paths are checked without a
network in test_static_site.py, where they belong: those are facts about
the build. This file checks the ones that are facts about the world.

It exists because the site's install instructions pointed at a repository
whose reachability nothing verified. A "Run it locally" call to action that
404s is worse than none: the reader concludes the tool does not exist.

Network-gated. If the machine is offline these skip, because an offline
machine cannot tell a renamed repository from a missing router, and
guessing between them is how a check starts lying.
"""

import io
import os
import re
import socket
import urllib.error
import urllib.request

import pytest

from spice import deployment, siteinfo
from tools import build_static

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 30
AGENT = "faradaem-link-check (+https://faradaem.com)"


def _online():
    try:
        socket.create_connection(("github.com", 443), timeout=8).close()
        return True
    except OSError:
        return False


ONLINE = _online()
requires_network = pytest.mark.skipif(
    not ONLINE, reason="no network; an outbound link check would be a guess")

#: Hosts that answer a non-browser agent with a refusal rather than a page.
#: LinkedIn returns 999, an invented status meaning "you are not a browser".
#: A link there is not broken; it is unverifiable from here, and saying so
#: is better than either failing on it or pretending it was checked.
BOT_HOSTILE = ("linkedin.com",)

#: The deployed site lags the source by one deploy, always. A tree that is
#: ahead of production is not a broken tree, so these are opt-in:
#:
#:     pytest -m deployed tests/test_link_integrity.py
#:
#: and deselected in the default run by the marker configuration.
deployed = pytest.mark.deployed


def head(url):
    """Return the status for a URL, following redirects. GET, not HEAD:
    some hosts answer HEAD differently or not at all."""
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def outbound_links():
    """Every external URL any rendered page carries, deduplicated."""
    found = set()
    for mode in deployment.MODES:
        for name in build_static.PAGES + (build_static.NOT_FOUND,):
            with io.open(os.path.join(PROJECT, name), encoding="utf-8") as f:
                html = deployment.render(f.read(), mode)
            for href in re.findall(r'href="(https?://[^"]+)"', html):
                found.add(href)
    return sorted(found)


# ---------------------------------------------------------------------------
# the repository the site tells people to clone
# ---------------------------------------------------------------------------


@requires_network
def test_the_repository_is_publicly_reachable():
    """Unauthenticated, because that is how a reader arrives. A private
    repository answers 404 to everyone who is not signed in, which is
    exactly what the site's install instructions would then be."""
    assert head(siteinfo.REPO_URL) == 200


@requires_network
def test_the_repository_api_says_it_is_public():
    """The page can 200 for a signed-in owner and 404 for everyone else;
    the API answering unauthenticated is the check that means public."""
    owner_repo = siteinfo.REPO_URL.split("github.com/")[1]
    assert head("https://api.github.com/repos/" + owner_repo) == 200


@requires_network
@pytest.mark.parametrize("url", outbound_links())
def test_every_outbound_link_resolves(url):
    if any(host in url for host in BOT_HOSTILE):
        pytest.skip(url + " refuses non-browser agents; unverifiable here")
    if url.startswith(siteinfo.SITE_ORIGIN):
        pytest.skip("the site's own pages are checked by the deployed marker")
    status = head(url)
    assert status < 400, (url, status)


# ---------------------------------------------------------------------------
# the site itself, if it is deployed
# ---------------------------------------------------------------------------


PUBLISHED_ROUTES = ("/", "/manual", "/about", "/changelog", "/notebook")


@deployed
@requires_network
@pytest.mark.parametrize("route", PUBLISHED_ROUTES)
def test_every_published_route_answers_on_the_deployed_site(route):
    """Skipped rather than failed when the site is not up: this checks a
    deployment, and a deployment that has not happened yet is not a bug in
    the source."""
    status = head(siteinfo.SITE_ORIGIN + route)
    if status in (502, 503, 504):
        pytest.skip("the deployed site is not answering (%d)" % status)
    assert status == 200, (route, status)


@deployed
@requires_network
def test_a_missing_path_answers_404_on_the_deployed_site():
    status = head(siteinfo.SITE_ORIGIN + "/definitely-not-a-page-here")
    if status in (502, 503, 504):
        pytest.skip("the deployed site is not answering (%d)" % status)
    assert status == 404


@deployed
@requires_network
@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml",
                                  "/static/og.png", "/static/style.css"])
def test_the_deployed_site_serves_its_generated_files(path):
    status = head(siteinfo.SITE_ORIGIN + path)
    if status in (502, 503, 504):
        pytest.skip("the deployed site is not answering (%d)" % status)
    assert status == 200, (path, status)


# ---------------------------------------------------------------------------
# what this file cannot check
# ---------------------------------------------------------------------------


def test_the_link_set_is_small_enough_to_check_exhaustively():
    """If this ever grows, the checks above stop being exhaustive and the
    file should say so rather than sampling quietly."""
    links = outbound_links()
    assert links, "no outbound links found; the extractor is broken"
    assert len(links) < 20, links
