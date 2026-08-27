"""The published site, as the world receives it.

These tests build the real thing and read the bytes it would serve. They
exist because the deployed site was wrong in ways nothing local could see:
/notebook was linked from the header and the footer of every page and was
never built, so it answered with the host's own unbranded 404; every
og:image was a relative path, which link previews cannot resolve; there was
no robots.txt, no sitemap, no canonical URL anywhere; and an arbitrary
missing path fell through to the host as well.

Everything here reads the built output rather than the source, because the
source is not what anyone visits.
"""

import io
import json
import os
import re
import xml.etree.ElementTree as ElementTree

import pytest

from spice import deployment, siteinfo
from tools import build_static

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Every page the build writes, and the clean URL the host serves it at.
ROUTES = {
    "/": "index.html",
    "/manual": "manual.html",
    "/about": "about.html",
    "/changelog": "changelog.html",
    "/notebook": "notebook.html",
}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The published build, once, read into memory."""
    out = tmp_path_factory.mktemp("dist")
    written = build_static.build(str(out), root=PROJECT,
                                 mode=deployment.STATIC)
    pages = {}
    for name in written:
        path = os.path.join(str(out), name)
        if name.endswith((".html", ".json", ".txt", ".xml")):
            with io.open(path, encoding="utf-8") as stream:
                pages[name] = stream.read()
    return {"dir": str(out), "written": written, "text": pages}


@pytest.fixture(scope="module")
def local_pages():
    """The same pages rendered for the local server."""
    out = {}
    for name in ROUTES.values():
        with io.open(os.path.join(PROJECT, name), encoding="utf-8") as stream:
            out[name] = deployment.render(stream.read(), deployment.LOCAL)
    return out


# ---------------------------------------------------------------------------
# every route exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route,page", sorted(ROUTES.items()))
def test_every_published_route_has_a_file(site, route, page):
    """/notebook was linked eleven times and built zero times."""
    assert page in site["written"], route
    assert os.path.isfile(os.path.join(site["dir"], page))
    assert len(site["text"][page]) > 500


def test_the_not_found_page_is_built(site):
    assert build_static.NOT_FOUND in site["written"]


def test_the_build_writes_the_catalogue_robots_and_sitemap(site):
    for name in ("catalogue.json", "robots.txt", "sitemap.xml"):
        assert name in site["written"], name


def test_the_catalogue_is_the_registry(site):
    from spice import circuits

    published = json.loads(site["text"]["catalogue.json"])["circuits"]
    assert [item["id"] for item in published] == \
        [item["id"] for item in circuits.catalog()]


# ---------------------------------------------------------------------------
# nothing server-only survives
# ---------------------------------------------------------------------------

#: Controls that need a simulator. Every one of these used to ship to the
#: published site and be hidden by JavaScript a moment later.
SERVER_ONLY_IDS = (
    "advise", "advise-form", "advise-input", "advise-send",
    "run", "run-label", "netlist-toggle", "netlist-copy", "netlist-view",
    "analysis", "analysis-tabs", "design", "design-generate", "design-start",
    "design-stop", "design-apply", "robust", "robust-pvt", "robust-mc",
    "charact", "charact-run", "packet-run", "layout", "layout-run",
    "layout-signoff", "layout-gds", "step-run", "sheet-run", "autopsy-run",
    "triage-run", "blame-run", "sweep-run", "pin-set", "pin-check",
    "ab-hold", "ab-release", "bias-chip", "history-row", "history-prev",
    "bench", "result", "bode-panel", "mentor",
)


@pytest.mark.parametrize("element", SERVER_ONLY_IDS)
def test_no_server_only_control_is_in_the_published_page(site, element):
    assert 'id="' + element + '"' not in site["text"]["index.html"], element


def test_the_local_page_keeps_every_server_only_control(local_pages):
    """The deletion must be the static build's, not a loss of function."""
    page = local_pages["index.html"]
    for element in SERVER_ONLY_IDS:
        assert 'id="' + element + '"' in page, element


def test_what_still_works_is_still_there(site):
    """Circuit selection, presets, editing, redraw, export, import, reset."""
    page = site["text"]["index.html"]
    for element in ("modes", "presets", "inputs", "schematic",
                    "design-export", "design-import", "design-import-file",
                    "design-reset", "circuit-panel", "import-error",
                    "share-note"):
        assert 'id="' + element + '"' in page, element


