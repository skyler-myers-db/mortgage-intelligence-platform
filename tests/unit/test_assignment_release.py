"""Assignment and distribution release: the audited Undo (audit states-07 item 1).

POST /api/leads/{borrower_id}/assignment/release undoes one assignment and
POST /api/sales/distribute/release undoes one distribution run. Both are
compensating writes: the released row keeps its history (``released_at``,
never a DELETE), the replaced officer gets a fresh row back only while that
is still allowed, and every released borrower gets exactly one LEAD_UNASSIGN
on the borrower entity. A replay of the same request id returns the same
body with the ORIGINAL audit id and writes nothing.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from tests.fixtures import mock_population as mock_data
from tests.fixtures.reviewed_approval import draft_proof

ADMIN = "skyler@entrada.ai"
client = TestClient(app)
client.headers.update({"X-Forwarded-Email": ADMIN})

LEAD_UNASSIGN_KEYS = {
    "action",
    "borrower_id",
    "assignment_id",
    "assigned_to_email",
    "restored_assignment_id",
    "restored_to_email",
    "restore_skipped_reason",
}


@pytest.fixture(autouse=True)
def _restore_the_roster(fake_lakebase_client: Any) -> Iterator[None]:
    # The fake's roster is session-scoped (reset_for_test leaves it alone), so
    # a test that deactivates or re-parents an officer puts it back.
    roster = copy.deepcopy(fake_lakebase_client.sales_team)
    yield
    fake_lakebase_client.sales_team[:] = roster


def _bid(index: int) -> str:
    return mock_data.BORROWERS[index].borrower_id


def _approve(borrower_id: str) -> None:
    draft = client.post("/api/outreach/draft", json={"borrower_id": borrower_id, "channel": "email"})
    assert draft.status_code == 200, draft.text
    approved = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": borrower_id,
            "channel": "email",
            **draft_proof(draft.json()),
            "request_id": str(uuid4()),
        },
    )
    assert approved.status_code == 200, approved.text


def _assign(borrower_id: str, lo: str, *, actor: str = ADMIN) -> dict[str, Any]:
    response = client.post(
        f"/api/leads/{borrower_id}/assign",
        json={"assigned_to_email": lo, "strategy": "manual", "request_id": str(uuid4())},
        headers={"X-Forwarded-Email": actor},
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["assignment"])


def _release(
    borrower_id: str, assignment_id: str, *, request_id: str | None = None, actor: str = ADMIN
) -> Any:
    return client.post(
        f"/api/leads/{borrower_id}/assignment/release",
        json={"assignment_id": assignment_id, "request_id": request_id or str(uuid4())},
        headers={"X-Forwarded-Email": actor},
    )


def _unassign_rows(fake: Any) -> list[dict[str, Any]]:
    return [row for row in fake.audit_events if row.get("event_type") == "LEAD_UNASSIGN"]


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata")
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def _add_member(fake: Any, email: str, role: str, manager: str | None = None) -> None:
    fake.sales_team.append(
        {
            "email": email,
            "display_label": email.split("@")[0],
            "role": role,
            "manager_email": manager,
            "region": None,
            "capacity_per_day": 10,
            "active": True,
        }
    )


def _set_member(fake: Any, email: str, **changes: Any) -> None:
    for row in fake.sales_team:
        if row["email"] == email:
            row.update(changes)


# -- the single release -------------------------------------------------------


def test_release_with_no_previous_assignee_releases_and_restores_nothing(fake_lakebase_client: Any) -> None:
    borrower_id = _bid(0)
    _approve(borrower_id)
    assignment = _assign(borrower_id, "lo01@summit.example")
    rows_before = len(fake_lakebase_client.assignments)

    response = _release(borrower_id, assignment["assignment_id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["released"]["assignment_id"] == assignment["assignment_id"]
    assert body["released"]["released_at"] is not None
    assert body["restored"] is None
    assert body["restore_skipped_reason"] is None
    assert len(fake_lakebase_client.assignments) == rows_before  # released, never deleted
    [audit] = _unassign_rows(fake_lakebase_client)
    assert str(audit["audit_id"]) == body["audit_event_id"]
    assert audit["entity_type"] == "borrower" and audit["entity_id"] == borrower_id
    assert _metadata(audit) == {
        "action": "lead.unassign",
        "borrower_id": borrower_id,
        "assignment_id": assignment["assignment_id"],
        "assigned_to_email": "lo01@summit.example",
    }
    assert client.get(f"/api/leads/{borrower_id}/assignment").status_code == 404


def test_release_restores_the_officer_the_assignment_replaced(fake_lakebase_client: Any) -> None:
    borrower_id = _bid(1)
    _approve(borrower_id)
    first = _assign(borrower_id, "lo01@summit.example")
    second = _assign(borrower_id, "lo02@summit.example")

    response = _release(borrower_id, second["assignment_id"])

    assert response.status_code == 200, response.text
    body = response.json()
    restored = body["restored"]
    assert restored["assigned_to_email"] == "lo01@summit.example"
    assert restored["assigned_to_label"] == "Summit LO 01"
    assert restored["assignment_id"] not in {first["assignment_id"], second["assignment_id"]}
    assert body["restore_skipped_reason"] is None
    active = client.get(f"/api/leads/{borrower_id}/assignment").json()
    assert active["assignment_id"] == restored["assignment_id"]
    [audit] = _unassign_rows(fake_lakebase_client)
    metadata = _metadata(audit)
    assert set(metadata) <= LEAD_UNASSIGN_KEYS
    assert metadata["restored_assignment_id"] == restored["assignment_id"]
    assert metadata["restored_to_email"] == "lo01@summit.example"
    # The first row stays released: the restore is a NEW row, history intact.
    assert len(fake_lakebase_client.assignments) == 3


def test_a_replay_returns_the_same_body_and_the_original_audit_id(fake_lakebase_client: Any) -> None:
    borrower_id = _bid(2)
    _approve(borrower_id)
    _assign(borrower_id, "lo01@summit.example")
    second = _assign(borrower_id, "lo02@summit.example")
    request_id = str(uuid4())

    first = _release(borrower_id, second["assignment_id"], request_id=request_id)
    rows_after_first = len(fake_lakebase_client.assignments)
    replay = _release(borrower_id, second["assignment_id"], request_id=request_id)

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert len(_unassign_rows(fake_lakebase_client)) == 1
    assert len(fake_lakebase_client.assignments) == rows_after_first


def test_a_request_id_reused_for_another_assignment_is_a_conflict(fake_lakebase_client: Any) -> None:
    first_id, second_id = _bid(3), _bid(4)
    for borrower_id in (first_id, second_id):
        _approve(borrower_id)
    a = _assign(first_id, "lo01@summit.example")
    b = _assign(second_id, "lo01@summit.example")
    request_id = str(uuid4())
    assert _release(first_id, a["assignment_id"], request_id=request_id).status_code == 200

    reused = _release(second_id, b["assignment_id"], request_id=request_id)

    assert reused.status_code == 409
    assert reused.json()["reason"] == "request_id_conflict"
    assert len(_unassign_rows(fake_lakebase_client)) == 1


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("wrong_assignment", "assignment_not_active"),
        ("started", "assignment_started"),
        ("assignee_inactive", "assignee_inactive"),
    ],
)
def test_each_conflict_is_a_409_with_its_closed_reason_and_writes_nothing(
    fake_lakebase_client: Any, setup: str, reason: str
) -> None:
    borrower_id = _bid(5)
    _approve(borrower_id)
    assignment = _assign(borrower_id, "lo01@summit.example")
    target = assignment["assignment_id"]
    if setup == "wrong_assignment":
        target = str(uuid4())
    elif setup == "started":
        fake_lakebase_client.assignments[-1]["status"] = "contact_drafted"
    else:
        _set_member(fake_lakebase_client, "lo01@summit.example", active=False)
    snapshot = [dict(row) for row in fake_lakebase_client.assignments]

    response = _release(borrower_id, target)

    assert response.status_code == 409
    assert response.json()["reason"] == reason
    assert "detail" in response.json()
    assert fake_lakebase_client.assignments == snapshot
    assert _unassign_rows(fake_lakebase_client) == []


def test_validation_and_not_found(fake_lakebase_client: Any) -> None:
    borrower_id = _bid(6)
    good = {"assignment_id": str(uuid4()), "request_id": str(uuid4())}
    assert client.post("/api/leads/not-an-id/assignment/release", json=good).status_code == 422
    for bad in (
        {"assignment_id": "x", "request_id": str(uuid4())},
        {"assignment_id": str(uuid4())},
        {**good, "notes": "free text"},
        {**good, "request_id": "call me at 555-555-0100"},
    ):
        assert client.post(f"/api/leads/{borrower_id}/assignment/release", json=bad).status_code == 422
    # A well-formed masked id the repository does not hold is a 404, not a 422.
    assert client.post("/api/leads/B-ZZZZZZZZZZ999/assignment/release", json=good).status_code == 404
    assert _unassign_rows(fake_lakebase_client) == []


def test_a_loan_officer_and_an_out_of_scope_manager_are_refused(fake_lakebase_client: Any) -> None:
    borrower_id = _bid(7)
    _approve(borrower_id)
    assignment = _assign(borrower_id, "lo01@summit.example")
    _add_member(fake_lakebase_client, "mgr02@summit.example", "sales_manager")

    as_lo = _release(borrower_id, assignment["assignment_id"], actor="lo01@summit.example")
    other_manager = _release(borrower_id, assignment["assignment_id"], actor="mgr02@summit.example")

    assert as_lo.status_code == 403
    assert other_manager.status_code == 403
    assert _unassign_rows(fake_lakebase_client) == []
    assert _release(borrower_id, assignment["assignment_id"]).status_code == 200


# -- the restore matrix -------------------------------------------------------


def _replaced_pair(fake: Any, index: int, *, second_actor: str = ADMIN) -> tuple[str, dict[str, Any]]:
    borrower_id = _bid(index)
    _approve(borrower_id)
    _assign(borrower_id, "lo01@summit.example")
    return borrower_id, _assign(borrower_id, "lo02@summit.example", actor=second_actor)


def test_restore_skips_an_inactive_previous_officer(fake_lakebase_client: Any) -> None:
    borrower_id, second = _replaced_pair(fake_lakebase_client, 8)
    _set_member(fake_lakebase_client, "lo01@summit.example", active=False)
    body = _release(borrower_id, second["assignment_id"]).json()
    assert body["restored"] is None
    assert body["restore_skipped_reason"] == "previous_assignee_inactive"
    [audit] = _unassign_rows(fake_lakebase_client)
    assert _metadata(audit)["restore_skipped_reason"] == "previous_assignee_inactive"
    assert "restored_assignment_id" not in _metadata(audit)


def test_restore_skips_a_previous_officer_outside_the_actor_scope(fake_lakebase_client: Any) -> None:
    _add_member(fake_lakebase_client, "mgr02@summit.example", "sales_manager")
    _set_member(fake_lakebase_client, "lo02@summit.example", manager_email="mgr02@summit.example")
    borrower_id, second = _replaced_pair(fake_lakebase_client, 9, second_actor="mgr02@summit.example")
    body = _release(borrower_id, second["assignment_id"], actor="mgr02@summit.example").json()
    assert body["restored"] is None
    assert body["restore_skipped_reason"] == "previous_assignee_out_of_scope"


def test_restore_skips_a_lead_that_is_no_longer_approved(fake_lakebase_client: Any) -> None:
    borrower_id, second = _replaced_pair(fake_lakebase_client, 10)
    rejected = client.post(
        "/api/outreach/reject",
        json={
            "borrower_id": borrower_id,
            "channel": "email",
            "rationale_code": "fair_lending_review",
            "request_id": str(uuid4()),
        },
    )
    assert rejected.status_code == 200, rejected.text
    body = _release(borrower_id, second["assignment_id"]).json()
    assert body["restored"] is None
    assert body["restore_skipped_reason"] == "lead_not_approved"


def test_restore_skips_a_lead_that_is_no_longer_contactable(
    fake_lakebase_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api import sales as sales_api

    borrower_id, second = _replaced_pair(fake_lakebase_client, 11)
    monkeypatch.setattr(sales_api, "_contactability_refusal", lambda borrower: "lead is suppressed")
    body = _release(borrower_id, second["assignment_id"]).json()
    assert body["restored"] is None
    assert body["restore_skipped_reason"] == "lead_not_contactable"


# -- the distribution release -------------------------------------------------


def _distribute(borrower_ids: list[str], request_id: str, *, actor: str = ADMIN) -> dict[str, Any]:
    response = client.post(
        "/api/sales/distribute",
        json={
            "borrower_ids": borrower_ids,
            "lo_emails": ["lo01@summit.example", "lo02@summit.example"],
            "strategy": "round_robin",
            "expires_in_hours": 24,
            "request_id": request_id,
        },
        headers={"X-Forwarded-Email": actor},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _release_run(distribution_request_id: str, request_id: str, *, actor: str = ADMIN) -> Any:
    return client.post(
        "/api/sales/distribute/release",
        json={"distribution_request_id": distribution_request_id, "request_id": request_id},
        headers={"X-Forwarded-Email": actor},
    )


def test_a_distribution_release_undoes_only_that_run_and_counts_the_skips(fake_lakebase_client: Any) -> None:
    run = [_bid(12), _bid(13), _bid(14)]
    outside = _bid(15)
    for borrower_id in [*run, outside]:
        _approve(borrower_id)
    _assign(run[0], "lo02@summit.example")  # replaced by the run, restored by the Undo
    other = _assign(outside, "lo01@summit.example")
    run_id = str(uuid4())
    _distribute(run, run_id)
    by_borrower = {row["borrower_id"]: row for row in fake_lakebase_client.assignments if row.get("request_id") == run_id}
    by_borrower[run[1]]["status"] = "contact_drafted"  # started: skipped
    _assign(run[2], "lo01@summit.example")  # reassigned since: not active, skipped
    rows_before = len(fake_lakebase_client.assignments)

    response = _release_run(run_id, str(uuid4()))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["released_count"] == 1
    assert body["skipped_started_count"] == 1
    assert body["skipped_not_active_count"] == 1
    assert body["restored_count"] == 1
    assert body["restore_skipped_count"] == 0
    assert [row["borrower_id"] for row in body["released"]] == [run[0]]
    assert body["restored"][0]["assigned_to_email"] == "lo02@summit.example"
    audits = _unassign_rows(fake_lakebase_client)
    assert [str(row["audit_id"]) for row in audits] == body["audit_event_ids"]
    assert [(row["entity_type"], row["entity_id"]) for row in audits] == [("borrower", run[0])]
    assert len(fake_lakebase_client.assignments) == rows_before + 1  # one restore, nothing deleted
    assert client.get(f"/api/leads/{outside}/assignment").json()["assignment_id"] == other["assignment_id"]


def test_a_distribution_release_writes_one_row_per_borrower_and_replays(fake_lakebase_client: Any) -> None:
    run = [_bid(16), _bid(17)]
    for borrower_id in run:
        _approve(borrower_id)
    run_id = str(uuid4())
    _distribute(run, run_id)
    request_id = str(uuid4())

    first = _release_run(run_id, request_id)
    replay = _release_run(run_id, request_id)

    assert first.status_code == replay.status_code == 200
    assert first.json()["released_count"] == 2
    assert replay.json() == first.json()
    audits = _unassign_rows(fake_lakebase_client)
    assert sorted(row["entity_id"] for row in audits) == sorted(run)
    for row in audits:
        assert set(_metadata(row)) <= LEAD_UNASSIGN_KEYS
        assert _metadata(row)["borrower_id"] == row["entity_id"]


def test_only_the_actor_who_ran_a_distribution_can_undo_it(fake_lakebase_client: Any) -> None:
    run = [_bid(18)]
    _approve(run[0])
    run_id = str(uuid4())
    _distribute(run, run_id)
    _add_member(fake_lakebase_client, "ops@summit.example", "admin")
    snapshot = [dict(row) for row in fake_lakebase_client.assignments]

    stranger = _release_run(run_id, str(uuid4()), actor="ops@summit.example")
    as_lo = _release_run(run_id, str(uuid4()), actor="lo01@summit.example")

    assert stranger.status_code == 403
    assert as_lo.status_code == 403
    assert fake_lakebase_client.assignments == snapshot
    assert _unassign_rows(fake_lakebase_client) == []
    assert _release_run(str(uuid4()), str(uuid4())).status_code == 404
