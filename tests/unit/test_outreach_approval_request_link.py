"""The maker-checker link on approve and reject (flow-02, D-approval-flow-c item 10).

``approval_request_batch_id`` is optional on both decision requests. When it
is set, the router asks the approval-request service whether the borrower's
request is still open and was raised by someone else, AFTER both replay
lookups (so a retried linked decision returns its stored body) and BEFORE
contact eligibility and draft verification. Pinned here:

1. Unlinked intents and fallback ids are byte-identical to the ones the tree
   computed before the link existed (literals captured from 8343364e).
2. A linked intent carries the id, the replay matchers compare it, and the
   APPROVE / OUTREACH_REJECT audit row records it.
3. not_open and self answer 409 with their fixed copy and write nothing; a
   Lakebase outage is a 503; an unlinked decision never consults the service.
4. ``verify_decision_link`` itself, over the in-memory ledger model.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api import outreach as outreach_mod
from backend.config.settings import settings
from backend.main import app
from backend.schemas.offer import OutreachApproveRequest, OutreachRejectRequest
from backend.services.approval_requests import (
    ApprovalRequestLinkRefused,
    list_approval_requests,
    verify_decision_link,
)
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError
from backend.services.outreach_decision_commit import _derive_fallback_request_id
from backend.services.outreach_decision_intent import (
    _approval_decision_intent,
    _approve_intent_matches_payload,
    _reject_decision_intent,
    _reject_intent_matches_payload,
)
from tests.fixtures.approval_ledger_fake import FakeApprovalLedger
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)
OWNER = "skyler@entrada.ai"
HEADERS = {"X-Forwarded-Email": OWNER}
BORROWER = "B-48291"
BATCH = "44444444-4444-4444-8444-444444444444"
# BATCH is all digits, so its upper-case twin is itself; the case tests need letters.
LETTERED_BATCH = "4abc4def-4444-4a44-8bcd-44444444abcd"
ACTOR = "approver.one@summit.example"

# Captured from the pre-link tree (8343364e) by printing the canonical intent.
GOLDEN_REJECT_INTENT = (
    '{"action":"reject","actor":"approver.one@summit.example","borrower_id":"B-000000000A003",'
    '"campaign_id":null,"campaign_owner_email":null,"campaign_treatment_fingerprint":null,'
    '"channel":"email","evidence_ids":["ev-004"],"evidence_ids_supplied":true,"offer_code":"refi",'
    '"offer_code_supplied":true,"rationale":"Not a fit this quarter.","rationale_code":"low_intent",'
    '"variant_name":null}'
)
GOLDEN_REJECT_FALLBACK = "auto-30a2cf164babcac731bc1fbae2195fb1"
GOLDEN_REVIEWED_APPROVE_INTENT = (
    '{"action":"approve","actor":"approver.one@summit.example","assigned_to_email":null,'
    '"borrower_id":"B-000000000A001","bulk_id":null,"bulk_rationale":null,"campaign_id":null,'
    '"campaign_owner_email":null,"campaign_treatment_fingerprint":null,"channel":"email",'
    '"draft_body":"Hello. Reply STOP to opt out.",'
    '"draft_generation_id":"11111111-1111-4111-8111-111111111111",'
    '"draft_response_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"draft_source_refreshed_at":"2026-09-01T00:00:00+00:00",'
    '"draft_subject":"A governed mortgage review","evidence_ids":["ev-001"],'
    '"evidence_ids_supplied":true,"follow_up_in_days":null,"offer_code":"refi",'
    '"offer_code_supplied":true,"rationale":"Reviewed against current rules.",'
    '"review_mode":"individual","variant_name":null}'
)
GOLDEN_REVIEWED_APPROVE_FALLBACK = "auto-d4ea3f4206f60dd58d7e16f592a50f5f"


def _golden_approve(**updates: Any) -> OutreachApproveRequest:
    return OutreachApproveRequest(
        borrower_id="B-000000000A001",
        offer_code="refi",
        channel="email",
        evidence_ids=["ev-001"],
        rationale="Reviewed against current rules.",
        draft_body="Hello. Reply STOP to opt out.",
        draft_subject="A governed mortgage review",
        draft_generation_id="11111111-1111-4111-8111-111111111111",
        draft_response_hash="a" * 64,
        draft_source_refreshed_at="2026-09-01T00:00:00+00:00",
        review_mode="individual",
        **updates,
    )


def _golden_reject(**updates: Any) -> OutreachRejectRequest:
    return OutreachRejectRequest(
        borrower_id="B-000000000A003",
        offer_code="refi",
        channel="email",
        evidence_ids=["ev-004"],
        rationale_code="low_intent",
        rationale="Not a fit this quarter.",
        **updates,
    )


def _approve_intent(payload: OutreachApproveRequest) -> str:
    return _approval_decision_intent(
        payload,
        actor=ACTOR,
        offer_code="refi",
        evidence_ids=["ev-001"],
        safe_rationale="Reviewed against current rules.",
        safe_bulk_rationale=None,
        campaign_owner_email=None,
        campaign_treatment_fingerprint=None,
    )


def _reject_intent(payload: OutreachRejectRequest) -> str:
    return _reject_decision_intent(
        payload,
        actor=ACTOR,
        offer_code="refi",
        evidence_ids=["ev-004"],
        safe_rationale="Not a fit this quarter.",
        campaign_id=None,
        variant_name=None,
        campaign_owner_email=None,
        campaign_treatment_fingerprint=None,
    )


# -- 1-2. intents -------------------------------------------------------------------


def test_unlinked_intents_and_fallback_ids_are_byte_identical_to_before_the_link() -> None:
    approve = _approve_intent(_golden_approve())
    reject = _reject_intent(_golden_reject())
    assert approve == GOLDEN_REVIEWED_APPROVE_INTENT
    assert reject == GOLDEN_REJECT_INTENT
    assert _derive_fallback_request_id(actor=ACTOR, action="approve", decision_intent=approve) == (
        GOLDEN_REVIEWED_APPROVE_FALLBACK
    )
    assert _derive_fallback_request_id(actor=ACTOR, action="reject", decision_intent=reject) == (
        GOLDEN_REJECT_FALLBACK
    )


def test_a_linked_intent_carries_the_request_and_the_matchers_compare_it() -> None:
    linked_approve = _golden_approve(approval_request_batch_id=BATCH)
    linked_reject = _golden_reject(approval_request_batch_id=BATCH)
    approve = json.loads(_approve_intent(linked_approve))
    reject = json.loads(_reject_intent(linked_reject))
    assert approve["approval_request_batch_id"] == reject["approval_request_batch_id"] == BATCH
    matches_approve = {
        "actor": ACTOR,
        "safe_rationale": "Reviewed against current rules.",
        "safe_bulk_rationale": None,
    }
    assert _approve_intent_matches_payload(approve, payload=linked_approve, **matches_approve)
    assert not _approve_intent_matches_payload(approve, payload=_golden_approve(), **matches_approve)
    unlinked = json.loads(GOLDEN_REVIEWED_APPROVE_INTENT)
    assert not _approve_intent_matches_payload(unlinked, payload=linked_approve, **matches_approve)
    assert _reject_intent_matches_payload(
        reject, payload=linked_reject, actor=ACTOR, safe_rationale="Not a fit this quarter."
    )
    assert not _reject_intent_matches_payload(
        reject, payload=_golden_reject(), actor=ACTOR, safe_rationale="Not a fit this quarter."
    )


def test_a_linked_intent_built_from_an_upper_case_id_carries_the_ledger_spelling() -> None:
    # The pattern admits either case; the ledger matches the link as TEXT
    # against batch_id::text (lower-case), so the schema canonicalizes it.
    assert LETTERED_BATCH.upper() != LETTERED_BATCH
    upper_approve = _golden_approve(approval_request_batch_id=LETTERED_BATCH.upper())
    upper_reject = _golden_reject(approval_request_batch_id=LETTERED_BATCH.upper())
    assert upper_approve.approval_request_batch_id == upper_reject.approval_request_batch_id == LETTERED_BATCH
    assert _approve_intent(upper_approve) == _approve_intent(_golden_approve(approval_request_batch_id=LETTERED_BATCH))
    assert _reject_intent(upper_reject) == _reject_intent(_golden_reject(approval_request_batch_id=LETTERED_BATCH))
    assert f'"approval_request_batch_id":"{LETTERED_BATCH}"' in _approve_intent(upper_approve)


def test_a_link_never_rides_a_campaign_binding() -> None:
    with pytest.raises(ValueError, match="campaign binding"):
        _golden_reject(
            approval_request_batch_id=BATCH,
            campaign_id="11111111-1111-4111-8111-111111111111",
            variant_name="Benefit-led",
        )
    with pytest.raises(ValueError):
        _golden_reject(approval_request_batch_id="not-a-uuid")


# -- 3. the router -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _approver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "approver_identities", OWNER)
    monkeypatch.setattr(settings, "trust_forwarded_headers", True)


@pytest.fixture
def audit() -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    saved = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        yield store
    finally:
        if saved is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = saved


class _Link:
    """Stands in for the approval-request service at the router boundary."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.refuse: str | None = None
        self.down = False

    def __call__(self, lakebase: object, *, batch_id: str, borrower_id: str, actor: str) -> None:
        self.calls.append({"batch_id": batch_id, "borrower_id": borrower_id, "actor": actor})
        if self.down:
            raise LakebaseError("down")
        if self.refuse is not None:
            raise ApprovalRequestLinkRefused(self.refuse)  # type: ignore[arg-type]


