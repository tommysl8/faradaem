"""The netlist builder must emit exactly what ngspice expects to be handed."""

import pytest

from spice.runner import build_divider_netlist

CONTROL_BLOCK = (".control", "op", "print v(out)", "quit", ".endc")


def lines_of(vdd, r1, r2):
    return build_divider_netlist(vdd, r1, r2).splitlines()


def test_title_line_is_a_comment():
    # SPICE always treats the first line as the title, so it must not be a card.
    assert lines_of(5, 10000, 10000)[0].startswith("*")


def test_contains_source_and_both_resistors():
    lines = lines_of(5, 10000, 10000)
    assert "V1 in 0 DC 5" in lines
    assert "R1 in out 10000" in lines
    assert "R2 out 0 10000" in lines


def test_topology_nodes_are_wired_in_out_ground():
    lines = lines_of(3, 220, 470)
    assert "V1 in 0 DC 3" in lines
    assert "R1 in out 220" in lines
    assert "R2 out 0 470" in lines


def test_contains_control_block_lines():
    lines = lines_of(5, 10000, 10000)
    for expected in CONTROL_BLOCK:
        assert expected in lines


def test_control_block_is_ordered_and_ends_with_end():
    lines = lines_of(5, 10000, 10000)
    positions = [lines.index(item) for item in CONTROL_BLOCK]
    assert positions == sorted(positions)
    assert ".end" in lines
    assert lines.index(".end") > lines.index(".endc")
    assert lines[-1] == ".end"


def test_netlist_ends_with_a_newline():
    # ngspice is happier when the final card is newline terminated.
    assert build_divider_netlist(5, 10000, 10000).endswith(".end\n")


def test_integer_values_have_no_trailing_decimal():
    lines = lines_of(5.0, 10000.0, 10000.0)
    assert "V1 in 0 DC 5" in lines
    assert "R1 in out 10000" in lines
    assert "R2 out 0 10000" in lines


def test_fractional_values_keep_their_precision():
    lines = lines_of(3.3, 4700.5, 0.25)
    assert "V1 in 0 DC 3.3" in lines
    assert "R1 in out 4700.5" in lines
    assert "R2 out 0 0.25" in lines


def test_no_spice_unit_suffixes_are_emitted():
    # "10k" and "1meg" invite parsing ambiguity; plain numbers never do.
    netlist = build_divider_netlist(5, 1_000_000, 10_000)
    assert "R1 in out 1000000" in netlist.splitlines()
    assert "meg" not in netlist.lower()


def test_negative_supply_is_formatted_as_a_signed_number():
    assert "V1 in 0 DC -5" in lines_of(-5, 10000, 10000)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected(bad):
    with pytest.raises(ValueError):
        build_divider_netlist(bad, 10000, 10000)