def test_the_published_page_offers_a_way_to_run_it_instead(site):
    page = site["text"]["index.html"]
    assert 'id="run-locally"' in page
    assert "Run locally to simulate" in page
    assert siteinfo.REPO_URL in page


def test_the_published_page_says_what_it_is(site):
    # Collapsed, because the sentence is wrapped in the source and the
    # reader sees it as one line either way.
    page = " ".join(site["text"]["index.html"].split())
    assert "This published demo redraws circuits but does not run ngspice" \
        in page
    assert "Run the local app to measure them" in page


def test_the_published_footer_states_the_limit(site):
    wanted = ("Faradaem reports measurements only from ngspice. This "
              "published demo does not run simulations.")
    for page in ("index.html", "notebook.html", build_static.NOT_FOUND):
        assert wanted in site["text"][page], page


def test_the_published_build_ships_no_panel_code(site):
    """Scripts whose only controls were deleted are dead weight."""
    for asset in build_static.LOCAL_ONLY_ASSETS:
        assert "static/" + asset not in site["written"], asset
        assert asset not in site["text"]["index.html"], asset


def test_the_published_notebook_explains_itself(site):
    page = site["text"]["notebook.html"]
    assert "There is no ledger here" in page
    assert "Run Faradaem" in page
    assert "/manual#the-notebook" in page
    # And carries none of the local notebook's machinery.
    assert 'id="notebook-runs"' not in page
    assert "notebook.js" not in page


# ---------------------------------------------------------------------------
# the not-found page
# ---------------------------------------------------------------------------


def test_the_not_found_page_is_branded_and_navigable(site):
    page = site["text"][build_static.NOT_FOUND]
    assert "Page not found" in page
    assert 'class="nav"' in page
    assert 'href="/manual"' in page
    assert 'href="/"' in page


def test_the_not_found_page_asks_not_to_be_indexed(site):
    page = site["text"][build_static.NOT_FOUND]
    assert re.search(r'<meta name="robots" content="[^"]*noindex', page)


def test_the_not_found_page_is_not_in_the_sitemap(site):
    assert "404" not in site["text"]["sitemap.xml"]


# ---------------------------------------------------------------------------
# metadata a crawler and a link preview need
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route,page", sorted(ROUTES.items()))
def test_every_page_has_a_canonical_url(site, route, page):
    found = re.search(r'<link rel="canonical" href="([^"]+)"',
                      site["text"][page])
    assert found, page
    assert found.group(1) == siteinfo.SITE_ORIGIN + (
        "/" if route == "/" else route)


@pytest.mark.parametrize("route,page", sorted(ROUTES.items()))
def test_every_page_declares_its_own_url_for_sharing(site, route, page):
    found = re.search(r'<meta property="og:url" content="([^"]+)"',
                      site["text"][page])
    assert found, page
    assert found.group(1).startswith(siteinfo.SITE_ORIGIN)


@pytest.mark.parametrize("page", sorted(set(ROUTES.values())))
def test_share_images_are_absolute(site, page):
    """A relative og:image is a broken preview everywhere it is shared."""
    for prop in ('property="og:image"', 'name="twitter:image"'):
        found = re.search(r'<meta ' + prop + r' content="([^"]+)"',
                          site["text"][page])
        assert found, page + " " + prop
        assert found.group(1) == siteinfo.SITE_ORIGIN + "/static/og.png"


@pytest.mark.parametrize("page", sorted(set(ROUTES.values())))
def test_every_page_has_a_title_and_description(site, page):
    text = site["text"][page]
    assert re.search(r"<title>[^<]{8,}</title>", text), page
    assert re.search(r'<meta name="description" content="[^"]{20,}"', text), page


def test_titles_and_descriptions_are_unique(site):
    titles = {}
    descriptions = {}
    for page in set(ROUTES.values()):
        text = site["text"][page]
        titles[page] = re.search(r"<title>([^<]+)</title>", text).group(1)
        descriptions[page] = re.search(
            r'<meta name="description" content="([^"]+)"', text).group(1)
    assert len(set(titles.values())) == len(titles), titles
    assert len(set(descriptions.values())) == len(descriptions), descriptions


