"""S6 approval-funnel analytics endpoint contracts.

Five live stages: population + high-opportunity from the S1 headline
metric view (via the analytics repository seam) and approved / actioned /
outcome_recorded from live Lakebase workflow state. Approving in the API
must move the funnel immediately (the sales-state cache hook invalidates
the funnel cache), who-approved-what surfaces the approver identity, and
per-LO drill-down returns real per-officer counts with honest zero states.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.scoring import is_high_opportunity
from tests.fixtures import mock_population as mock_data

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})

LO_01 = "55555555-5555-4555-8555-555555555501"
LO_02 = "55555555-5555-4555-8555-555555555502"

_DISCLOSURE = (
    "Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out."
)
_DRAFT_BODY = f"Contact a loan officer to review available mortgage options. {_DISCLOSURE}"


def _funnel() -> dict:
    response = client.get("/api/analytics/funnel")
    assert response.status_code == 200, response.text
    return response.json()


def _drill(loan_officer_id: str):
    return client.get(f"/api/analytics/funnel/loan-officers/{loan_officer_id}")


def _stage(body: dict, stage: str) -> dict:
    return next(s for s in body["stages"] if s["stage"] == stage)


def _approve(borrower_id: str) -> dict:
    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": borrower_id,
            "offer_code": "refi_plus_heloc",
            "channel": "email",
            "draft_subject": "Your mortgage review",
            "draft_body": _DRAFT_BODY,
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assign(borrower_id: str, loan_officer_id: str = LO_01) -> dict:
    response = client.post(
        "/api/loan-officers/assignments",
        json={
            "borrower_id": borrower_id,
            "loan_officer_id": loan_officer_id,
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["assignment"]


def _advance(assignment_id: str, statuses: tuple[str, ...]) -> None:
    for status in statuses:
        response = client.patch(
            f"/api/loan-officers/assignments/{assignment_id}/status",
            json={"status": status},
        )
        assert response.status_code == 200, response.text


def _dispose(borrower_id: str, lo_email: str = "lo01@summit.example") -> None:
    """Log a real call disposition — the Lakebase write that makes a
    borrower 'actioned' in both the executive funnel and this tab."""
    response = client.post(
        f"/api/leads/{borrower_id}/disposition",
        json={
            "lo_email": lo_email,
            "outcome": "connected",
            "notes": "Reviewed refinance options.",
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text


def test_funnel_zero_state_is_honest_and_population_stages_are_live() -> None:
    body = _funnel()

    assert [s["stage"] for s in body["stages"]] == [
        "population",
        "high_opportunity",
        "approved",
        "actioned",
        "outcome_recorded",
    ]
    borrowers = list(mock_data.BORROWERS)
    population = _stage(body, "population")
    assert population["borrower_count"] == len(borrowers)
    high = _stage(body, "high_opportunity")
    assert high["borrower_count"] == sum(
        1 for b in borrowers if is_high_opportunity(b.opportunity_score)
    )
    # No approvals or assignments yet: honest zeros, not invented numbers.
    for stage in ("approved", "actioned", "outcome_recorded"):
        assert _stage(body, stage)["borrower_count"] == 0
    assert body["approvals"] == []
    # Every active loan officer appears with an honest zero state.
    officers = {row["loan_officer_id"]: row for row in body["loan_officers"]}
    assert set(officers) == {LO_01, LO_02}
    assert all(row["total_active"] == 0 for row in officers.values())


def test_every_stage_cites_its_source() -> None:
    body = _funnel()
    by_stage = {s["stage"]: s["source"] for s in body["stages"]}
    assert "portfolio_headline_metric_view" in by_stage["population"]
    assert "portfolio_headline_metric_view" in by_stage["high_opportunity"]
    assert "mip_app.approvals" in by_stage["approved"]
    # 2026-08-08 UX walk: the Actioned stage used to cite lead_assignments
    # while counting a different concept from the executive funnel's
    # Actioned. It now cites the tables its number is actually derived
    # from — the same pair jobs/sync_lifecycle_state.py mirrors into gold.
    assert by_stage["actioned"] == "mip_app.approvals + mip_app.call_dispositions"
    assert "mip_app.call_dispositions" in by_stage["outcome_recorded"]
    assert "mip_app.lead_assignments" in by_stage["outcome_recorded"]


def test_approving_in_the_api_moves_the_funnel_immediately() -> None:
    before = _stage(_funnel(), "approved")["borrower_count"]

    _approve(mock_data.BORROWERS[0].borrower_id)

    after = _funnel()
    assert _stage(after, "approved")["borrower_count"] == before + 1
    # Who-approved-what: the approver identity is on the approval row.
    approvals = after["approvals"]
    assert approvals, "expected the approve decision to surface"
    assert approvals[0]["actor_email"] == "skyler@entrada.ai"
    assert approvals[0]["borrower_id"] == mock_data.BORROWERS[0].borrower_id


def test_lifecycle_and_outcome_stages_count_cumulatively() -> None:
    first_id = mock_data.BORROWERS[0].borrower_id
    second_id = mock_data.BORROWERS[1].borrower_id
    first = _assign(first_id, LO_01)
    second = _assign(second_id, LO_01)
    _advance(first["assignment_id"], ("contact_drafted", "approved", "actioned"))
    _advance(
        second["assignment_id"],
        ("contact_drafted", "approved", "actioned"),
    )
    _approve(first_id)
    _approve(second_id)
    _dispose(first_id)
    _dispose(second_id)
    outcome = client.post(
        f"/api/loan-officers/assignments/{second['assignment_id']}/outcome",
        json={"outcome": "success"},
    )
    assert outcome.status_code == 200, outcome.text

    body = _funnel()
    # Both borrowers were approved and then worked (a real call disposition);
    # one of the two also reached the terminal assignment stage.
    assert _stage(body, "approved")["borrower_count"] == 2
    assert _stage(body, "actioned")["borrower_count"] == 2
    assert _stage(body, "outcome_recorded")["borrower_count"] == 1


def test_actioned_needs_outreach_not_just_an_advanced_assignment() -> None:
    """The regression this stage was rewritten for.

    Advancing an LO assignment to the ``actioned`` status is an internal
    workflow move, not borrower outreach. The executive funnel has always
    required an approved borrower with a recorded disposition; this tab used
    to publish the assignment-progression count under the same word, so one
    page showed two different numbers for "Actioned" (live 2026-08-08: 3 vs
    0). Assignment progression alone must not move the stage.
    """
    borrower_id = mock_data.BORROWERS[0].borrower_id
    assignment = _assign(borrower_id, LO_01)
    _advance(assignment["assignment_id"], ("contact_drafted", "approved", "actioned"))
    _approve(borrower_id)

    assert _stage(_funnel(), "actioned")["borrower_count"] == 0

    _dispose(borrower_id)

    assert _stage(_funnel(), "actioned")["borrower_count"] == 1


def test_per_lo_drill_returns_real_per_officer_counts() -> None:
    first = _assign(mock_data.BORROWERS[0].borrower_id, LO_01)
    _assign(mock_data.BORROWERS[1].borrower_id, LO_01)
    _advance(first["assignment_id"], ("contact_drafted", "approved", "actioned"))
    outcome = client.post(
        f"/api/loan-officers/assignments/{first['assignment_id']}/outcome",
        json={"outcome": "declined"},
    )
    assert outcome.status_code == 200, outcome.text

    response = _drill(LO_01)
    assert response.status_code == 200, response.text
    body = response.json()
    officer = body["officer"]
    assert officer["loan_officer_id"] == LO_01
    assert officer["assigned"] == 1
    assert officer["outcome_recorded"] == 1
    assert officer["total_active"] == 2
    assert len(body["assignments"]) == 2
    assert body["outcome_counts"] == {"success": 0, "no_response": 0, "declined": 1}

    # An LO with no assignments drills to an honest zero state, not a 404.
    empty = _drill(LO_02)
    assert empty.status_code == 200, empty.text
    assert empty.json()["officer"]["total_active"] == 0
    assert empty.json()["assignments"] == []

    missing = _drill("99999999-9999-4999-8999-999999999999")
    assert missing.status_code == 404
