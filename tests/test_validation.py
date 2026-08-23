"""Validation for POST /simulate.

The server keeps this logic in one importable function, so these tests exercise
the same code path a live request would, without opening a socket.
"""

import pytest

from server import ValidationError, analytic_divider, validate_simulate_request


def test_accepts_a_well_formed_body():
    assert validate_simulate_request({"vdd": 5, "r1": 10000, "r2": 10000}) == (
        5.0,
        10000.0,
        10000.0,
    )


def test_returns_floats_even_for_integer_input():
    vdd, r1, r2 = validate_simulate_request({"vdd": 5, "r1": 220, "r2": 470})
    assert all(isinstance(value, float) for value in (vdd, r1, r2))


def test_accepts_numeric_strings():
    assert validate_simulate_request({"vdd": "3.3", "r1": "1e3", "r2": "2e3"}) == (
        3.3,
        1000.0,
        2000.0,
    )


def test_ignores_unknown_extra_fields():
    assert validate_simulate_request(
        {"vdd": 5, "r1": 10000, "r2": 10000, "temperature": 27}
    ) == (5.0, 10000.0, 10000.0)


@pytest.mark.parametrize("missing", ["vdd", "r1", "r2"])
def test_missing_field_is_rejected(missing):
    body = {"vdd": 5, "r1": 10000, "r2": 10000}
    del body[missing]
    with pytest.raises(ValidationError) as excinfo:
        validate_simulate_request(body)
    assert missing in str(excinfo.value)


def test_empty_body_is_rejected():
    with pytest.raises(ValidationError):
        validate_simulate_request({})


@pytest.mark.parametrize("payload", [None, [], "vdd=5", 5, ()])
def test_non_object_body_is_rejected(payload):
    with pytest.raises(ValidationError):
        validate_simulate_request(payload)


@pytest.mark.parametrize("bad", ["abc", "", None, [], {}, True, "1,5"])
def test_non_numeric_value_is_rejected(bad):
    with pytest.raises(ValidationError) as excinfo:
        validate_simulate_request({"vdd": bad, "r1": 10000, "r2": 10000})
    assert "vdd" in str(excinfo.value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "nan", "inf"])
def test_non_finite_value_is_rejected(bad):
    with pytest.raises(ValidationError):
        validate_simulate_request({"vdd": bad, "r1": 10000, "r2": 10000})


@pytest.mark.parametrize("field", ["r1", "r2"])
@pytest.mark.parametrize("bad", [0, 0.0, -1, -10000.5, "0", "-5"])
def test_zero_or_negative_resistance_is_rejected(field, bad):
    body = {"vdd": 5, "r1": 10000, "r2": 10000}
    body[field] = bad
    with pytest.raises(ValidationError) as excinfo:
        validate_simulate_request(body)
    message = str(excinfo.value)
    assert field in message
    assert "0 ohms" in message


def test_zero_and_negative_supply_are_allowed():
    # Only the resistances are constrained; a 0 V or negative rail is legal.
    assert validate_simulate_request({"vdd": 0, "r1": 10, "r2": 10}) == (0.0, 10.0, 10.0)
    assert validate_simulate_request({"vdd": -5, "r1": 10, "r2": 10}) == (
        -5.0,
        10.0,
        10.0,
    )


def test_analytic_divider_matches_the_closed_form():
    assert analytic_divider(5.0, 10000.0, 10000.0) == pytest.approx(2.5)
    assert analytic_divider(3.3, 1000.0, 2000.0) == pytest.approx(2.2)
