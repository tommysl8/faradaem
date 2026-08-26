"""The record of what was tried, and the properties that make it evidence.

A log that loses records, invents categories, or cannot say what produced
its numbers is not evidence, it is reassurance. Each test here is one of
those failure modes.
"""

import io
import json
import os

import pytest

from spice import ledger


@pytest.fixture
def directory(tmp_path):
    return str(tmp_path / "runs")


def test_a_run_writes_one_json_object_per_line(directory):
    book = ledger.Ledger(directory=directory, clock=lambda: 1000.0,
                         token="abc123", stamp_provenance=False)
    book.record("start", by="human", arm="human", circuit="ota_5t")
    book.record("result", by="human", arm="human", feasible=True)

    lines = io.open(book.path, encoding="utf-8").read().strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


def test_records_are_numbered_in_the_order_they_happened(directory):
    book = ledger.Ledger(directory=directory, stamp_provenance=False)
    for _ in range(5):
        book.record("attempt", by="optimizer")
    seqs = [item["seq"] for item in ledger.read(book.path)["records"]]
    assert seqs == [1, 2, 3, 4, 5]


def test_an_unknown_kind_is_refused(directory):
    """A typo must not invent a category later analysis will not look for."""
    book = ledger.Ledger(directory=directory, stamp_provenance=False)
    with pytest.raises(ledger.LedgerError):
        book.record("attemtp", by="optimizer")


def test_an_unknown_author_is_refused(directory):
    """Telling the methods apart is the entire point of the comparison."""
    book = ledger.Ledger(directory=directory, stamp_provenance=False)
    with pytest.raises(ledger.LedgerError):
        book.record("attempt", by="magic")


def test_two_runs_started_together_are_still_told_apart(directory):
    one = ledger.Ledger(directory=directory, clock=lambda: 1000.0,
                        token="aaaaaa", stamp_provenance=False)
    two = ledger.Ledger(directory=directory, clock=lambda: 1000.0,
                        token="bbbbbb", stamp_provenance=False)
    assert one.run_id != two.run_id
    assert one.path != two.path


def test_the_same_inputs_produce_the_same_file(directory):
    """A test that cannot reproduce a run byte for byte cannot detect a
    change in what is recorded."""
    def build(where):
        book = ledger.Ledger(directory=where, clock=lambda: 1234.5,
                             token="fixed1", stamp_provenance=False)
        book.record("attempt", by="optimizer", circuit="ota_5t",
                    params={"ibias": 2e-05}, measured={"loop_gain_db": 37.0})
        return io.open(book.path, encoding="utf-8").read()

    assert build(directory) == build(directory + "-again")


# ---------------------------------------------------------------------------
# provenance: what the numbers came out of
# ---------------------------------------------------------------------------


def test_provenance_is_the_first_record(directory):
    book = ledger.Ledger(directory=directory)
    book.record("attempt", by="optimizer")
    records = ledger.read(book.path)["records"]
    assert records[0]["kind"] == "provenance"
    assert records[0]["seq"] == 1


def test_provenance_names_the_commit_and_whether_the_tree_was_clean():
    """A run made from an edited tree is not reproducible from the commit
    alone, and the record has to say so."""
    found = ledger.provenance()
    assert "git" in found
    if found["git"] is not None:
        assert len(found["git"]["commit"]) == 40
        assert isinstance(found["git"]["clean"], bool)


def test_provenance_names_the_simulator_and_the_process():
    found = ledger.provenance()
    for key in ("ngspice", "pdk", "klayout", "python", "platform"):
        assert key in found, key
    if found["pdk"] is not None:
        # The corner matters: the same sizing measures differently at ss.
        assert found["pdk"]["corner"]


def test_what_cannot_be_determined_is_null_and_not_missing():
    """A missing key reads as an oversight. A null reads as what it is."""
    found = ledger.provenance()
    for key in ("git", "ngspice", "pdk", "klayout"):
        assert key in found, key


# ---------------------------------------------------------------------------
# reading it back
# ---------------------------------------------------------------------------


def test_a_truncated_last_line_is_reported_and_not_dropped(directory):
    """What a crash leaves behind. Dropping it silently turns a partial run
    into one that looks complete."""
    book = ledger.Ledger(directory=directory, stamp_provenance=False)
    book.record("attempt", by="optimizer")
    with io.open(book.path, "a", encoding="utf-8") as out:
        out.write('{"run": "x", "seq": 2, "kind": "att')

    loaded = ledger.read(book.path)
    assert len(loaded["records"]) == 1
    assert loaded["damaged"] == 1


def test_a_summary_counts_without_judging(directory):
    book = ledger.Ledger(directory=directory, stamp_provenance=False)
    book.record("attempt", by="optimizer", arm="optimizer")
    book.record("attempt", by="optimizer", arm="optimizer")
    book.record("attempt", by="llm", arm="llm")

    found = ledger.summarise(ledger.read(book.path))
    assert found["records"] == 3
    assert found["authors"] == {"optimizer": 2, "llm": 1}
    assert found["arms"] == {"optimizer": 2, "llm": 1}
    # Nothing here decides whether any of it was good.
    assert "score" not in found and "winner" not in found


def test_runs_are_listed_from_the_directory(directory):
    """A real run stamps its provenance on construction, so its file exists
    from the first moment. These write a record for the same reason."""
    made = []
    for token in ("aaa111", "bbb222"):
        book = ledger.Ledger(directory=directory, token=token,
                             stamp_provenance=False)
        book.record("start", by="human")
        made.append(book.path)
    assert sorted(ledger.runs(directory)) == sorted(made)


def test_a_run_leaves_a_file_the_moment_it_starts(directory):
    """A run that crashes before its first attempt still has to be findable,
    or a failed experiment looks like one that never happened."""
    book = ledger.Ledger(directory=directory)
    assert os.path.isfile(book.path)
    assert ledger.runs(directory) == [book.path]


def test_an_empty_directory_lists_nothing(tmp_path):
    assert ledger.runs(str(tmp_path / "nothing-here")) == []


# ---------------------------------------------------------------------------
# where it lives
# ---------------------------------------------------------------------------


def test_the_default_root_is_outside_the_project():
    """The project is in a syncing folder. An append-heavy log inside one is
    a bad idea twice over."""
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    saved = os.environ.pop(ledger.LEDGER_ENV_VAR, None)
    try:
        default = os.path.abspath(ledger.root())
    finally:
        if saved is not None:
            os.environ[ledger.LEDGER_ENV_VAR] = saved
    assert not default.startswith(os.path.abspath(project))


def test_the_environment_can_move_it(tmp_path):
    saved = os.environ.get(ledger.LEDGER_ENV_VAR)
    os.environ[ledger.LEDGER_ENV_VAR] = str(tmp_path)
    try:
        assert ledger.root() == str(tmp_path)
    finally:
        if saved is None:
            os.environ.pop(ledger.LEDGER_ENV_VAR, None)
        else:
            os.environ[ledger.LEDGER_ENV_VAR] = saved


def test_the_module_records_and_never_judges():
    source = open(ledger.__file__, encoding="utf-8").read().lower()
    assert "leaves the reading to whoever compares" in source
    for verdict in ("def winner", "def best_arm", "def rank"):
        assert verdict not in source, verdict
