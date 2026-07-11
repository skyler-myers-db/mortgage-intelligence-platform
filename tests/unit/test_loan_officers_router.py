"""Loan-officer roster + assignment lifecycle endpoint contracts (S2)."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from tests.fixtures import mock_population as mock_data

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})

LO_01 = "55555555-5555-4555-8555-555555555501"
LO_02 = "55555555-5555-4555-8555-555555555502"


def _borrower_id() -> str:
    return mock_data.BORROWERS[0].borrower_id


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
    return response.json()


def test_list_loan_officers_returns_roster_with_coverage() -> None:
    response = client.get("/api/loan-officers")
    assert response.status_code == 200
    officers = response.json()
    assert [officer["loan_officer_id"] for officer in officers] == [LO_01, LO_02]
    assert officers[0]["coverage_states"] == ["IL", "IN", "WI"]
    assert officers[0]["coverage_counties"] == ["17031", "17043", "17089"]
    assert all("geometry" not in officer for officer in officers)


def test_assign_writes_assignment_with_audit_event() -> None:
    body = _assign(_borrower_id())
    assert body["assignment"]["borrower_id"] == _borrower_id()
    assert body["assignment"]["loan_officer_id"] == LO_01
    assert body["assignment"]["status"] == "assigned"
    assert body["audit_event_id"]


def test_assign_unknown_borrower_is_404_and_bad_officer_is_422() -> None:
    missing = client.post(
        "/api/loan-officers/assignments",
        json={"borrower_id": "B-ZZZZZZZZZZZZZ", "loan_officer_id": LO_01},
    )
    assert missing.status_code == 404

    bad_officer = client.post(
        "/api/loan-officers/assignments",
        json={
            "borrower_id": _borrower_id(),
            "loan_officer_id": "99999999-9999-4999-8999-999999999999",
        },
    )
    assert bad_officer.status_code == 422

    malformed = client.post(
        "/api/loan-officers/assignments",
        json={"borrower_id": "not-a-borrower", "loan_officer_id": LO_01},
    )
    assert malformed.status_code == 422


def test_status_transitions_walk_lifecycle_and_reject_illegal_jumps() -> None:
    assignment_id = _assign(_borrower_id())["assignment"]["assignment_id"]

    illegal = client.patch(
        f"/api/loan-officers/assignments/{assignment_id}/status",
        json={"status": "actioned"},
    )
    assert illegal.status_code == 409

    for status in ("contact_drafted", "approved", "actioned", "outcome_recorded"):
        response = client.patch(
            f"/api/loan-officers/assignments/{assignment_id}/status",
            json={"status": status},
        )
        assert response.status_code == 200, response.text
        assert response.json()["assignment"]["status"] == status
        assert response.json()["audit_event_id"]

    terminal = client.patch(
        f"/api/loan-officers/assignments/{assignment_id}/status",
        json={"status": "assigned"},
    )
    assert terminal.status_code == 409

    unknown = client.patch(
        f"/api/loan-officers/assignments/{uuid4()}/status",
        json={"status": "contact_drafted"},
    )
    assert unknown.status_code == 404


def test_list_assignments_filters_by_officer_borrower_and_status() -> None:
    borrower_id = _borrower_id()
    assignment = _assign(borrower_id, LO_02)["assignment"]

    by_officer = client.get(f"/api/loan-officers/assignments?loan_officer_id={LO_02}")
    assert by_officer.status_code == 200
    assert [a["assignment_id"] for a in by_officer.json()] == [assignment["assignment_id"]]

    by_borrower = client.get(f"/api/loan-officers/assignments?borrower_id={borrower_id}")
    assert by_borrower.status_code == 200
    assert [a["borrower_id"] for a in by_borrower.json()] == [borrower_id]

    by_status = client.get("/api/loan-officers/assignments?status=outcome_recorded")
    assert by_status.status_code == 200
    assert by_status.json() == []

    invalid_status = client.get("/api/loan-officers/assignments?status=worked")
    assert invalid_status.status_code == 422

    invalid_borrower = client.get("/api/loan-officers/assignments?borrower_id=raw-name")
    assert invalid_borrower.status_code == 422


def test_leads_endpoint_exposes_assignment_status_chip_field() -> None:
    borrower_id = _borrower_id()
    _assign(borrower_id)
    response = client.get("/api/leads?limit=50")
    assert response.status_code == 200
    row = next(lead for lead in response.json() if lead["borrower_id"] == borrower_id)
    assert row["assignment_status"] == "assigned"
    assert row["assigned_to_email"] == "lo01@summit.example"