def test_robots_points_at_the_sitemap(site):
    text = site["text"]["robots.txt"]
    assert "User-agent: *" in text
    assert siteinfo.SITE_ORIGIN + "/sitemap.xml" in text


def test_the_sitemap_parses_and_lists_every_published_route(site):
    root = ElementTree.fromstring(site["text"]["sitemap.xml"])
    namespace = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    found = {node.text for node in root.iter(namespace + "loc")}
    wanted = {siteinfo.SITE_ORIGIN + ("/" if route == "/" else route)
              for route in ROUTES}
    assert found == wanted


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------


def _links(text):
    return re.findall(r'href="([^"]+)"', text)


@pytest.mark.parametrize("page", sorted(set(ROUTES.values())
                                        | {build_static.NOT_FOUND}))
def test_every_internal_link_resolves_to_a_built_route(site, page):
    for href in _links(site["text"][page]):
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        route = href.split("#")[0].split("?")[0]
        if route.startswith("/static/"):
            assert route.lstrip("/") in site["written"], (page, href)
            continue
        assert route in ROUTES, (page, href)


@pytest.mark.parametrize("page", sorted(set(ROUTES.values())
                                        | {build_static.NOT_FOUND}))
def test_every_asset_a_page_asks_for_was_published(site, page):
    text = site["text"][page]
    referenced = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', text))
    referenced |= set(re.findall(r'content="[^"]*(/static/[^"]+)"', text))
    for path in referenced:
        assert path.lstrip("/") in site["written"], (page, path)


def test_every_page_links_the_repository_and_only_the_shared_one(site):
    for page in set(ROUTES.values()) | {build_static.NOT_FOUND}:
        text = site["text"][page]
        found = set(re.findall(r'href="(https://github\.com/[^"]+)"', text))
        assert found <= {siteinfo.REPO_URL}, (page, found)


def test_no_page_carries_an_unsubstituted_token(site):
    for name, text in site["text"].items():
        assert not siteinfo.LEFTOVER.search(text), name


def test_manual_hash_links_from_other_pages_hit_real_headings(site):
    manual = site["text"]["manual.html"]
    ids = set(re.findall(r'\sid="([^"]+)"', manual))
    for page, text in site["text"].items():
        if not page.endswith(".html"):
            continue
        for href in _links(text):
            if href.startswith("/manual#"):
                assert href.split("#", 1)[1] in ids, (page, href)


def test_manual_own_hash_links_hit_real_headings(site):
    manual = site["text"]["manual.html"]
    ids = set(re.findall(r'\sid="([^"]+)"', manual))
    for href in _links(manual):
        if href.startswith("#"):
            assert href[1:] in ids, href


# ---------------------------------------------------------------------------
# the headers the host is told to send
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def host_headers():
    """Every header vercel.json applies to every path."""
    with io.open(os.path.join(PROJECT, "vercel.json"), encoding="utf-8") as f:
        config = json.load(f)
    for rule in config["headers"]:
        if rule["source"] == "/(.*)":
            return {item["key"]: item["value"] for item in rule["headers"]}
    raise AssertionError("vercel.json has no catch-all header rule")


@pytest.mark.parametrize("header", [
    "Content-Security-Policy",
    "Permissions-Policy",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
])
def test_every_declared_header_is_present(host_headers, header):
    assert header in host_headers, header
    assert host_headers[header].strip()


def test_the_page_cannot_be_framed(host_headers):
    assert host_headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in host_headers["Content-Security-Policy"]


def test_the_policy_allows_only_first_party_code(host_headers):
    policy = host_headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "object-src 'none'" in policy
    # 'unsafe-inline' and 'unsafe-eval' would make script-src decorative.
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


def test_the_pages_can_live_under_that_policy(site):
    """A policy the site violates would be turned off rather than fixed."""
    for name, text in site["text"].items():
        if not name.endswith(".html"):
            continue
        assert "<script>" not in text, name
        assert "<style>" not in text, name
        assert 'style="' not in text, name
        assert "javascript:" not in text, name
        for src in re.findall(r'<script[^>]*\ssrc="([^"]+)"', text):
            assert src.startswith("/static/"), (name, src)


def test_the_sniffing_and_referrer_rules_are_the_strict_ones(host_headers):
    assert host_headers["X-Content-Type-Options"] == "nosniff"
    assert host_headers["Referrer-Policy"].startswith("strict-origin")
