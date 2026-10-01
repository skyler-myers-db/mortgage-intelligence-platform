"""Request-side contract of POST /outreach/approval-requests (flow-02 / shell-06)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.schemas.approval_request import (
    MAX_APPROVAL_REQUEST_BORROWERS,
    ApprovalRequestCreate,
    ApprovalRequestRow,
)

IDS = ["B-ARQTESTX00001", "B-ARQTESTX00002"]
NOTE = "Rate-sensitive refinance candidates in our footprint."


def _create(**overrides: object) -> ApprovalRequestCreate:
    values: dict[str, object] = {"borrower_ids": IDS, "rationale": NOTE, "request_key": str(uuid4())}
    values.update(overrides)
    return ApprovalRequestCreate.model_validate(values)


def test_a_well_formed_request_validates() -> None:
    created = _create()
    assert created.borrower_ids == IDS
    assert created.rationale == NOTE


@pytest.mark.parametrize(
    "overrides",
    [
        {"borrower_ids": []},
        {"borrower_ids": [f"B-{index:013d}" for index in range(MAX_APPROVAL_REQUEST_BORROWERS + 1)]},
        {"borrower_ids": ["B-ARQTESTX00001", "B-ARQTESTX00001"]},
        {"borrower_ids": ["12345"]},
        {"rationale": ""},
        {"rationale": "   "},
        {"rationale": "n" * 501},
        {"rationale": "Please ask Jane Smith to call them"},
        {"request_key": "not an opaque key"},
        {"request_key": "jane@summit.example"},
        {"campaign_id": str(uuid4())},
    ],
    ids=[
        "no-borrowers",
        "over-500",
        "duplicate",
        "not-public-id",
        "empty-note",
        "blank-note",
        "long-note",
        "name-shaped-note",
        "free-text-key",
        "email-key",
        "no-campaign-field",
    ],
)
def test_malformed_requests_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _create(**overrides)


def test_the_request_carries_no_campaign_or_channel_field() -> None:
    assert set(ApprovalRequestCreate.model_fields) == {"borrower_ids", "rationale", "request_key"}


def test_row_states_are_the_closed_vocabulary() -> None:
    for state in ("open", "approved", "rejected", "decided_outside", "withdrawn", "expired"):
        ApprovalRequestRow(borrower_id=IDS[0], state=state)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ApprovalRequestRow(borrower_id=IDS[0], state="held")  # type: ignore[arg-type]
