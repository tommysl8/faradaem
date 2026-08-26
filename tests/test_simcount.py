"""Counting the simulations, at the only two places one happens.

The plan's headline metric for comparing design methods is how many
simulations each spent. Counting that at the caller was wrong in one
direction: run_pvt is eleven ngspice runs and lay_out is two, and both
counted as zero, while the plain optimizer was counted exactly. The arms
were never on one axis.

These tests hold the counter to the only standard that matters: it counts
subprocesses, not intentions.
"""

import pytest

from spice import circuits, ledger, pvt, runner

requires_ngspice = pytest.mark.skipif(
    not runner.find_ngspice(),
    reason="ngspice is needed to count real simulations",
)


def test_two_observers_at_once_are_refused():
    """Two budgets in force cannot both be the budget."""
    with runner.observing(runner.SimObserver()):
        with pytest.raises(runner.NgspiceRunError):
            with runner.observing(runner.SimObserver()):
                pass


def test_the_observer_is_removed_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with runner.observing(runner.SimObserver()):
            raise ValueError("boom")
    assert runner.observer() is None


def test_nothing_is_counted_without_an_observer():
    assert runner.observer() is None


def test_a_median_of_nothing_is_nothing():
    assert runner.SimObserver().median_seconds() is None


def test_the_budget_is_checked_before_the_subprocess_not_after():
    """A budget that lets the last run start has not enforced a budget."""
    watcher = runner.SimObserver(budget=0)
    with pytest.raises(runner.SimBudgetExhausted):
        watcher.about_to_run()
    assert watcher.count == 0


def test_exhausting_the_budget_is_its_own_error():
    """An arm that runs out has not failed to converge. The difference has
    to be recorded rather than inferred."""
    assert issubclass(runner.SimBudgetExhausted, runner.NgspiceRunError)


# ---------------------------------------------------------------------------
# against the real simulator
# ---------------------------------------------------------------------------


@requires_ngspice
def test_one_simulation_is_one_run():
    watcher = runner.SimObserver()
    with runner.observing(watcher):
        circuits.simulate("ota_5t", circuits.defaults("ota_5t"))
    assert watcher.count == 1


@requires_ngspice
def test_the_corner_suite_is_counted_and_used_to_be_zero():
    """Eleven ngspice runs. The metric that decided which design method was
    cheapest could not see a single one of them."""
    watcher = runner.SimObserver()
    with runner.observing(watcher):
        pvt.run_pvt("ota_5t", circuits.defaults("ota_5t"))
    assert watcher.count == 11
    assert watcher.median_seconds() > 0.0


@requires_ngspice
def test_a_layout_run_is_two_and_used_to_be_zero():
    watcher = runner.SimObserver()
    with runner.observing(watcher):
        circuits.run_layout("ota_5t", circuits.defaults("ota_5t"))
    assert watcher.count == 2


@requires_ngspice
def test_the_budget_stops_the_work():
    watcher = runner.SimObserver(budget=2)
    with pytest.raises(runner.SimBudgetExhausted):
        with runner.observing(watcher):
            for step in range(6):
                circuits.simulate(
                    "ota_5t",
                    dict(circuits.defaults("ota_5t"), ibias=2e-5 + step * 1e-6),
                )
    assert watcher.count == 2


@requires_ngspice
def test_every_simulation_is_recorded_with_what_it_ran(tmp_path):
    book = ledger.Ledger(directory=str(tmp_path), stamp_provenance=False)
    watcher = runner.SimObserver(ledger=book, arm="optimizer", exp="t")
    with runner.observing(watcher):
        circuits.simulate("ota_5t", circuits.defaults("ota_5t"))

    sims = [item for item in ledger.read(book.path)["records"]
            if item["kind"] == "sim"]
    assert len(sims) == 1
    entry = sims[0]
    assert entry["arm"] == "optimizer"
    assert entry["sim_index"] == 1
    assert entry["duration_s"] > 0.0
    assert entry["returncode"] == 0
    # The deck itself is hashed, so a recorded run can be shown to be the
    # run it says it was rather than taken on trust.
    assert len(entry["netlist_sha256"]) == 64
    assert entry["netlist_bytes"] > 0


@requires_ngspice
def test_the_same_deck_hashes_the_same_way(tmp_path):
    """Two runs of one design must be recognisable as the same deck, or a
    replay cannot check anything."""
    book = ledger.Ledger(directory=str(tmp_path), stamp_provenance=False)
    watcher = runner.SimObserver(ledger=book)
    with runner.observing(watcher):
        circuits.simulate("ota_5t", circuits.defaults("ota_5t"))
        circuits.simulate("ota_5t", circuits.defaults("ota_5t"))

    hashes = [item["netlist_sha256"]
              for item in ledger.read(book.path)["records"]
              if item["kind"] == "sim"]
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]
