"""Maker-checker approval requests over HTTP (flow-02 / shell-06, 12.4 #10).

Routes: GET and POST /api/outreach/approval-requests and
POST /api/outreach/approval-requests/{batch_id}/withdraw (canonical twins
under /api/v1). The Lakebase tables are the in-memory model in
tests/fixtures/approval_ledger_fake.py; the gold read is a tiny fake
repository. The real SQL is pinned by the Postgres integration suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.lakebase import get_lakebase_client
from backend.services.repositories import get_lead_repository
from tests.fixtures.approval_ledger_fake import FakeApprovalLedger

ALICE = "alice.analyst@summit.example"
BOB = "bob.analyst@summit.example"
PAT = "pat.approver@summit.example"
REQUESTER = {"X-Forwarded-Email": ALICE, "X-Forwarded-Groups": ""}
OTHER_REQUESTER = {"X-Forwarded-Email": BOB, "X-Forwarded-Groups": ""}
APPROVER = {"X-Forwarded-Email": PAT, "X-Forwarded-Groups": "mip-admin"}
NOTE = "Rate-sensitive refinance candidates in our footprint."
OK_1, OK_2 = "B-ARQTESTX00001", "B-ARQTESTX00002"
DNC, LAKEBASE_APPROVED, MISSING = "B-ARQTESTX00003", "B-ARQTESTX00004", "B-ARQTESTX00009"
URL = "/api/outreach/approval-requests"


class _Leads:
    """The gold borrower-id read: contactability and the lagging approval_status."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        self.calls: list[dict[str, Any]] = []
        for borrower_id in (OK_1, OK_2, DNC, LAKEBASE_APPROVED, "B-ARQTESTX00005", "B-ARQTESTX00006"):
            self.add(borrower_id)
        self.rows[DNC].dnc = True

    def add(self, borrower_id: str, **fields: Any) -> None:
        self.rows[borrower_id] = SimpleNamespace(
            borrower_id=borrower_id,
            marketing_eligible=True,
            dnc=False,
            consent_status="opt_in",
            suppression_reason=None,
            approval_status="pending",
            **fields,
        )

    def list(self, segment: str | None, portfolio_id: str | None, limit: int | None = None, **kwargs: Any) -> list[Any]:
        self.calls.append({"segment": segment, "portfolio_id": portfolio_id, "limit": limit, **kwargs})
        return [self.rows[bid] for bid in kwargs.get("borrower_ids") or [] if bid in self.rows]


@pytest.fixture
def ledger() -> FakeApprovalLedger:
    return FakeApprovalLedger()


@pytest.fixture
def leads() -> _Leads:
    return _Leads()


@pytest.fixture
def client(ledger: FakeApprovalLedger, leads: _Leads) -> Iterator[TestClient]:
    app.dependency_overrides[get_lakebase_client] = lambda: ledger
    app.dependency_overrides[get_lead_repository] = lambda: leads
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_lakebase_client, None)
        app.dependency_overrides.pop(get_lead_repository, None)


def _body(ids: list[str], *, key: str | None = None, note: str = NOTE) -> dict[str, Any]:
    return {"borrower_ids": ids, "rationale": note, "request_key": key or str(uuid4())}


def _create(client: TestClient, ids: list[str], **kwargs: Any) -> Any:
    return client.post(URL, json=_body(ids, **kwargs), headers=REQUESTER)


# -- create -----------------------------------------------------------------


def test_an_approver_is_told_to_decide_directly_with_nothing_read_or_written(
    client: TestClient, ledger: FakeApprovalLedger, leads: _Leads
) -> None:
    response = client.post(URL, json=_body([OK_1]), headers=APPROVER)
    assert response.status_code == 409
    assert response.json()["detail"] == "Approvers decide directly; open the review instead."
    assert ledger.statements == [] and leads.calls == []