@pytest.fixture
def link(monkeypatch: pytest.MonkeyPatch) -> _Link:
    stub = _Link()
    monkeypatch.setattr(outreach_mod, "verify_decision_link", stub)
    return stub


def _draft() -> dict[str, Any]:
    response = client.post(
        "/api/outreach/draft", json={"borrower_id": BORROWER, "channel": "email"}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approval(**updates: Any) -> dict[str, Any]:
    draft = _draft()
    payload = {
        "borrower_id": BORROWER,
        "channel": "email",
        "offer_code": draft["offer_code"],
        "draft_subject": draft["subject"],
        "draft_body": draft["body"],
        "draft_generation_id": draft["generation_id"],
        "draft_response_hash": draft["response_hash"],
        "draft_source_refreshed_at": draft["source_refreshed_at"],
        "review_mode": "individual",
    }
    payload.update(updates)
    return payload


def _rows(audit: InMemoryAuditStore, event_type: str) -> list[dict[str, Any]]:
    return [dict(event.payload_json or {}) for event in audit.list(limit=500) if event.event_type == event_type]


def _decision_inserts(lakebase: Any) -> list[dict[str, Any]]:
    return [params for sql, params in lakebase.executes if "INSERT INTO mip_app.approvals" in sql]


def test_a_linked_approve_is_verified_and_the_audit_row_records_the_request(
    link: _Link, audit: InMemoryAuditStore, fake_lakebase_client: Any
) -> None:
    response = client.post(
        "/api/outreach/approve", json=_approval(approval_request_batch_id=BATCH), headers=HEADERS
    )
    assert response.status_code == 200, response.text
    assert link.calls == [{"batch_id": BATCH, "borrower_id": BORROWER, "actor": OWNER}]
    (row,) = _rows(audit, "APPROVE")
    assert row["approval_request_batch_id"] == BATCH
    (insert,) = _decision_inserts(fake_lakebase_client)
    assert json.loads(insert["decision_intent"])["approval_request_batch_id"] == BATCH


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_an_upper_case_link_is_verified_and_recorded_in_the_ledger_spelling(
    link: _Link, audit: InMemoryAuditStore, fake_lakebase_client: Any, verb: str
) -> None:
    body = (
        _approval(approval_request_batch_id=LETTERED_BATCH.upper())
        if verb == "approve"
        else {"borrower_id": BORROWER, "rationale_code": "low_intent", "approval_request_batch_id": LETTERED_BATCH.upper()}
    )
    assert LETTERED_BATCH.upper() != LETTERED_BATCH
    response = client.post(f"/api/outreach/{verb}", json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert link.calls == [{"batch_id": LETTERED_BATCH, "borrower_id": BORROWER, "actor": OWNER}]
    (row,) = _rows(audit, "APPROVE" if verb == "approve" else "OUTREACH_REJECT")
    assert row["approval_request_batch_id"] == LETTERED_BATCH
    (insert,) = _decision_inserts(fake_lakebase_client)
    assert json.loads(insert["decision_intent"])["approval_request_batch_id"] == LETTERED_BATCH


def test_an_unlinked_decision_never_consults_the_request_service(
    link: _Link, audit: InMemoryAuditStore
) -> None:
    response = client.post("/api/outreach/approve", json=_approval(), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert link.calls == []
    (row,) = _rows(audit, "APPROVE")
    assert "approval_request_batch_id" not in row


@pytest.mark.parametrize(
    ("kind", "detail"),
    [
        (
            "not_open",
            "This approval request is no longer open for this borrower; clear the request scope to decide directly.",
        ),
        ("self", "You raised this approval request; another approver must decide it."),
    ],
)
@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_a_refused_link_answers_409_and_writes_nothing(
    link: _Link, audit: InMemoryAuditStore, fake_lakebase_client: Any, kind: str, detail: str, verb: str
) -> None:
    link.refuse = kind
    body = (
        _approval(approval_request_batch_id=BATCH)
        if verb == "approve"
        else {"borrower_id": BORROWER, "rationale_code": "low_intent", "approval_request_batch_id": BATCH}
    )
    response = client.post(f"/api/outreach/{verb}", json=body, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"] == detail
    assert _decision_inserts(fake_lakebase_client) == []
    assert _rows(audit, "APPROVE") == [] and _rows(audit, "OUTREACH_REJECT") == []


def test_a_linked_reject_records_the_request(link: _Link, audit: InMemoryAuditStore) -> None:
    response = client.post(
        "/api/outreach/reject",
        json={"borrower_id": BORROWER, "rationale_code": "low_intent", "approval_request_batch_id": BATCH},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    (row,) = _rows(audit, "OUTREACH_REJECT")
    assert row["approval_request_batch_id"] == BATCH


def test_a_lakebase_outage_during_the_link_check_is_a_503(link: _Link) -> None:
    link.down = True
    response = client.post(
        "/api/outreach/approve", json=_approval(approval_request_batch_id=BATCH), headers=HEADERS
    )
    assert response.status_code == 503


def _serve_persisted_approvals(monkeypatch: pytest.MonkeyPatch, lakebase: Any) -> None:
    original = lakebase.fetchone

    def _fetchone(sql: str, params: dict[str, Any] | None = None) -> Any:
        if "FROM mip_app.approvals" in sql and "request_id" in sql:
            for row in lakebase.approvals:
                if row.get("request_id") == (params or {}).get("request_id"):
                    return dict(row)
            return None
        return original(sql, params)

    monkeypatch.setattr(lakebase, "fetchone", _fetchone)


def test_a_linked_retry_returns_its_stored_body_after_the_request_closed(
    link: _Link, audit: InMemoryAuditStore, fake_lakebase_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_persisted_approvals(monkeypatch, fake_lakebase_client)
    payload = _approval(approval_request_batch_id=BATCH, request_id=str(uuid4()))
    first = client.post("/api/outreach/approve", json=payload, headers=HEADERS)
    assert first.status_code == 200, first.text
    # The decision itself closed the request; a retry must still replay.
    link.refuse = "not_open"
    again = client.post("/api/outreach/approve", json=payload, headers=HEADERS)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert len(link.calls) == 1
    assert len(_rows(audit, "APPROVE")) == 1


# -- 4. verify_decision_link over the ledger model ----------------------------------------

ALICE = "alice.analyst@summit.example"
OPEN_ID, CLOSED_ID = "B-ARQTESTX00001", "B-ARQTESTX00002"


@pytest.fixture
def ledger() -> FakeApprovalLedger:
    return FakeApprovalLedger()


def test_an_open_request_raised_by_someone_else_passes(ledger: FakeApprovalLedger) -> None:
    batch_id = ledger.add_batch(ALICE, [OPEN_ID])
    verify_decision_link(ledger, batch_id=batch_id, borrower_id=OPEN_ID, actor=ACTOR)  # type: ignore[arg-type]


def test_the_requester_cannot_decide_their_own_request(ledger: FakeApprovalLedger) -> None:
    batch_id = ledger.add_batch(ALICE, [OPEN_ID])
    with pytest.raises(ApprovalRequestLinkRefused) as refused:
        verify_decision_link(ledger, batch_id=batch_id, borrower_id=OPEN_ID, actor=" Alice.Analyst@Summit.example ")  # type: ignore[arg-type]
    assert refused.value.kind == "self"


def test_a_decision_linked_with_an_upper_case_id_reads_approved_not_decided_outside(
    ledger: FakeApprovalLedger,
) -> None:
    batch_id = ledger.add_batch(ALICE, [OPEN_ID, CLOSED_ID])
    assert batch_id.upper() != batch_id
    linked = _golden_approve(approval_request_batch_id=batch_id.upper()).model_copy(
        update={"borrower_id": OPEN_ID}
    )
    verify_decision_link(
        ledger,  # type: ignore[arg-type]
        batch_id=str(linked.approval_request_batch_id),
        borrower_id=OPEN_ID,
        actor=ACTOR,
    )
    approval_id = ledger.add_decision(OPEN_ID, "approve", decision_intent=_approve_intent(linked))
    [view] = list_approval_requests(ledger, actor=ALICE, scope="mine").batches  # type: ignore[arg-type]
    assert {row.borrower_id: (row.state, row.approval_id) for row in view.rows} == {
        OPEN_ID: ("approved", approval_id),
        CLOSED_ID: ("open", None),
    }


@pytest.mark.parametrize("closure", ["decided", "withdrawn", "expired", "stale", "absent", "unknown"])
def test_a_request_no_longer_open_for_the_borrower_is_refused(
    ledger: FakeApprovalLedger, closure: str
) -> None:
    batch_id = ledger.add_batch(ALICE, [OPEN_ID, CLOSED_ID])
    borrower_id = CLOSED_ID
    if closure == "decided":
        ledger.add_decision(CLOSED_ID, "approve", batch_id=batch_id)
    elif closure in {"withdrawn", "expired"}:
        ledger.items[(batch_id, CLOSED_ID)].update(status=closure, closed_at=ledger.now)
    elif closure == "stale":
        ledger.batches[batch_id]["created_at"] = ledger.now - timedelta(days=31)
    elif closure == "absent":
        borrower_id = "B-ARQTESTX00009"
    else:
        batch_id = str(uuid4())
    with pytest.raises(ApprovalRequestLinkRefused) as refused:
        verify_decision_link(ledger, batch_id=batch_id, borrower_id=borrower_id, actor=ACTOR)  # type: ignore[arg-type]
    assert refused.value.kind == "not_open"
