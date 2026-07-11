"""Assignment lifecycle rules: one step forward, everything else rejected."""

from __future__ import annotations

import pytest

from backend.schemas.loan_officer import ASSIGNMENT_LIFECYCLE
from backend.services.loan_officer_state import (
    IllegalStatusTransitionError,
    legal_next_status,
    validate_status_transition,
)

LEGAL_STEPS = [
    ("assigned", "contact_drafted"),
    ("contact_drafted", "approved"),
    ("approved", "actioned"),
    ("actioned", "outcome_recorded"),
]


def test_lifecycle_order_is_the_contracted_five_stages() -> None:
    assert ASSIGNMENT_LIFECYCLE == (
        "assigned",
        "contact_drafted",
        "approved",
        "actioned",
        "outcome_recorded",
    )


@pytest.mark.parametrize(("from_status", "to_status"), LEGAL_STEPS)
def test_every_single_step_forward_is_legal(from_status: str, to_status: str) -> None:
    validate_status_transition(from_status, to_status)  # must not raise
    assert legal_next_status(from_status) == to_status


def test_terminal_stage_has_no_next_status() -> None:
    assert legal_next_status("outcome_recorded") is None
    with pytest.raises(IllegalStatusTransitionError, match="terminal"):
        validate_status_transition("outcome_recorded", "assigned")


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("assigned", "approved"),  # skip a stage
        ("assigned", "outcome_recorded"),  # skip to the end
        ("contact_drafted", "assigned"),  # backwards
        ("approved", "contact_drafted"),  # backwards
        ("actioned", "approved"),  # backwards
        ("assigned", "assigned"),  # no-op re-entry
    ],
)
def test_skips_backwards_and_reentry_are_illegal(from_status: str, to_status: str) -> None:
    with pytest.raises(IllegalStatusTransitionError):
        validate_status_transition(from_status, to_status)


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        ("unknown", "contact_drafted"),
        ("assigned", "closed"),
        ("", "assigned"),
    ],
)
def test_unknown_statuses_are_illegal(from_status: str, to_status: str) -> None:
    with pytest.raises(IllegalStatusTransitionError, match="unknown|terminal"):
        validate_status_transition(from_status, to_status)


def test_legal_next_status_for_unknown_status_is_none() -> None:
    assert legal_next_status("bogus") is None
