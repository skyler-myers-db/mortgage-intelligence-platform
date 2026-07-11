"""S6 assignment-outcome recording contracts.

Outcome recording is the terminal lifecycle write: one transaction advances
``actioned -> outcome_recorded``, inserts the outcome as a
``mip_app.feedback`` row (the existing feedback-table pattern), and appends
the ``LEAD_OUTCOME_RECORDED`` audit row. Rejected requests must write
NOTHING -- the fake Lakebase client's recorded state proves it.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.loan_officer import ASSIGNMENT_OUTCOMES
from tests.fixtures import mock_population as mock_data

client = TestClient(app)
client.headers.update({"X-Forwarded-Email": "skyler@entrada.ai"})

LO_01 = "55555555-5555-4555-8555-555555555501"


def _borrower_id() -> str:
    return mock_data.BORROWERS[0].borrower_id


def _assign(borrower_id: str | None = None) -> dict:
    response = client.post(
        "/api/loan-officers/assignments",
        json={
            "borrower_id": borrower_id or _borrower_id(),
            "loan_officer_id": LO_01,
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["assignment"]


def _advance_to(assignment_id: str, target: str) -> None:
    for status in ("contact_drafted", "approved", "actioned"):
        response = client.patch(
            f"/api/loan-officers/assignments/{assignment_id}/status",
            json={"status": status},
        )
        assert response.status_code == 200, response.text
        if status == target:
            return


def _record(assignment_id: str, outcome: str, request_id: str | None = None, **kwargs) -> object:
    payload: dict = {"outcome": outcome}
    if request_id is not None:
        payload["request_id"] = request_id
    headers = kwargs.pop("headers", None)
    return client.post(
        f"/api/loan-officers/assignments/{assignment_id}/outcome",
        json=payload,
        headers=headers,
    )


def test_outcome_vocabulary_is_the_contracted_trio() -> None:
    assert ASSIGNMENT_OUTCOMES == ("success", "no_response", "declined")


def test_record_outcome_advances_lifecycle_and_writes_feedback_and_audit(
    fake_lakebase_client,
) -> None:
    assignment = _assign()
    _advance_to(assignment["assignment_id"], "actioned")
    audit_before = len(fake_lakebase_client.audit_events)

    response = _record(assignment["assignment_id"], "success")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["assignment"]["status"] == "outcome_recorded"
    assert body["outcome"] == "success"
    assert body["feedback_id"]
    assert body["audit_event_id"]

    # Feedback row uses the existing feedback-table pattern.
    assert len(fake_lakebase_client.feedback) == 1
    feedback = fake_lakebase_client.feedback[0]
    assert feedback["event_type"] == "assignment_outcome_success"
    assert feedback["borrower_id"] == assignment["borrower_id"]
    assert str(feedback["assignment_id"]) == assignment["assignment_id"]
    # Audit row is written in the SAME transaction and linked back.
    assert str(feedback["audit_event_id"]) == body["audit_event_id"]
    new_events = fake_lakebase_client.audit_events[audit_before:]
    outcome_events = [e for e in new_events if e.get("event_type") == "LEAD_OUTCOME_RECORDED"]
    assert len(outcome_events) == 1


def test_outcome_before_actioned_is_409_and_writes_nothing(
    fake_lakebase_client,
) -> None:
    assignment = _assign()
    _advance_to(assignment["assignment_id"], "approved")
    audit_before = len(fake_lakebase_client.audit_events)

    response = _record(assignment["assignment_id"], "declined")
    assert response.status_code == 409

    # Rejection path writes nothing: no feedback, no audit, status unchanged.
    assert fake_lakebase_client.feedback == []
    assert len(fake_lakebase_client.audit_events) == audit_before
    row = next(
        r for r in fake_lakebase_client.assignments
        if str(r["assignment_id"]) == assignment["assignment_id"]
    )
    assert row["status"] == "approved"


def test_second_outcome_is_409_terminal(fake_lakebase_client) -> None:
    assignment = _assign()
    _advance_to(assignment["assignment_id"], "actioned")
    assert _record(assignment["assignment_id"], "success").status_code == 200

    response = _record(assignment["assignment_id"], "declined")
    assert response.status_code == 409
    assert len(fake_lakebase_client.feedback) == 1


def test_outcome_replay_with_same_request_id_is_idempotent(
    fake_lakebase_client,
) -> None:
    assignment = _assign()
    _advance_to(assignment["assignment_id"], "actioned")
    request_id = str(uuid4())

    first = _record(assignment["assignment_id"], "no_response", request_id)
    assert first.status_code == 200, first.text
    replay = _record(assignment["assignment_id"], "no_response", request_id)
    assert replay.status_code == 200, replay.text

    assert replay.json()["feedback_id"] == first.json()["feedback_id"]
    # The replay must not write a second feedback row or audit event.
    assert len(fake_lakebase_client.feedback) == 1
    assert replay.json()["audit_event_id"] is None


def test_request_id_reuse_for_a_different_outcome_is_403() -> None:
    assignment = _assign()
    _advance_to(assignment["assignment_id"], "actioned")
    request_id = str(uuid4())
    assert _record(assignment["assignment_id"], "success", request_id).status_code == 200

    other = _assign(mock_data.BORROWERS[1].borrower_id)
    _advance_to(other["assignment_id"], "actioned")
    conflict = _record(other["assignment_id"], "success", request_id)
    assert conflict.status_code == 403


def test_unknown_assignment_is_404_and_bad_outcome_is_422() -> None:
    missing = _record(str(uuid4()), "success")
    assert missing.status_code == 404

    assignment = _assign()
    _advance_to(assignment["assignment_id"], "actioned")
    bad = _record(assignment["assignment_id"], "won_the_loan")
    assert bad.status_code == 422


def test_actor_scope_only_owning_lo_or_manager_can_record() -> None:
    assignment = _assign()
    _advance_to(assignment["assignment_id"], "actioned")

    # Another LO (not the assignee) is out of scope.
    forbidden = _record(
        assignment["assignment_id"],
        "success",
        headers={"X-Forwarded-Email": "lo02@summit.example"},
    )
    assert forbidden.status_code == 403

    # The assigned LO records their own outcome.
    allowed = _record(
        assignment["assignment_id"],
        "success",
        headers={"X-Forwarded-Email": "lo01@summit.example"},
    )
    assert allowed.status_code == 200, allowed.text
