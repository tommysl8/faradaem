"""Which Faradaem a page is, decided before it is written.

The bug these tests exist to prevent: the published site used to work out
that it had no simulator by asking for one, being refused, and then taking
its own controls away in front of the reader. Everything here holds the
replacement -- that the mode is known at build time, that the other mode's
controls are deleted rather than hidden, and that the shared facts are
substituted from the code rather than typed into four HTML files.
"""

import io
import os
import re

import pytest

from spice import deployment, siteinfo

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAGE = """<!DOCTYPE html>
<html lang="en">
<body>
<!--local-only--><p id="local">server</p><!--/local-only-->
<!--static-only--><p id="static" class="note hidden">published</p><!--/static-only-->
<p id="both">always</p>
</body>
</html>
"""


def render(text, mode):
    """Render without asking siteinfo for tokens the fixtures do not use."""
    return deployment.render(text, mode, tokens={})


# ---------------------------------------------------------------------------
# the mode itself
# ---------------------------------------------------------------------------


def test_the_modes_are_a_closed_pair():
    assert deployment.MODES == (deployment.LOCAL, deployment.STATIC)
    assert deployment.other(deployment.LOCAL) == deployment.STATIC
    assert deployment.other(deployment.STATIC) == deployment.LOCAL


def test_the_default_stands_when_the_environment_is_quiet(monkeypatch):
    monkeypatch.delenv(deployment.ENV_VAR, raising=False)
    assert deployment.resolve(deployment.LOCAL) == deployment.LOCAL
    assert deployment.resolve(deployment.STATIC) == deployment.STATIC


def test_the_environment_names_the_mode(monkeypatch):
    monkeypatch.setenv(deployment.ENV_VAR, "static")
    assert deployment.resolve(deployment.LOCAL) == deployment.STATIC
    monkeypatch.setenv(deployment.ENV_VAR, "LOCAL")
    assert deployment.resolve(deployment.STATIC) == deployment.LOCAL


def test_an_unknown_mode_is_refused_rather_than_guessed(monkeypatch):
    """Guessing here ships the wrong page."""
    monkeypatch.setenv(deployment.ENV_VAR, "production")
    with pytest.raises(deployment.DeploymentError) as excinfo:
        deployment.resolve(deployment.LOCAL)
    assert "production" in str(excinfo.value)

    monkeypatch.delenv(deployment.ENV_VAR, raising=False)
    with pytest.raises(deployment.DeploymentError):
        deployment.resolve("preview")
    with pytest.raises(deployment.DeploymentError):
        render(PAGE, "preview")


# ---------------------------------------------------------------------------
# deleting, not hiding
# ---------------------------------------------------------------------------


def test_the_static_build_has_no_server_only_markup_at_all():
    """Not hidden, not disabled: absent. A hidden control is still in the
    document, still focusable, still read aloud, still a lie."""
    out = render(PAGE, deployment.STATIC)
    assert 'id="local"' not in out
    assert "server" not in out
    assert 'id="static"' in out
    assert 'id="both"' in out


def test_the_local_build_has_no_published_only_markup_at_all():
    out = render(PAGE, deployment.LOCAL)
    assert 'id="static"' not in out
    assert "published" not in out
    assert 'id="local"' in out
    assert 'id="both"' in out


def test_no_marker_survives_into_either_build():
    for mode in deployment.MODES:
        out = render(PAGE, mode)
        assert "local-only" not in out
        assert "static-only" not in out
        assert "<!--" not in out.replace("<!DOCTYPE", "")


def test_a_block_kept_for_this_mode_arrives_visible():
    """A static-only notice shipped with `hidden` has only moved the flash."""
    out = render(PAGE, deployment.STATIC)
    assert 'class="note"' in out
    assert "hidden" not in out


def test_stripping_hidden_leaves_other_classes_alone():
    kept = deployment._strip_hidden('<p class="a hidden b">x</p>')
    assert kept == '<p class="a b">x</p>'
    # A class list that was only "hidden" loses the attribute entirely.
    assert deployment._strip_hidden('<p class="hidden">x</p>') == "<p >x</p>"
    # A class merely containing the letters is not the class.
    assert "hiddenish" in deployment._strip_hidden('<p class="hiddenish">x</p>')


def test_the_mode_is_stamped_where_the_first_script_can_read_it():
    for mode in deployment.MODES:
        out = render(PAGE, mode)
        assert deployment.BODY_ATTR + '="' + mode + '"' in out
        assert re.search(r"<html[^>]*" + deployment.BODY_ATTR, out)