def test_five_ids_hold_two_and_one_audit_row_records_every_skip(
    client: TestClient, ledger: FakeApprovalLedger, leads: _Leads
) -> None:
    # Gold still reads pending; Lakebase already has the finalized approve.
    ledger.add_decision(LAKEBASE_APPROVED, "approve")
    response = _create(client, [OK_1, MISSING, DNC, LAKEBASE_APPROVED, OK_2])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested"] == [OK_1, OK_2]
    assert body["skipped"] == [
        {"borrower_id": MISSING, "reason": "not_found"},
        {"borrower_id": DNC, "reason": "not_contactable"},
        {"borrower_id": LAKEBASE_APPROVED, "reason": "already_decided"},
    ]
    # ONE gold read by id with no portfolio criteria (no default filters).
    assert leads.calls == [
        {"segment": None, "portfolio_id": None, "limit": 5,
         "borrower_ids": [OK_1, MISSING, DNC, LAKEBASE_APPROVED, OK_2]}
    ]
    [audit] = ledger.audits
    assert audit["event_type"] == "APPROVAL_REQUESTED"
    assert audit["entity_type"] == "approval_request_batch"
    assert audit["entity_id"] == body["batch_id"] == audit["metadata"]["approval_request_batch_id"]
    assert audit["metadata"]["borrower_ids"] == [OK_1, OK_2]
    assert (audit["metadata"]["requested_count"], audit["metadata"]["skipped_count"]) == (2, 3)
    assert audit["metadata"]["skipped_by_reason"] == {
        "not_found": [MISSING],
        "not_contactable": [DNC],
        "already_decided": [LAKEBASE_APPROVED],
    }
    assert audit["metadata"]["rationale"] == NOTE
    assert audit["audit_id"] == body["audit_event_id"]
    batch = ledger.batches[body["batch_id"]]
    assert batch["requested_by"] == ALICE and batch["audit_event_id"] == body["audit_event_id"]
    assert batch["response"] == body
    assert {key[1] for key in ledger.items} == {OK_1, OK_2}
    # Expire, batch, items, audit and finalize ran in the one transaction.
    assert ledger.statements[-5:] == [
        "EXPIRE_STALE_OPEN_ITEMS", "INSERT_BATCH", "INSERT_OPEN_ITEMS", "INSERT_AUDIT", "FINALIZE_BATCH",
    ]


@pytest.mark.parametrize(
    "field", ["marketing_eligible", "consent_status", "suppression_reason"]
)
def test_every_contactability_rule_skips_the_borrower(
    client: TestClient, leads: _Leads, field: str
) -> None:
    value: object = {"marketing_eligible": False, "consent_status": "opt_out", "suppression_reason": "dnc_list"}[field]
    setattr(leads.rows[OK_2], field, value)
    body = _create(client, [OK_1, OK_2]).json()
    assert body["skipped"] == [{"borrower_id": OK_2, "reason": "not_contactable"}]


def test_gold_decides_only_when_lakebase_has_no_row_and_a_latest_revoke_is_requestable(
    client: TestClient, ledger: FakeApprovalLedger, leads: _Leads
) -> None:
    leads.rows[OK_1].approval_status = "rejected"
    # Gold lags: it still reads approved, but the latest Lakebase row revoked it.
    leads.rows[OK_2].approval_status = "approved"
    ledger.add_decision(OK_2, "approve", decided_at=ledger.now - timedelta(hours=2))
    ledger.add_decision(OK_2, "revoke", decided_at=ledger.now - timedelta(hours=1))
    body = _create(client, [OK_1, OK_2]).json()
    assert body["requested"] == [OK_2]
    assert body["skipped"] == [{"borrower_id": OK_1, "reason": "already_decided"}]


