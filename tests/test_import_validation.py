"""Every way a design file can be wrong, and the one way it can be right.

The validator is JavaScript, so these run it under node, which is machine
tooling here like ngspice and KLayout: present on a development machine,
absent in some CI, and skipped cleanly rather than faked when it is.

static/import-validate.js is pure by design -- no DOM, no fetch, the
catalogue handed in -- precisely so this file can exist. The rule every
case below holds to is that an invalid file changes nothing: the caller in
app.js mutates the page only after ok is true, so proving the verdict
proves the page is untouched.
"""

import io
import json
import os
import shutil
import subprocess

import pytest

from spice import circuits

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALIDATOR = os.path.join(PROJECT, "static", "import-validate.js")

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(
    not NODE, reason="node is not on PATH; the JS validator cannot be run")

#: The real catalogue, so the tests validate against the shipped circuits
#: rather than a fixture that could drift from them.
CATALOGUE = circuits.catalog()

HARNESS = r"""
const fs = require('fs');
const g = globalThis;
eval(fs.readFileSync(process.argv[2], 'utf8'));
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = g.FaradaemImport.validate(input.text, input.options || {});
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    path = tmp_path_factory.mktemp("node") / "run.js"
    path.write_text(HARNESS, encoding="ascii")
    return str(path)


def check(harness, tmp_path, text, **options):
    """Run the real validator on this text and return its verdict."""
    options.setdefault("catalogue", CATALOGUE)
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({"text": text, "options": options}),
                       encoding="utf-8")
    done = subprocess.run([NODE, harness, VALIDATOR, str(payload)],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def design(source="divider", **overrides):
    """A valid exported design, built from a real catalogue entry.

    `source` picks which circuit's parameter set to build from; any field
    can then be replaced through overrides, including `circuit` itself,
    which is how the unknown-circuit cases are written.
    """
    entry = next(item for item in CATALOGUE if item["id"] == source)
    payload = {
        "faradaem_design": 1,
        "app_version": "v1.14.0",
        "circuit": source,
        "name": entry["name"],
        "params": {spec["key"]: spec["default"] for spec in entry["params"]},
        "measured": None,
        "exported_utc": "2026-08-27T12:00:00.000Z",
    }
    payload.update(overrides)
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# the file itself
# ---------------------------------------------------------------------------


@requires_node
def test_malformed_json_is_refused(harness, tmp_path):
    out = check(harness, tmp_path, "{not json at all")
    assert out["ok"] is False
    assert "did not parse" in out["error"]


@requires_node
def test_an_empty_file_is_refused(harness, tmp_path):
    assert check(harness, tmp_path, "")["ok"] is False


@requires_node
@pytest.mark.parametrize("text", ["[]", "[1,2,3]", '"a string"', "42",
                                  "null", "true"])
def test_a_root_that_is_not_an_object_is_refused(harness, tmp_path, text):
    out = check(harness, tmp_path, text)
    assert out["ok"] is False
    assert "not a Faradaem design" in out["error"]


@requires_node
def test_an_oversized_file_is_refused_by_size_not_content(harness, tmp_path):
    """Refused before it is parsed, so a huge file costs nothing."""
    out = check(harness, tmp_path, design(), bytes=400 * 1024)
    assert out["ok"] is False
    assert "kB" in out["error"]


# ---------------------------------------------------------------------------
# the envelope
# ---------------------------------------------------------------------------


@requires_node
@pytest.mark.parametrize("schema", [2, 0, -1, 99])
def test_an_unsupported_schema_is_refused(harness, tmp_path, schema):
    out = check(harness, tmp_path, design(faradaem_design=schema))
    assert out["ok"] is False
    assert "schema version" in out["error"]


@requires_node
@pytest.mark.parametrize("schema", ["1", 1.5, None, True, [1]])
def test_a_schema_that_is_not_a_whole_number_is_refused(harness, tmp_path,
                                                        schema):
    out = check(harness, tmp_path, design(faradaem_design=schema))
    assert out["ok"] is False


@requires_node
def test_a_missing_schema_is_refused(harness, tmp_path):
    payload = json.loads(design())
    del payload["faradaem_design"]
    out = check(harness, tmp_path, json.dumps(payload))
    assert out["ok"] is False
    assert "faradaem_design" in out["error"]


@requires_node
def test_an_unknown_field_is_refused_rather_than_ignored(harness, tmp_path):
    """Ignoring it might mean ignoring the one that mattered."""
    out = check(harness, tmp_path, design(surprise={"do": "something"}))
    assert out["ok"] is False
    assert "surprise" in out["error"]


@requires_node
@pytest.mark.parametrize("version", [1, [], {}, True])
def test_a_non_string_app_version_is_refused(harness, tmp_path, version):
    out = check(harness, tmp_path, design(app_version=version))
    assert out["ok"] is False
    assert "app_version" in out["error"]


@requires_node
def test_a_different_app_version_warns_but_loads(harness, tmp_path):
    out = check(harness, tmp_path, design(app_version="v0.9.0"),
                appVersion="v1.14.0")
    assert out["ok"] is True
    assert any("v0.9.0" in note for note in out["warnings"])


@requires_node
def test_a_matching_app_version_says_nothing(harness, tmp_path):
    out = check(harness, tmp_path, design(app_version="v1.14.0"),
                appVersion="v1.14.0")
    assert out["ok"] is True
    assert out["warnings"] == []


# ---------------------------------------------------------------------------
# the circuit
# ---------------------------------------------------------------------------


@requires_node
@pytest.mark.parametrize("circuit", ["not_a_circuit", "", None, 7, []])
def test_an_unknown_circuit_is_refused(harness, tmp_path, circuit):
    out = check(harness, tmp_path, design(circuit=circuit))
    assert out["ok"] is False
    assert "catalogue does not have" in out["error"]


@requires_node
@pytest.mark.parametrize("name", [7, [], {}, True])
def test_a_non_string_name_is_refused(harness, tmp_path, name):
    out = check(harness, tmp_path, design(name=name))
    assert out["ok"] is False
    assert "name" in out["error"]


@requires_node
def test_an_absurdly_long_name_is_refused(harness, tmp_path):
    out = check(harness, tmp_path, design(name="x" * 5000))
    assert out["ok"] is False
    assert "not a name" in out["error"]


# ---------------------------------------------------------------------------
# the parameters
# ---------------------------------------------------------------------------


@requires_node
@pytest.mark.parametrize("params", ["oops", [], 5, None, True])
def test_params_that_are_not_an_object_are_refused(harness, tmp_path, params):
    out = check(harness, tmp_path, design(params=params))
    assert out["ok"] is False
    assert "params" in out["error"]


@requires_node
def test_a_missing_parameter_is_refused(harness, tmp_path):
    payload = json.loads(design())
    del payload["params"]["r1"]
    out = check(harness, tmp_path, json.dumps(payload))
    assert out["ok"] is False
    assert "missing" in out["error"]
    assert "r1" in out["error"]


@requires_node
def test_an_extra_parameter_is_refused(harness, tmp_path):
    """A parameter this circuit does not have cannot silently do nothing."""
    payload = json.loads(design())
    payload["params"]["r99"] = 1000.0
    out = check(harness, tmp_path, json.dumps(payload))
    assert out["ok"] is False
    assert "r99" in out["error"]


@requires_node
@pytest.mark.parametrize("value", ["oops", "10k", None, True, [], {}])
def test_a_parameter_that_is_not_a_number_is_refused(harness, tmp_path, value):
    payload = json.loads(design())
    payload["params"]["r1"] = value
    out = check(harness, tmp_path, json.dumps(payload))
    assert out["ok"] is False
    assert "not a finite number" in out["error"]


@requires_node
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_parameter_is_refused(harness, tmp_path, literal):
    """JSON cannot carry these, so a file with one was written by hand."""
    text = design().replace('"r1": 10000.0', '"r1": ' + literal)
    out = check(harness, tmp_path, text)
    assert out["ok"] is False


@requires_node
def test_a_parameter_below_its_minimum_is_refused(harness, tmp_path):
    entry = next(item for item in CATALOGUE if item["id"] == "divider")
    spec = next(p for p in entry["params"] if p["key"] == "r1")
    payload = json.loads(design())
    payload["params"]["r1"] = spec["min"] / 10.0
    out = check(harness, tmp_path, json.dumps(payload))
    assert out["ok"] is False
    assert "below the minimum" in out["error"]


@requires_node
def test_a_parameter_above_its_maximum_is_refused(harness, tmp_path):
    entry = next(item for item in CATALOGUE if item["id"] == "divider")
    spec = next(p for p in entry["params"] if p["key"] == "r1")
    payload = json.loads(design())
    payload["params"]["r1"] = spec["max"] * 10.0
    out = check(harness, tmp_path, json.dumps(payload))
    assert out["ok"] is False
    assert "above the maximum" in out["error"]


@requires_node
def test_the_exact_bounds_are_accepted(harness, tmp_path):
    """Inclusive: the declared limit is a legal value, not a rejected one."""
    entry = next(item for item in CATALOGUE if item["id"] == "divider")
    payload = json.loads(design())
    for spec in entry["params"]:
        payload["params"][spec["key"]] = spec["min"]
    assert check(harness, tmp_path, json.dumps(payload))["ok"] is True


# ---------------------------------------------------------------------------
# the date, and the measurements
# ---------------------------------------------------------------------------


@requires_node
@pytest.mark.parametrize("stamp", [7, [], {}, True])
def test_a_non_string_exported_utc_is_refused(harness, tmp_path, stamp):
    out = check(harness, tmp_path, design(exported_utc=stamp))
    assert out["ok"] is False
    assert "exported_utc" in out["error"]


@requires_node
@pytest.mark.parametrize("stamp", [
    "yesterday", "2026-13-01T00:00:00Z", "2026-02-31T00:00:00Z",
    "2026-08-27", "27/08/2026", "2026-08-27T99:00:00Z", "",
])
def test_an_invalid_iso_date_is_refused(harness, tmp_path, stamp):
    out = check(harness, tmp_path, design(exported_utc=stamp))
    assert out["ok"] is False
    assert "ISO date" in out["error"]


@requires_node
@pytest.mark.parametrize("stamp", [
    "2026-08-27T12:00:00Z", "2026-08-27T12:00:00.000Z",
    "2026-08-27T12:00:00+02:00", "2024-02-29T00:00:00Z",
])
def test_a_valid_iso_date_is_accepted(harness, tmp_path, stamp):
    assert check(harness, tmp_path, design(exported_utc=stamp))["ok"] is True


@requires_node
def test_an_absent_exported_utc_is_fine(harness, tmp_path):
    payload = json.loads(design())
    del payload["exported_utc"]
    out = check(harness, tmp_path, json.dumps(payload))
    assert out["ok"] is True
    assert out["design"]["exported_utc"] is None


@requires_node
def test_imported_measurements_are_never_carried_onto_the_page(harness,
                                                               tmp_path):
    """The one thing this tool promises: a number here was measured here."""
    out = check(harness, tmp_path,
                design(measured={"vout": 2.5, "f3db": 1590.0}))
    assert out["ok"] is True
    assert "measured" not in out["design"]
    assert any("not loaded" in note for note in out["warnings"])


# ---------------------------------------------------------------------------
# what a good file does
# ---------------------------------------------------------------------------


@requires_node
def test_a_valid_same_circuit_design_is_accepted(harness, tmp_path):
    out = check(harness, tmp_path, design("divider"))
    assert out["ok"] is True
    assert out["design"]["circuit"] == "divider"
    assert set(out["design"]["params"]) == {"vdd", "r1", "r2"}
    assert all(isinstance(value, (int, float))
               for value in out["design"]["params"].values())


@requires_node
@pytest.mark.parametrize("circuit", [item["id"] for item in CATALOGUE])
def test_every_catalogued_circuit_round_trips_at_its_defaults(harness,
                                                              tmp_path,
                                                              circuit):
    """Export then import must work for all ten, including cross-circuit."""
    out = check(harness, tmp_path, design(circuit))
    assert out["ok"] is True, out.get("error")
    assert out["design"]["circuit"] == circuit
    entry = next(item for item in CATALOGUE if item["id"] == circuit)
    assert set(out["design"]["params"]) == {
        spec["key"] for spec in entry["params"]}


@requires_node
def test_the_verdict_never_carries_both_a_design_and_an_error(harness,
                                                              tmp_path):
    for text in (design(), "{bad", "[]", design(circuit="nope")):
        out = check(harness, tmp_path, text)
        if out["ok"]:
            assert "error" not in out
            assert out["design"]
        else:
            assert "design" not in out
            assert out["error"]


# ---------------------------------------------------------------------------
# the page only moves after a yes
# ---------------------------------------------------------------------------


def test_the_caller_mutates_nothing_before_the_verdict():
    """Read as code: applyImportedDesign is reachable only under ok."""
    app = io.open(os.path.join(PROJECT, "static", "app.js"),
                  encoding="utf-8").read()
    body = app.split("function importDesign(")[1].split("\n  id(")[0]

    assert "validateImportedDesign(" in body
    assert "if (!result.ok)" in body
    # The only page-mutating call in the function comes after that check.
    assert body.index("if (!result.ok)") < body.index("applyImportedDesign(")
    # And nothing selects a circuit or writes an input before it.
    before = body[:body.index("if (!result.ok)")]
    for forbidden in ("select(", "inputs[", "onEdit(", "run(", "clearResult("):
        assert forbidden not in before, forbidden


def test_the_static_deployment_does_not_measure_an_import():
    app = io.open(os.path.join(PROJECT, "static", "app.js"),
                  encoding="utf-8").read()
    body = app.split("function applyImportedDesign(")[1].split(
        "\n  function ")[0]
    assert "if (isStatic)" in body
    assert "run locally to simulate" in body
    # The old copy claimed a measurement the static site cannot make.
    assert "Measuring it here now" not in app
    # And run() is only reached on the far side of the static return.
    assert body.index("if (isStatic)") < body.index("run();")