def test_stamping_twice_does_not_stack_attributes():
    once = render(PAGE, deployment.STATIC)
    twice = deployment.render(once, deployment.LOCAL, tokens={})
    assert twice.count(deployment.BODY_ATTR) == 1
    assert deployment.BODY_ATTR + '="local"' in twice


def test_a_page_with_no_html_tag_is_refused():
    with pytest.raises(deployment.DeploymentError):
        render("<p>orphan</p>", deployment.LOCAL)


# ---------------------------------------------------------------------------
# markers that do not pair
# ---------------------------------------------------------------------------


def test_an_unclosed_marker_is_an_error_with_a_line_number():
    broken = "<html><body><!--local-only--><p>x</p></body></html>"
    with pytest.raises(deployment.DeploymentError) as excinfo:
        render(broken, deployment.STATIC)
    assert "never closed" in str(excinfo.value)
    assert "line 1" in str(excinfo.value)


def test_a_stray_closer_is_an_error():
    broken = "<html><body><!--/local-only--></body></html>"
    with pytest.raises(deployment.DeploymentError) as excinfo:
        render(broken, deployment.STATIC)
    assert "never opened" in str(excinfo.value)


def test_markers_of_one_kind_do_not_nest():
    broken = ("<html><body><!--local-only--><!--local-only-->x"
              "<!--/local-only--><!--/local-only--></body></html>")
    with pytest.raises(deployment.DeploymentError) as excinfo:
        render(broken, deployment.STATIC)
    assert "do not nest" in str(excinfo.value)


def test_counting_markers_reports_both_kinds():
    found = deployment.count_markers(PAGE)
    assert found[deployment.LOCAL] == 1
    assert found[deployment.STATIC] == 1


# ---------------------------------------------------------------------------
# the shared facts
# ---------------------------------------------------------------------------


def test_every_count_is_derived_from_the_thing_it_counts():
    from spice import circuits, drc, pvt

    found = siteinfo.counts()
    catalogue = circuits.catalog()
    assert found["circuits"] == len(catalogue)
    assert found["pdk_circuits"] == len(
        [item for item in catalogue if item.get("pdk")])
    assert found["topologies"] == len(
        [item for item in catalogue if item.get("pdk") and item.get("design")])
    assert found["pvt_corners"] == len(pvt.PVT_CONDITIONS)
    assert found["drc_rules"] == len(drc.CHECKED_RULES)


def test_the_sky130_count_is_not_the_topology_count():
    """The home page said '3 of them SKY130 amplifiers' while four circuits
    carried pdk=True. Both numbers are real and they are different numbers."""
    found = siteinfo.counts()
    assert found["pdk_circuits"] == 4
    assert found["topologies"] == 3
    assert found["pdk_circuits"] != found["topologies"]


def test_the_version_comes_from_where_a_release_is_minted():
    found = siteinfo.version(PROJECT)
    assert re.match(r"^v\d+\.\d+\.\d+$", found), found
    css = io.open(os.path.join(PROJECT, "static", "style.css"),
                  encoding="utf-8").read()
    assert '--app-version: "' + found + '"' in css


def test_tokens_offer_digits_and_words_for_every_count():
    table = siteinfo.tokens(PROJECT)
    for name in siteinfo.counts():
        assert "{{" + name + "}}" in table
        assert "{{" + name + "_word}}" in table
    assert table["{{drc_rules}}"] == "36"
    assert table["{{drc_rules_word}}"] == "thirty-six"
    assert table["{{repo_url}}"] == siteinfo.REPO_URL


def test_substitution_replaces_what_it_knows():
    out = siteinfo.substitute("rules: {{drc_rules_word}}",
                              {"{{drc_rules_word}}": "thirty-six"})
    assert out == "rules: thirty-six"


def test_an_undefined_token_is_refused_rather_than_shipped():
    """Literal braces on a published page are worse than a build failure."""
    with pytest.raises(ValueError) as excinfo:
        siteinfo.substitute("hello {{nonsense}}", {"{{known}}": "x"})
    assert "{{nonsense}}" in str(excinfo.value)


def test_render_substitutes_the_shared_facts():
    page = '<html><body><a href="{{repo_url}}">x</a></body></html>'
    out = deployment.render(page, deployment.STATIC)
    assert siteinfo.REPO_URL in out
    assert "{{" not in out
