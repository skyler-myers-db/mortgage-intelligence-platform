"""Feature C: loan-officer assignment + follow-up reminder on approve.

The approver can route an approved borrower to a loan officer and ask to
"follow up in N days". The endpoint PERSISTS both the assignment and a
computed ``follow_up_at`` timestamp on the approval row and echoes them in
the response. Reminder DELIVERY (scheduling/notification) is explicitly out
of scope and not tested here -- only persistence + echo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.schemas.offer import OutreachApproveRequest

_DISCLOSURE = (
    "Summit Mortgage, NMLS #123456. Equal Housing Lender. "
    "Reply unsubscribe to opt out."
)
_DRAFT_BODY = f"Reviewed and approved governed outreach. {_DISCLOSURE}"


def _approve_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "borrower_id": "B-48291",
        "offer_code": "refi_plus_heloc",
        "channel": "email",
        "draft_body": _DRAFT_BODY,
    }
    payload.update(overrides)
    return payload


def test_approve_persists_assignment_and_follow_up_and_echoes_them(
    fake_lakebase_client,
) -> None:
    before = datetime.now(UTC)
    response = TestClient(app).post(
        "/api/outreach/approve",
        json=_approve_payload(
            assigned_to_email="lo01@summit.example",
            follow_up_in_days=5,
        ),
    )
    after = datetime.now(UTC)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approved"] is True
    assert body["assigned_to_email"] == "lo01@summit.example"

    follow_up_at = datetime.fromisoformat(body["follow_up_at"])
    # now + 5 days, allowing for the request-handling window.
    assert before + timedelta(days=5) <= follow_up_at <= after + timedelta(days=5)

    # Persisted on the approval row (both columns threaded through INSERT).
    approval = fake_lakebase_client.approvals[-1]
    assert approval["assigned_to_email"] == "lo01@summit.example"
    persisted = approval["follow_up_at"]
    assert isinstance(persisted, datetime)
    assert before + timedelta(days=5) <= persisted <= after + timedelta(days=5)


@pytest.mark.parametrize("days", [0, 31, -1, 100])
def test_follow_up_in_days_out_of_range_is_422(days: int) -> None:
    response = TestClient(app).post(
        "/api/outreach/approve",
        json=_approve_payload(follow_up_in_days=days),
    )
    assert response.status_code == 422, response.text


def test_malformed_assigned_to_email_is_422() -> None:
    response = TestClient(app).post(
        "/api/outreach/approve",
        json=_approve_payload(assigned_to_email="not-an-email"),
    )
    assert response.status_code == 422, response.text


def test_assigned_to_email_outside_staff_domain_is_422() -> None:
    # Email-shaped but not an approved internal staff domain.
    response = TestClient(app).post(
        "/api/outreach/approve",
        json=_approve_payload(assigned_to_email="jane@gmail.com"),
    )
    assert response.status_code == 422, response.text


def test_approve_without_assignment_or_follow_up_still_works(
    fake_lakebase_client,
) -> None:
    response = TestClient(app).post(
        "/api/outreach/approve",
        json=_approve_payload(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approved"] is True
    assert body["assigned_to_email"] is None
    assert body["follow_up_at"] is None

    approval = fake_lakebase_client.approvals[-1]
    assert approval["assigned_to_email"] is None
    assert approval["follow_up_at"] is None


def test_schema_accepts_valid_assignment_and_follow_up_window() -> None:
    req = OutreachApproveRequest(
        borrower_id="B-48291",
        assigned_to_email="LO01@Summit.example",
        follow_up_in_days=30,
    )
    # Internal-staff validator normalizes to lowercase.
    assert req.assigned_to_email == "lo01@summit.example"
    assert req.follow_up_in_days == 30


@pytest.mark.parametrize("days", [0, 31])
def test_schema_rejects_out_of_range_follow_up_window(days: int) -> None:
    with pytest.raises(ValidationError):
        OutreachApproveRequest(borrower_id="B-48291", follow_up_in_days=days)
