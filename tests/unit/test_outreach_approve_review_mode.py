"""The APPROVE row's review ledger (audit flow-03 / states-06 / wow-power-1,
D-approval-flow-a1).

``POST /api/outreach/approve`` records HOW the approver saw the copy the
approval certifies: ``review_mode`` is ``individual`` or ``triage`` (the copy
was on screen for this borrower), ``bulk_sample`` (a bulk run's previewed
sample) or ``bulk_cohort`` (approved under the run's shared rationale, never
individually shown). Since W5c the mode is REQUIRED: a request with none (an
SPA older than the ledger) is refused with 422 "Reload the app to approve"
before any read or write, and the write-side value policy no longer admits
the server-only ``undeclared`` (historic rows stay readable on the receipt).
The row also records the draft's age at approval (``draft_age_seconds``, read
from Lakebase, never from the body).

Pinned here:

1. The schema rules (bulk_id needs a shared rationale; bulk modes need a
   bulk_id; individual modes refuse one; any declared mode needs the full
   generated draft proof) answer 422 and write nothing. So does a token
   outside the four (including the server-only ``undeclared``).
2. A declared mode is recorded; an omitted one is refused (422, no read,
   no write); the value policy admits exactly the four declared tokens.
3. ``draft_age_seconds`` rides the APPROVE row when the lookup returns it,
   is omitted when it does not, and never enters the decision intent.
4. The decision intent and derived fallback request id for
   ``review_mode=None`` are byte-identical to the ones 0a30fca2 computed
   (captured by running that tree with ``python -P``, pasted as literals).
5. A replay with the same payload returns the same approval; a replay whose
   review_mode differs is a 409.
6. A bulk_rationale the audit ledger's text policy refuses answers 422 with
   zero approvals rows and zero audit rows (it was a 503 before the router
   pre-check); with the pre-check bypassed, the commit's re-raise still
   answers 422, not 503.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api import outreach as outreach_mod
from backend.config.settings import settings
from backend.main import app
from backend.schemas.offer import OutreachApproveRequest
from backend.services.audit_metadata_public_values import _assert_public_safe_values
from backend.services.audit_store import AuditMetadataValueViolation, get_audit_store
from backend.services.outreach_decision_commit import _derive_fallback_request_id
from backend.services.outreach_decision_intent import _approval_decision_intent
from backend.services.repositories import get_outreach_repository
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)
OWNER = "skyler@entrada.ai"
HEADERS = {"X-Forwarded-Email": OWNER}
BORROWER = "B-48291"
BULK_ID = "22222222-2222-4222-8222-222222222222"
# A shared rationale the schema's name-shape validator admits and the audit
# ledger's protected-class policy refuses (so it reached the write, which
# answered 503 before the router pre-check). The brief's example, "Target
# unwed homeowners for this offer.", never reaches the router: the schema
# validator already answers 422 for it.
FLAGGED_BULK_RATIONALE = "Focus on borrowers without disabilities."


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


def _draft() -> dict[str, Any]:
    response = client.post(
        "/api/outreach/draft",
        json={"borrower_id": BORROWER, "channel": "email"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approval(draft: dict[str, Any] | None, **updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"borrower_id": BORROWER, "channel": "email"}
    if draft is not None:
        payload.update(
            {
                "offer_code": draft["offer_code"],
                "draft_subject": draft["subject"],
                "draft_body": draft["body"],
                "draft_generation_id": draft["generation_id"],
                "draft_response_hash": draft["response_hash"],
                "draft_source_refreshed_at": draft["source_refreshed_at"],
            }
        )
    payload.update(updates)
    return payload


def _approve_rows(audit: InMemoryAuditStore) -> list[dict[str, Any]]:
    return [
        dict(event.payload_json or {})
        for event in audit.list(limit=500)
        if event.event_type == "APPROVE"
    ]


def _approvals_inserts(lakebase) -> list[dict[str, Any]]:
    return [
        params for sql, params in lakebase.executes if "INSERT INTO mip_app.approvals" in sql
    ]


# -- 1. schema rules: 422, nothing written ------------------------------------------------


@pytest.mark.parametrize(
    ("updates", "with_proof", "message"),
    [
        ({"bulk_id": BULK_ID}, True, "bulk approvals require a shared rationale"),
        (
            {"bulk_id": BULK_ID, "bulk_rationale": "   "},
            True,
            "bulk approvals require a shared rationale",
        ),
        ({"review_mode": "bulk_sample"}, True, "bulk review modes require bulk_id"),
        ({"review_mode": "bulk_cohort"}, True, "bulk review modes require bulk_id"),
        (
            {"review_mode": "individual", "bulk_id": BULK_ID, "bulk_rationale": "Q3 sweep."},
            True,
            "individual review modes cannot carry bulk_id",
        ),
        (
            {"review_mode": "triage", "bulk_id": BULK_ID, "bulk_rationale": "Q3 sweep."},
            True,
            "individual review modes cannot carry bulk_id",
        ),
        ({"review_mode": "individual"}, False, "review_mode requires the generated draft proof"),
        ({"review_mode": "undeclared"}, True, "review_mode"),
        ({"review_mode": "blind"}, True, "review_mode"),
    ],
)
def test_invalid_review_combinations_answer_422_and_write_nothing(
    audit: InMemoryAuditStore,
    fake_lakebase_client,
    updates: dict[str, Any],
    with_proof: bool,
    message: str,
) -> None:
    draft = _draft() if with_proof else None
    drafts_before = len(fake_lakebase_client.generated_outreach_drafts)

    response = client.post("/api/outreach/approve", json=_approval(draft, **updates), headers=HEADERS)

    assert response.status_code == 422, response.text
    assert message in response.text
    assert _approvals_inserts(fake_lakebase_client) == []
    assert _approve_rows(audit) == []
    assert len(fake_lakebase_client.generated_outreach_drafts) == drafts_before


# -- 2. the ledger records the mode ---------------------------------------------------------


@pytest.mark.parametrize("mode", ["individual", "triage"])
def test_a_declared_mode_is_recorded_on_the_approve_row(
    audit: InMemoryAuditStore, fake_lakebase_client, mode: str
) -> None:
    response = client.post(
        "/api/outreach/approve", json=_approval(_draft(), review_mode=mode), headers=HEADERS
    )

    assert response.status_code == 200, response.text
    (row,) = _approve_rows(audit)
    assert row["review_mode"] == mode
    (inserted,) = _approvals_inserts(fake_lakebase_client)
    assert json.loads(inserted["decision_intent"])["review_mode"] == mode


def test_a_bulk_sample_row_is_recorded_with_its_bulk_id(audit: InMemoryAuditStore) -> None:
    response = client.post(
        "/api/outreach/approve",
        json=_approval(
            _draft(),
            review_mode="bulk_sample",
            bulk_id=BULK_ID,
            bulk_rationale="Q3 retention sweep, reviewed against current rules.",
        ),
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    (row,) = _approve_rows(audit)
    assert row["review_mode"] == "bulk_sample"
    assert row["bulk_id"] == BULK_ID


def test_an_omitted_mode_is_refused_before_any_read_or_write(
    monkeypatch: pytest.MonkeyPatch, audit: InMemoryAuditStore, fake_lakebase_client
) -> None:
    # W5c (D-approval-flow-a1): a stale cached SPA sends no review_mode. It is
    # told to reload, and nothing is read or written: no replay lookup, no
    # borrower read, no approvals row, no audit row.
    draft = _draft()
    looked_up: list[str] = []
    original = fake_lakebase_client.fetchone

    def _fetchone(sql: str, params: dict[str, Any] | None = None):
        looked_up.append(sql)
        return original(sql, params)

    monkeypatch.setattr(fake_lakebase_client, "fetchone", _fetchone)
    repo = app.dependency_overrides[get_outreach_repository]()
    monkeypatch.setattr(repo, "find_borrower", MagicMock(side_effect=AssertionError("borrower read")))
    response = client.post(
        "/api/outreach/approve",
        json=_approval(
            None, draft_body=draft["body"], draft_subject=draft["subject"], request_id=str(uuid4())
        ),
        headers=HEADERS,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "Reload the app to approve"
    assert looked_up == []
    assert _approvals_inserts(fake_lakebase_client) == []
    assert _approve_rows(audit) == []


@pytest.mark.parametrize("value", ["individual", "triage", "bulk_sample", "bulk_cohort"])
def test_the_value_policy_admits_the_four_declared_tokens(value: str) -> None:
    _assert_public_safe_values({"review_mode": value})


@pytest.mark.parametrize("value", ["blind", "INDIVIDUAL", "", "bulk", "undeclared"])
def test_the_value_policy_refuses_any_other_token(value: str) -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"review_mode": value})


@pytest.mark.parametrize("value", [0, 42, 315_360_000])
def test_the_value_policy_admits_a_bounded_draft_age(value: int) -> None:
    _assert_public_safe_values({"draft_age_seconds": value})


@pytest.mark.parametrize("value", [-1, 315_360_001, True, "42", 4.2])
def test_the_value_policy_refuses_an_unbounded_draft_age(value: object) -> None:
    with pytest.raises(AuditMetadataValueViolation):
        _assert_public_safe_values({"draft_age_seconds": value})


# -- 3. draft_age_seconds -----------------------------------------------------------------


def _serve_draft_age(monkeypatch: pytest.MonkeyPatch, lakebase, age: int | None) -> None:
    original = lakebase.fetchone

    def _fetchone(sql: str, params: dict[str, Any] | None = None):
        row = original(sql, params)
        if row is not None and "FROM mip_app.generated_outreach_drafts" in sql:
            assert "draft_age_seconds" in sql, "the lookup selects the draft's age"
            return {**row, "draft_age_seconds": age}
        return row

    monkeypatch.setattr(lakebase, "fetchone", _fetchone)


def test_the_draft_age_from_the_lookup_rides_the_approve_row(
    monkeypatch: pytest.MonkeyPatch, audit: InMemoryAuditStore, fake_lakebase_client
) -> None:
    _serve_draft_age(monkeypatch, fake_lakebase_client, 42)

    response = client.post(
        "/api/outreach/approve",
        json=_approval(_draft(), review_mode="individual"),
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    (row,) = _approve_rows(audit)
    assert row["draft_age_seconds"] == 42
    (inserted,) = _approvals_inserts(fake_lakebase_client)
    assert "draft_age_seconds" not in inserted["decision_intent"]


def test_no_draft_age_key_when_the_lookup_has_none(
    monkeypatch: pytest.MonkeyPatch, audit: InMemoryAuditStore
) -> None:
    response = client.post(
        "/api/outreach/approve",
        json=_approval(_draft(), review_mode="individual"),
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    (row,) = _approve_rows(audit)
    assert "draft_age_seconds" not in row


# -- 4. golden intents for review_mode=None ------------------------------------------------

ACTOR = "approver.one@summit.example"
GOLDEN_APPROVE_INTENT = (
    '{"action":"approve","actor":"approver.one@summit.example","assigned_to_email":null,'
    '"borrower_id":"B-000000000A001","bulk_id":null,"bulk_rationale":null,"campaign_id":null,'
    '"campaign_owner_email":null,"campaign_treatment_fingerprint":null,"channel":"email",'
    '"draft_body":"Hello. Reply STOP to opt out.",'
    '"draft_generation_id":"11111111-1111-4111-8111-111111111111",'
    '"draft_response_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"draft_source_refreshed_at":"2026-09-01T00:00:00+00:00",'
    '"draft_subject":"A governed mortgage review","evidence_ids":["ev-001","ev-002"],'
    '"evidence_ids_supplied":true,"follow_up_in_days":null,"offer_code":"refi",'
    '"offer_code_supplied":true,"rationale":"Reviewed against current rules.","variant_name":null}'
)
GOLDEN_APPROVE_FALLBACK = "auto-35a8f60c2a025c4769f1b1dee0708d6f"
GOLDEN_BULK_INTENT = (
    '{"action":"approve","actor":"approver.one@summit.example","assigned_to_email":null,'
    '"borrower_id":"B-000000000A002","bulk_id":"22222222-2222-4222-8222-222222222222",'
    '"bulk_rationale":"Q3 retention sweep, reviewed against current rules.","campaign_id":null,'
    '"campaign_owner_email":null,"campaign_treatment_fingerprint":null,"channel":"email",'
    '"draft_body":"Hello. Reply STOP to opt out.","draft_generation_id":null,'
    '"draft_response_hash":null,"draft_source_refreshed_at":null,"draft_subject":null,'
    '"evidence_ids":["ev-003"],"evidence_ids_supplied":false,"follow_up_in_days":null,'
    '"offer_code":"heloc","offer_code_supplied":false,"rationale":null,"variant_name":null}'
)
GOLDEN_BULK_FALLBACK = "auto-0626dd2df1e86f136d5885abf66f3a81"


def _golden_payloads() -> tuple[OutreachApproveRequest, OutreachApproveRequest]:
    single = OutreachApproveRequest(
        borrower_id="B-000000000A001",
        offer_code="refi",
        channel="email",
        evidence_ids=["ev-001", "ev-002"],
        rationale="Reviewed against current rules.",
        draft_body="Hello. Reply STOP to opt out.",
        draft_subject="A governed mortgage review",
        draft_generation_id="11111111-1111-4111-8111-111111111111",
        draft_response_hash="a" * 64,
        draft_source_refreshed_at="2026-09-01T00:00:00+00:00",
    )
    bulk = OutreachApproveRequest(
        borrower_id="B-000000000A002",
        channel="email",
        bulk_id=BULK_ID,
        bulk_rationale="Q3 retention sweep, reviewed against current rules.",
        draft_body="Hello. Reply STOP to opt out.",
    )
    return single, bulk


def test_intents_without_a_review_mode_are_byte_identical_to_0a30fca2() -> None:
    single, bulk = _golden_payloads()
    single_intent = _approval_decision_intent(
        single,
        actor=ACTOR,
        offer_code="refi",
        evidence_ids=["ev-001", "ev-002"],
        safe_rationale="Reviewed against current rules.",
        safe_bulk_rationale=None,
        campaign_owner_email=None,
        campaign_treatment_fingerprint=None,
    )
    bulk_intent = _approval_decision_intent(
        bulk,
        actor=ACTOR,
        offer_code="heloc",
        evidence_ids=["ev-003"],
        safe_rationale=None,
        safe_bulk_rationale="Q3 retention sweep, reviewed against current rules.",
        campaign_owner_email=None,
        campaign_treatment_fingerprint=None,
    )

    assert single_intent == GOLDEN_APPROVE_INTENT
    assert bulk_intent == GOLDEN_BULK_INTENT
    assert (
        _derive_fallback_request_id(actor=ACTOR, action="approve", decision_intent=single_intent)
        == GOLDEN_APPROVE_FALLBACK
    )
    assert (
        _derive_fallback_request_id(actor=ACTOR, action="approve", decision_intent=bulk_intent)
        == GOLDEN_BULK_FALLBACK
    )


def test_a_declared_mode_enters_the_intent_and_changes_the_fallback_id() -> None:
    single, _ = _golden_payloads()
    declared = single.model_copy(update={"review_mode": "individual"})
    intent = _approval_decision_intent(
        declared,
        actor=ACTOR,
        offer_code="refi",
        evidence_ids=["ev-001", "ev-002"],
        safe_rationale="Reviewed against current rules.",
        safe_bulk_rationale=None,
        campaign_owner_email=None,
        campaign_treatment_fingerprint=None,
    )

    assert json.loads(intent)["review_mode"] == "individual"
    assert (
        _derive_fallback_request_id(actor=ACTOR, action="approve", decision_intent=intent)
        != GOLDEN_APPROVE_FALLBACK
    )


# -- 5. replay -----------------------------------------------------------------------------


def _serve_persisted_approvals(monkeypatch: pytest.MonkeyPatch, lakebase) -> None:
    original = lakebase.fetchone

    def _fetchone(sql: str, params: dict[str, Any] | None = None):
        if "FROM mip_app.approvals" in sql and "request_id" in sql:
            request_id = (params or {}).get("request_id")
            for row in lakebase.approvals:
                if row.get("request_id") == request_id:
                    return dict(row)
            return None
        return original(sql, params)

    monkeypatch.setattr(lakebase, "fetchone", _fetchone)


def test_a_replay_returns_the_same_approval_and_a_changed_mode_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch, audit: InMemoryAuditStore, fake_lakebase_client
) -> None:
    _serve_persisted_approvals(monkeypatch, fake_lakebase_client)
    payload = _approval(_draft(), review_mode="individual", request_id=str(uuid4()))

    first = client.post("/api/outreach/approve", json=payload, headers=HEADERS)
    again = client.post("/api/outreach/approve", json=payload, headers=HEADERS)
    changed = client.post(
        "/api/outreach/approve", json={**payload, "review_mode": "triage"}, headers=HEADERS
    )

    assert first.status_code == 200, first.text
    assert again.status_code == 200, again.text
    assert again.json()["approval_id"] == first.json()["approval_id"]
    assert changed.status_code == 409, changed.text
    assert len(_approvals_inserts(fake_lakebase_client)) == 1
    assert len(_approve_rows(audit)) == 1


# -- 6. the text policy answers 422 before any write ----------------------------------------


def test_a_refused_bulk_rationale_answers_422_and_writes_nothing(
    audit: InMemoryAuditStore, fake_lakebase_client
) -> None:
    draft = _draft()
    drafts_before = len(fake_lakebase_client.generated_outreach_drafts)

    response = client.post(
        "/api/outreach/approve",
        json=_approval(
            draft,
            review_mode="bulk_sample",
            bulk_id=BULK_ID,
            bulk_rationale=FLAGGED_BULK_RATIONALE,
        ),
        headers=HEADERS,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "bulk_rationale failed the governed text policy"
    assert FLAGGED_BULK_RATIONALE not in response.text
    assert _approvals_inserts(fake_lakebase_client) == []
    assert _approve_rows(audit) == []
    assert len(fake_lakebase_client.generated_outreach_drafts) == drafts_before


def test_a_refused_rationale_answers_422_before_the_replay_lookup(
    monkeypatch: pytest.MonkeyPatch, audit: InMemoryAuditStore, fake_lakebase_client
) -> None:
    draft = _draft()
    looked_up: list[str] = []
    original = fake_lakebase_client.fetchone

    def _fetchone(sql: str, params: dict[str, Any] | None = None):
        looked_up.append(sql)
        return original(sql, params)

    monkeypatch.setattr(fake_lakebase_client, "fetchone", _fetchone)
    response = client.post(
        "/api/outreach/approve",
        json=_approval(
            draft, review_mode="individual", rationale=FLAGGED_BULK_RATIONALE, request_id=str(uuid4())
        ),
        headers=HEADERS,
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "rationale failed the governed text policy"
    assert looked_up == [], "nothing is read before the text policy refuses"
    assert _approve_rows(audit) == []


class _AtomicResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


def _enable_atomic_decisions(monkeypatch: pytest.MonkeyPatch, lakebase) -> list[str]:
    """The production transaction contract; returns the SQL each transaction ran."""

    ran: list[str] = []

    class _Connection:
        def execute(self, sql: str, params: dict[str, Any] | None = None) -> _AtomicResult:
            values = params or {}
            ran.append(sql)
            if "pg_advisory_xact_lock" in sql:
                return _AtomicResult(None)
            if "SELECT approval_id" in sql and "FROM mip_app.approvals" in sql:
                return _AtomicResult(None)
            if "INSERT INTO mip_app.approvals" in sql:
                return _AtomicResult({"approval_id": values["approval_id"]})
            if "INSERT INTO mip_app.action_audit" in sql:
                return _AtomicResult(
                    {"audit_id": str(uuid4()), "audit_sequence": 1, "event_at": datetime.now(UTC)}
                )
            if "UPDATE mip_app.approvals" in sql:
                return _AtomicResult({"approval_id": values["approval_id"]})
            raise AssertionError(f"unexpected transactional SQL: {sql}")

    @contextmanager
    def _transaction():
        yield _Connection()

    monkeypatch.setattr(lakebase, "_supports_atomic_transactions", True, raising=False)
    monkeypatch.setattr(lakebase, "transaction", _transaction)
    return ran


def test_the_commit_reraise_still_answers_422_when_the_precheck_is_bypassed(
    monkeypatch: pytest.MonkeyPatch, fake_lakebase_client
) -> None:
    draft = _draft()
    ran = _enable_atomic_decisions(monkeypatch, fake_lakebase_client)
    monkeypatch.setattr(outreach_mod, "_refuse_ungoverned_text", lambda values: None)

    response = client.post(
        "/api/outreach/approve",
        json=_approval(
            draft,
            review_mode="bulk_sample",
            bulk_id=BULK_ID,
            bulk_rationale=FLAGGED_BULK_RATIONALE,
        ),
        headers=HEADERS,
    )

    assert response.status_code == 422, response.text
    # The shared rationale is also the row's rationale; the write's free-text
    # loop may name either key first.
    assert response.json()["detail"] in {
        "rationale failed the governed text policy",
        "bulk_rationale failed the governed text policy",
    }
    assert FLAGGED_BULK_RATIONALE not in response.text
    # The value policy refused inside the transaction, before the audit insert.
    assert not any("INSERT INTO mip_app.action_audit" in sql for sql in ran)
