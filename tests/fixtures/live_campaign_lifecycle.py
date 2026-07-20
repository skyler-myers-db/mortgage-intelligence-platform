"""Shared public lifecycle setup for campaign-bound live approval fixtures."""

from __future__ import annotations

from collections.abc import Callable

LiveRequest = Callable[..., tuple[int, object]]


def approve_campaign_for_outreach(
    campaign_id: str,
    *,
    request: LiveRequest,
    approver_token: str,
) -> None:
    """Persist ``draft -> pending_review -> approved`` through the app API."""

    status, pending = request(
        "PATCH",
        f"/api/campaigns/{campaign_id}",
        {
            "status": "pending_review",
            "expected_status": "draft",
            "rationale": "Submit generated campaign copy for governed review.",
        },
    )
    assert status == 200, pending
    assert isinstance(pending, dict)
    assert pending.get("status") == "pending_review"

    status, approved = request(
        "PATCH",
        f"/api/campaigns/{campaign_id}",
        {
            "status": "approved",
            "expected_status": "pending_review",
            "rationale": "Governed campaign copy reviewed for approval.",
        },
        token=approver_token,
    )
    assert status == 200, approved
    assert isinstance(approved, dict)
    assert approved.get("status") == "approved"

    status, persisted = request("GET", f"/api/campaigns/{campaign_id}")
    assert status == 200, persisted
    assert isinstance(persisted, dict)
    assert persisted.get("status") == "approved"
