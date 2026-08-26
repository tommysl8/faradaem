"""Closing the loop on the drawn circuit, and refusing to overstate it.

The tool could already say what the interconnect cost. Saying it and doing
nothing is a diagnosis without a treatment. These tests hold the loop to
the two things that make it worth having: it sizes against a deck that has
the wiring in it, and it never reports a design it has not drawn and
measured.
"""

import pytest

from spice import circuits, closeloop, ledger, layout, runner

requires_ngspice = pytest.mark.skipif(
    not runner.find_ngspice() or not layout.tech_available(),
    reason="ngspice and the PDK are needed to draw and measure",
)


# ---------------------------------------------------------------------------
# what it refuses
# ---------------------------------------------------------------------------


def test_a_circuit_with_no_layout_is_refused():
    with pytest.raises(closeloop.LoopError) as caught:
        closeloop.parasitics_of("divider", circuits.defaults("divider"))
    assert "no layout" in str(caught.value)


def test_a_circuit_with_no_design_block_is_refused():
    with pytest.raises(closeloop.LoopError) as caught:
        closeloop.close_loop("nfet_cs_amp",
                             circuits.defaults("nfet_cs_amp"), {})
    assert "nothing to re-size" in str(caught.value)


def test_an_outcome_that_is_not_met_is_not_a_success():
    """Every way this can end except one leaves a circuit that misses its
    spec when drawn."""
    assert "met" in closeloop.OUTCOMES
    for outcome in closeloop.OUTCOMES:
        if outcome != "met":
            assert outcome != "success"


def test_the_result_says_what_an_unmet_outcome_means():
    source = open(closeloop.__file__, encoding="utf-8").read()
    assert "is not a design that meets the spec" in source


def test_a_settled_sizing_is_recognised():
    """A round that moves nothing is chasing extraction noise."""
    assert closeloop._moved({"a": 1.0}, {"a": 1.0}) == 0.0
    assert closeloop._moved({"a": 1.0}, {"a": 1.5}) == pytest.approx(0.5)
    # A parameter that was zero cannot have moved fractionally.
    assert closeloop._moved({"a": 0.0}, {"a": 1.0}) == 0.0


# ---------------------------------------------------------------------------
# what it does
# ---------------------------------------------------------------------------


@requires_ngspice
def test_the_wiring_is_re_extracted_every_round():
    """The wiring is a consequence of the sizing: wider devices sit further
    apart and their nets are longer. Reusing round one's extraction is
    sizing against a layout that no longer exists."""
    base = circuits.defaults("opamp_two_stage")
    wider = dict(base, wpair=4e-5)

    one, _ = closeloop.parasitics_of("opamp_two_stage", base)
    two, _ = closeloop.parasitics_of("opamp_two_stage", wider)

    total_one = sum(item["capacitance_f"] for item in one.values())
    total_two = sum(item["capacitance_f"] for item in two.values())
    assert total_two != total_one


@requires_ngspice
def test_a_drawn_circuit_that_meets_its_spec_stops_after_one_round():
    """No redesign when the drawing already works: the loop is a treatment,
    not a ritual."""
    easy = {"loop_gain_db": 60.0, "f_crossover": 5e6,
            "phase_margin": 60.0, "power": 2e-4}
    found = closeloop.close_loop(
        "opamp_two_stage", circuits.defaults("opamp_two_stage"), easy,
        budget=8, max_rounds=3)

    assert found["met"] is True
    assert found["outcome"] == "met"
    assert len(found["rounds"]) == 1
    assert "meets the spec" in found["rounds"][0]["action"]


@requires_ngspice
def test_the_loop_resizes_when_the_wiring_breaks_the_spec_and_records_it(
        tmp_path):
    """The whole point. A phase margin the schematic met and the drawn
    circuit missed, fixed by sizing against the drawn circuit."""
    book = ledger.Ledger(directory=str(tmp_path), stamp_provenance=False)
    targets = {"loop_gain_db": 70.0, "f_crossover": 1.3e7,
               "phase_margin": 73.0, "power": 2e-4}

    found = closeloop.close_loop(
        "opamp_two_stage", circuits.defaults("opamp_two_stage"), targets,
        budget=12, max_rounds=2, book=book)

    first = found["rounds"][0]
    # The schematic meets 73 degrees; the drawn circuit does not.
    assert first["feasible"] is False
    assert first["binding_goal"] == "phase_margin"
    assert first["interconnect_f"] > 0.0
    assert "resized" in first["action"]

    if found["met"]:
        last = found["rounds"][-1]
        assert last["feasible"] is True
        assert last["params"] != first["params"]

    # And every round is recorded, so the cost of closing the loop is
    # visible rather than hidden behind the final answer.
    rounds = [item for item in ledger.read(book.path)["records"]
              if item["kind"] == "layout"]
    assert len(rounds) == len(found["rounds"])


@requires_ngspice
def test_the_resize_runs_against_a_deck_that_has_the_wiring_in_it():
    """Sizing against the schematic would re-derive the design that just
    failed. The transform is what makes the second round different."""
    source = open(closeloop.__file__, encoding="utf-8").read()
    assert "transform=transform" in source

    parasitics, _ = closeloop.parasitics_of(
        "opamp_two_stage", circuits.defaults("opamp_two_stage"))
    transform = layout.parasitic_transform(parasitics)
    clean = circuits.simulate("opamp_two_stage",
                              circuits.defaults("opamp_two_stage"))
    loaded = circuits.simulate("opamp_two_stage",
                               circuits.defaults("opamp_two_stage"),
                               transform=transform)
    # The wiring costs phase margin; that is the thing being sized against.
    assert loaded["phase_margin"] < clean["phase_margin"]