def test_zero_eligible_answers_counts_only_and_audits_the_refusal(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    ledger.add_decision(OK_1, "reject")
    key = str(uuid4())
    response = _create(client, [OK_1, DNC, MISSING], key=key)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"] == "No selected borrower can be requested."
    assert detail["skipped_counts"] == {
        "not_found": 1, "not_contactable": 1, "already_decided": 1, "already_requested": 0,
    }
    # No per-borrower attribute leaves without the audit row.
    for borrower_id in (OK_1, DNC, MISSING):
        assert borrower_id not in response.text
    assert ledger.batches == {} and ledger.items == {}
    [audit] = ledger.audits
    assert audit["event_type"] == "APPROVAL_REQUEST_REFUSED"
    assert (audit["entity_type"], audit["entity_id"], audit["request_id"]) == ("approval_request", key, key)
    assert audit["metadata"]["borrower_ids"] == [OK_1, DNC, MISSING]
    assert audit["metadata"]["skipped_by_reason"] == {
        "already_decided": [OK_1], "not_contactable": [DNC], "not_found": [MISSING],
    }
    assert detail["audit_event_id"] == audit["audit_id"]


def test_a_concurrently_held_borrower_is_reclassified_already_requested(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    def _race(_ids: list[str]) -> None:
        ledger.items[(ledger.add_batch(BOB, []), OK_2)] = {"status": "open", "closed_at": None}

    ledger.before_insert_items = _race
    body = _create(client, [OK_1, OK_2]).json()
    assert body["requested"] == [OK_1]
    assert body["skipped"] == [{"borrower_id": OK_2, "reason": "already_requested"}]


def test_when_every_candidate_is_taken_the_attempt_rolls_back_and_is_still_audited(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    other = ledger.add_batch(BOB, [OK_1])
    ledger.before_insert_items = lambda ids: ledger.items.setdefault((other, OK_2), {"status": "open", "closed_at": None})
    response = _create(client, [OK_1, OK_2])
    assert response.status_code == 409
    assert response.json()["detail"]["skipped_counts"]["already_requested"] == 2
    assert set(ledger.batches) == {other}
    assert ledger.audit_types() == ["APPROVAL_REQUEST_REFUSED"]


@pytest.mark.parametrize(
    ("note", "status"),
    [
        ("Please ask Jane Smith to call them", 422),
        ("Prioritize borrowers who are not Hispanic", 422),
    ],
    ids=["name-shaped", "protected-class"],
)
def test_a_refused_note_answers_422_before_any_read_or_write(
    client: TestClient, ledger: FakeApprovalLedger, leads: _Leads, note: str, status: int
) -> None:
    response = _create(client, [OK_1], note=note)
    assert response.status_code == status
    assert note not in response.text
    assert ledger.statements == [] and leads.calls == [] and ledger.writes() == 0


def test_a_note_that_redaction_pushes_past_the_cap_is_422_not_a_503(
    client: TestClient, ledger: FakeApprovalLedger, leads: _Leads
) -> None:
    # 500 characters on the wire; the email becomes [EMAIL-REDACTED] (longer),
    # so the stored note would break the batch's length CHECK inside the commit.
    note = ("Ping a@b.io first. " + "Rate-sensitive refinance candidates. " * 14)[:500]
    assert len(note) == 500
    response = _create(client, [OK_1], note=note)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == (
        "rationale is too long once personal details are redacted; shorten it and leave them out"
    )
    assert ledger.statements == [] and leads.calls == [] and ledger.writes() == 0
    # The same note with the address removed fits once redacted, and is held.
    fits = note.replace("Ping a@b.io first. ", "Review first, then ")
    assert len(fits) == 500
    assert _create(client, [OK_1], note=fits).status_code == 200


def test_a_replayed_key_returns_the_stored_body_and_a_changed_payload_conflicts(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    key = str(uuid4())
    first = _create(client, [OK_1, OK_2], key=key)
    writes = ledger.writes()
    again = _create(client, [OK_2, OK_1], key=key)
    assert again.status_code == 200 and again.json() == first.json()
    assert ledger.writes() == writes
    changed = _create(client, [OK_1], key=key)
    assert changed.status_code == 409
    assert changed.json()["detail"] == "request_key already belongs to a different approval request"
    assert ledger.writes() == writes


def test_a_key_taken_between_the_lookup_and_the_insert_replays_the_winner(
    client: TestClient, ledger: FakeApprovalLedger, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = str(uuid4())
    winner = _create(client, [OK_1], key=key).json()
    writes = ledger.writes()
    original = ledger._sql_batch_by_key
    lookups: list[int] = []

    def _not_yet_committed_on_first_lookup(params: dict[str, Any]) -> list[dict[str, Any]]:
        lookups.append(1)
        return [] if len(lookups) == 1 else original(params)

    monkeypatch.setattr(ledger, "_sql_batch_by_key", _not_yet_committed_on_first_lookup)
    assert _create(client, [OK_1], key=key).json() == winner
    assert ledger.statements.count("INSERT_BATCH") == 2  # the loser's insert ran and rolled back
    assert ledger.writes() == writes


def test_lakebase_down_is_a_503(client: TestClient, ledger: FakeApprovalLedger) -> None:
    ledger.down = True
    response = _create(client, [OK_1])
    assert response.status_code == 503


# -- list -------------------------------------------------------------------


def test_list_scope_defaults_by_role_and_a_non_approver_cannot_list_open(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    _create(client, [OK_1])
    assert client.get(URL, headers=REQUESTER).json()["scope"] == "mine"
    assert client.get(URL, headers=APPROVER).json()["scope"] == "open"
    refused = client.get(URL, params={"scope": "open"}, headers=REQUESTER)
    assert refused.status_code == 403
    # Listing is audit-free.
    assert ledger.audit_types() == ["APPROVAL_REQUESTED"]


def test_approvers_never_receive_the_requester_identity(client: TestClient) -> None:
    _create(client, [OK_1])
    response = client.get(URL, headers=APPROVER)
    [batch] = response.json()["batches"]
    assert batch["requested_by"] is None
    assert batch["requested_by_display"] == "Alice Analyst"
    assert batch["is_mine"] is False
    assert ALICE not in response.text
    mine = client.get(URL, headers=REQUESTER).json()["batches"]
    assert mine[0]["requested_by"] == ALICE and mine[0]["is_mine"] is True


def test_derived_states_read_back_through_the_list(client: TestClient, ledger: FakeApprovalLedger) -> None:
    ids = [OK_1, OK_2, "B-ARQTESTX00005", "B-ARQTESTX00006"]
    batch_id = ledger.add_batch(ALICE, ids, created_at=ledger.now - timedelta(days=1))
    linked = ledger.add_decision(OK_1, "approve", batch_id=batch_id)
    ledger.add_decision(OK_2, "approve")  # later, without the link
    ledger.items[(batch_id, "B-ARQTESTX00005")].update(status="withdrawn", closed_at=ledger.now)
    stale = ledger.add_batch(ALICE, ["B-ARQTESTX00007"], created_at=ledger.now - timedelta(days=31))
    [view] = client.get(URL, headers=REQUESTER).json()["batches"]
    assert view["batch_id"] == batch_id and stale not in {view["batch_id"]}
    assert {row["borrower_id"]: (row["state"], row["approval_id"]) for row in view["rows"]} == {
        OK_1: ("approved", linked),
        OK_2: ("decided_outside", None),
        "B-ARQTESTX00005": ("withdrawn", None),
        "B-ARQTESTX00006": ("open", None),
    }
    ledger.now += timedelta(days=30)
    expired_view = client.get(URL, params={"scope": "mine"}, headers=REQUESTER).json()
    assert expired_view["batches"] == []  # older than the 30-day window


def test_the_open_scope_hides_requests_with_no_open_borrower(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    decided = ledger.add_batch(ALICE, [OK_1])
    ledger.add_decision(OK_1, "reject", batch_id=decided)
    waiting = ledger.add_batch(BOB, [OK_2])
    batches = client.get(URL, headers=APPROVER).json()["batches"]
    assert [batch["batch_id"] for batch in batches] == [waiting]


# -- withdraw -----------------------------------------------------------------


def test_only_the_requester_withdraws_and_a_repeat_writes_nothing(
    client: TestClient, ledger: FakeApprovalLedger
) -> None:
    batch_id = _create(client, [OK_1, OK_2]).json()["batch_id"]
    ledger.add_decision(OK_2, "approve", batch_id=batch_id)
    path = f"{URL}/{batch_id}/withdraw"
    assert client.post(path, json={}, headers=OTHER_REQUESTER).status_code == 403
    assert client.post(path, json={}, headers=APPROVER).status_code == 403
    first = client.post(path, json={}, headers=REQUESTER)
    assert first.status_code == 200
    assert first.json()["withdrawn_now"] == 1 and first.json()["already_closed"] == 1
    assert ledger.items[(batch_id, OK_1)]["status"] == "withdrawn"
    assert ledger.items[(batch_id, OK_2)]["status"] == "open"  # decided: derived, not withdrawn
    withdrawn = ledger.audits[-1]
    assert withdrawn["event_type"] == "APPROVAL_REQUEST_WITHDRAWN"
    assert {key: withdrawn["metadata"][key] for key in ("approval_request_batch_id", "withdrawn_count", "borrower_ids")} == {
        "approval_request_batch_id": batch_id, "withdrawn_count": 1, "borrower_ids": [OK_1],
    }
    assert (withdrawn["entity_type"], withdrawn["entity_id"]) == ("approval_request_batch", batch_id)
    again = client.post(path, json={}, headers=REQUESTER).json()
    assert (again["withdrawn_now"], again["audit_event_id"]) == (0, None)
    assert ledger.audit_types().count("APPROVAL_REQUEST_WITHDRAWN") == 1


def test_withdrawing_an_unknown_request_is_404(client: TestClient) -> None:
    response = client.post(f"{URL}/{uuid4()}/withdraw", json={}, headers=REQUESTER)
    assert response.status_code == 404


def test_withdraw_needs_a_json_body_and_a_uuid(client: TestClient) -> None:
    assert client.post(f"{URL}/not-a-uuid/withdraw", json={}, headers=REQUESTER).status_code == 422
    plain = client.post(f"{URL}/{uuid4()}/withdraw", content="x", headers=REQUESTER)
    assert plain.status_code == 415


def test_the_canonical_v1_routes_serve_the_same_contract(client: TestClient) -> None:
    created = client.post("/api/v1/outreach/approval-requests", json=_body([OK_1]), headers=REQUESTER)
    assert created.status_code == 200
    listed = client.get("/api/v1/outreach/approval-requests", headers=REQUESTER)
    assert listed.json()["batches"][0]["batch_id"] == created.json()["batch_id"]
