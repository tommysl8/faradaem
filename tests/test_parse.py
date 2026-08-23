"""parse_vout must read ngspice output faithfully and fail loudly otherwise."""

import pytest

from spice.runner import NgspiceParseError, parse_vout

# Copied from a real ngspice-47 batch run of the V0.0 divider.
BANNER = """Note: No compatibility mode selected!


Circuit: * faradaem v0.0 resistor divider

Doing analysis at TEMP = 27.000000 and TNOM = 27.000000

Using SPARSE 1.3 as Direct Linear Solver

No. of Data Rows : 1
"""

FOOTER = "ngspice-47 done\n"


def canned(vout_line):
    return BANNER + vout_line + "\n" + FOOTER


def test_reads_the_value_from_a_realistic_run():
    assert parse_vout(canned("v(out) = 2.500000e+00")) == 2.5


def test_tolerates_extra_whitespace():
    assert parse_vout(canned("   v( out )   =    2.500000e+00   ")) == 2.5


def test_tolerates_uppercase():
    assert parse_vout(canned("V(OUT) = 2.500000e+00")) == 2.5


@pytest.mark.parametrize(
    "text,expected",
    [
        ("v(out) = 2.5", 2.5),
        ("v(out) = 0", 0.0),
        ("v(out) = -1.25e-03", -1.25e-3),
        ("v(out) = +3.300000e+00", 3.3),
        ("v(out) = .5", 0.5),
        ("v(out) = 1e6", 1e6),
    ],
)
def test_number_formats(text, expected):
    assert parse_vout(canned(text)) == pytest.approx(expected)


def test_missing_line_raises_and_quotes_the_output():
    with pytest.raises(NgspiceParseError) as excinfo:
        parse_vout(BANNER + FOOTER)
    message = str(excinfo.value)
    assert "v(out)" in message
    assert "ngspice-47 done" in message


def test_empty_output_raises():
    with pytest.raises(NgspiceParseError):
        parse_vout("")


@pytest.mark.parametrize(
    "malformed",
    [
        "v(out) = ",
        "v(out) = nan",
        "v(out) = 2.5.0",
        "v(out) = 2,5",
        "v(out) = --2.5",
        "v(out) = 1e",
        "v(out) = failed",
    ],
)
def test_malformed_number_raises(malformed):
    with pytest.raises(NgspiceParseError):
        parse_vout(canned(malformed))


def test_error_quotes_at_most_the_last_20_lines():
    noise = "\n".join("line %d" % index for index in range(200))
    with pytest.raises(NgspiceParseError) as excinfo:
        parse_vout(noise)
    message = str(excinfo.value)
    assert "line 199" in message
    assert "line 179" not in message


def test_last_value_wins_when_several_are_printed():
    # A future multi-point analysis should report the most recent print.
    text = canned("v(out) = 1.000000e+00") + "v(out) = 2.500000e+00\n"
    assert parse_vout(text) == 2.5


def test_a_similar_node_name_is_not_mistaken_for_out():
    with pytest.raises(NgspiceParseError):
        parse_vout(canned("v(output) = 2.500000e+00"))
