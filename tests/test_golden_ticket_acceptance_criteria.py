# tests/test_golden_ticket_acceptance_criteria.py
"""GATE D3 — GoldenTicket.acceptance_criteria is typed AcceptanceCriterion.

GoldenTicket previously carried acceptance_criteria as Optional[List[str]].
It is now Optional[list[AcceptanceCriterion]] (still Optional). This test is
the SUBSET CONTRACT proof:

- A GoldenTicket built from AcceptanceCriterion objects validates and the
  field round-trips as AcceptanceCriterion instances.
- The field stays Optional (None default is valid).
- Backward compat: plain-string criteria (the existing seed/tests shape) are
  coerced into AcceptanceCriterion so pre-existing callers do not regress.

RED before this gate: AcceptanceCriterion is not importable from
models.eval_schemas, and a GoldenTicket built from AcceptanceCriterion objects
would store raw objects without the typed contract.
"""

from __future__ import annotations

from models.eval_schemas import AcceptanceCriterion, GoldenTicket


def test_acceptance_criterion_importable_and_minimal():
    ac = AcceptanceCriterion(condition="Yield calculated daily at UTC midnight")
    assert ac.condition == "Yield calculated daily at UTC midnight"


def test_golden_ticket_from_acceptance_criterion_objects_validates():
    acs = [
        AcceptanceCriterion(condition="AC 1"),
        AcceptanceCriterion(condition="AC 2"),
    ]
    ticket = GoldenTicket(
        title="Test Ticket",
        short_description="desc",
        acceptance_criteria=acs,
    )
    # SUBSET CONTRACT: every element is an AcceptanceCriterion instance.
    assert ticket.acceptance_criteria is not None
    assert all(isinstance(a, AcceptanceCriterion) for a in ticket.acceptance_criteria)
    assert [a.condition for a in ticket.acceptance_criteria] == ["AC 1", "AC 2"]


def test_golden_ticket_acceptance_criteria_optional():
    ticket = GoldenTicket(title="t", short_description="d")
    assert ticket.acceptance_criteria is None


def test_golden_ticket_plain_string_criteria_coerced():
    # Existing callers (seed script, test_phase11_evals) pass plain strings.
    ticket = GoldenTicket(
        title="t",
        short_description="d",
        acceptance_criteria=["AC 1", "AC 2"],
    )
    assert ticket.acceptance_criteria is not None
    assert all(isinstance(a, AcceptanceCriterion) for a in ticket.acceptance_criteria)
    assert [a.condition for a in ticket.acceptance_criteria] == ["AC 1", "AC 2"]
