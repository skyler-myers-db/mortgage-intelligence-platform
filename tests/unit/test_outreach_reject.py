"""Unit tests for ``POST /api/outreach/reject``.

Audit finding 2026-04-22: the UI's "Reject" controls only mutated
local state; dropped borrowers left no durable audit trail. This
endpoint is the reject-side twin of ``/api/outreach/approve`` and must
write both the Lakebase decision row AND the append-only audit event.

Invariants covered:

1. ``/api/outreach/reject`` returns 200 + ``rejected=True`` with a
   fresh UUID ``approval_id`` and an audit event id.
2. The call writes one row to ``mip_app.approvals`` via
   ``lakebase.execute`` with ``action='reject'`` and the
   authenticated actor.
3. The call writes one row to ``mip_app.action_audit`` via the audit
   store with ``event_type='OUTREACH_REJECT'``.
4. The debounced lifecycle-sync trigger is scheduled on reject too
   (so the funnel view reflects rejected-borrower counts).
5. A Lakebase failure surfaces as 503 -- no silent fallback, matches
   the approve path contract.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import RLock
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api import outreach as outreach_mod
from backend.main import app
from backend.services import job_trigger, lakebase_bootstrap
from backend.services.audit_decision_inputs import DECISION_INPUT_KEYS
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.lakebase_bootstrap import _reset_bootstrap_for_tests
from backend.services.repositories import get_outreach_repository
from backend.services.resilience import _reset_breakers_for_tests
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore


def _disclosure_row(params: dict[str, Any] | None = None) -> dict[str, str]:
    params = params or {}
    channel = params.get("channel", "email")
    body = (
        "Summit Mortgage NMLS #123456. Equal Housing Lender. Reply STOP to opt out."
        if channel == "sms"
        else "Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
    )
    return {
        "state": params.get("state", "_ALL"),
        "channel": channel,
        "disclosure_version": "test-disclosure-v1",
        "body": body,
    }


DISCLOSURE_BODY = _disclosure_row()["body"]
APPROVAL_DRAFT_BODY = f"Governed approval body. {DISCLOSURE_BODY}"


def _record_non_atomic_approval(
    inserted: dict[str, dict[str, Any]],
    sql: str,
    params: dict[str, Any],
) -> None:
    if "INSERT INTO mip_app.approvals" in sql and params.get("request_id"):
        inserted[params["request_id"]] = {
            "approval_id": params["approval_id"],
            "borrower_id": params["borrower_id"],
            "action": params["action"],
            "actor_email": params["actor_email"],
            "decision_intent": params["decision_intent"],
            "decision_payload_hash": params["decision_payload_hash"],
            "decision_response": None,
            "audit_event_id": None,
        }
        return
    if "UPDATE mip_app.approvals" in sql:
        for row in inserted.values():
            if str(row["approval_id"]) == str(params["approval_id"]):
                row["decision_response"] = json.loads(params["decision_response"])
                row["audit_event_id"] = params["audit_event_id"]
                return


def _fetchone_none_or_disclosure(
    sql: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if "FROM mip_app.tenant_disclosures" in sql:
        return _disclosure_row(params)
    return None


class _TxnResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _TxnContext:
    def __init__(self, owner: _AtomicLakebase) -> None:
        self.owner = owner

    def __enter__(self) -> _AtomicConn:
        self.owner.conn = _AtomicConn(self.owner)
        return self.owner.conn

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if exc_type is None:
            self.owner.committed = True
            self.owner.committed_approvals.extend(self.owner.pending_approvals)
        else:
            self.owner.rolled_back = True
        self.owner.pending_approvals.clear()
        return False


class _AtomicConn:
    def __init__(self, owner: _AtomicLakebase) -> None:
        self.owner = owner

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> _TxnResult:
        params = params or {}
        self.owner.executed_sql.append(sql)
        if "INSERT INTO mip_app.approvals" in sql:
            if self.owner.conflict:
                self.owner.last_attempt_params = dict(params)
                return _TxnResult(None)
            self.owner.pending_approvals.append(dict(params))
            return _TxnResult({"approval_id": params["approval_id"]})
        if "SELECT approval_id" in sql:
            if self.owner.existing_row:
                return _TxnResult(self.owner.existing_row)
            if self.owner.existing_approval_id:
                attempted = self.owner.last_attempt_params or {}
                audit_event_id = "22222222-2222-4222-8222-222222222222"
                return _TxnResult(
                    {
                        "approval_id": self.owner.existing_approval_id,
                        "borrower_id": "B-48291",
                        "action": "approve",
                        "actor_email": "lo@example.com",
                        "decision_intent": attempted.get("decision_intent"),
                        "decision_payload_hash": attempted.get("decision_payload_hash"),
                        "decision_response": {
                            "approved": True,
                            "approval_id": self.owner.existing_approval_id,
                            "audit_event_id": audit_event_id,
                            "assigned_to_email": None,
                            "follow_up_at": None,
                        },
                        "audit_event_id": audit_event_id,
                    }
                )
            return _TxnResult(None)
        if "INSERT INTO mip_app.action_audit" in sql:
            self.owner.audit_insert_count += 1
            if self.owner.audit_fails:
                raise LakebaseError("audit insert failed")
            self.owner.audit_params = dict(params)
            return _TxnResult(
                {
                    "audit_id": "evt-atomic",
                    "audit_sequence": 1,
                    "event_at": datetime.now(UTC),
                }
            )
        if "UPDATE mip_app.approvals" in sql:
            for row in self.owner.pending_approvals:
                if str(row["approval_id"]) == str(params["approval_id"]):
                    row["decision_response"] = params["decision_response"]
                    row["audit_event_id"] = params["audit_event_id"]
                    return _TxnResult(
                        {
                            "approval_id": row["approval_id"],
                            "decision_response": params["decision_response"],
                            "audit_event_id": params["audit_event_id"],
                        }
                    )
            return _TxnResult(None)
        raise AssertionError(f"unexpected SQL: {sql}")


class _AtomicLakebase:
    _supports_atomic_transactions = True

    def __init__(
        self,
        *,
        audit_fails: bool = False,
        conflict: bool = False,
        existing_approval_id: str | None = None,
        existing_row: dict[str, Any] | None = None,
    ) -> None:
        self.audit_fails = audit_fails
        self.conflict = conflict
        self.existing_approval_id = existing_approval_id
        self.existing_row = existing_row
        self.pending_approvals: list[dict[str, Any]] = []
        self.committed_approvals: list[dict[str, Any]] = []
        self.executed_sql: list[str] = []
        self.audit_insert_count = 0
        self.audit_params: dict[str, Any] | None = None
        self.last_attempt_params: dict[str, Any] | None = None
        self.committed = False
        self.rolled_back = False
        self.conn: _AtomicConn | None = None

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if "FROM mip_app.tenant_disclosures" in sql:
            return _disclosure_row(params)
        return None

    def transaction(self) -> _TxnContext:
        return _TxnContext(self)


class _ConcurrentDecisionConn:
    def __init__(self, owner: _ConcurrentDecisionLakebase) -> None:
        self.owner = owner

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> _TxnResult:
        values = params or {}
        if "INSERT INTO mip_app.approvals" in sql:
            request_id = str(values["request_id"])
            if request_id in self.owner.approvals:
                return _TxnResult(None)
            row = {
                **values,
                "decision_response": None,
                "audit_event_id": None,
            }
            self.owner.approvals[request_id] = row
            return _TxnResult({"approval_id": row["approval_id"]})
        if "SELECT approval_id" in sql:
            row = self.owner.approvals.get(str(values["request_id"]))
            return _TxnResult(dict(row) if row is not None else None)
        if "INSERT INTO mip_app.action_audit" in sql:
            self.owner.audit_count += 1
            return _TxnResult(
                {
                    "audit_id": str(uuid4()),
                    "audit_sequence": self.owner.audit_count,
                    "event_at": datetime.now(UTC),
                }
            )
        if "UPDATE mip_app.approvals" in sql:
            for row in self.owner.approvals.values():
                if str(row["approval_id"]) == str(values["approval_id"]):
                    row["decision_response"] = json.loads(values["decision_response"])
                    row["audit_event_id"] = values["audit_event_id"]
                    return _TxnResult(dict(row))
            return _TxnResult(None)
        raise AssertionError(f"unexpected SQL: {sql}")


class _ConcurrentDecisionTransaction:
    def __init__(self, owner: _ConcurrentDecisionLakebase) -> None:
        self.owner = owner

    def __enter__(self) -> _ConcurrentDecisionConn:
        self.owner.lock.acquire()
        return _ConcurrentDecisionConn(self.owner)

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        self.owner.lock.release()
        return False


class _ConcurrentDecisionLakebase:
    _supports_atomic_transactions = True

    def __init__(self) -> None:
        self.lock = RLock()
        self.approvals: dict[str, dict[str, Any]] = {}
        self.audit_count = 0

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if "FROM mip_app.tenant_disclosures" in sql:
            return _disclosure_row(params)
        if "FROM mip_app.approvals" in sql:
            with self.lock:
                row = self.approvals.get(str((params or {}).get("request_id")))
                return dict(row) if row is not None else None
        return None

    def transaction(self) -> _ConcurrentDecisionTransaction:
        return _ConcurrentDecisionTransaction(self)


@pytest.fixture(autouse=True)
def _reset_trigger_state():
    """Isolate the debounce cache across tests so each reject fires.

    Also resets the process-wide circuit breakers before AND after
    each test. The 503 test below intentionally raises 5 Lakebase
    failures to exercise the no-silent-fallback path; without this
    reset the 'lakebase' breaker stays OPEN and poisons every later
    test in the session that touches Lakebase.

    R5-01 addendum: also reset the per-process lakebase-bootstrap
    flag so each test gets a predictable execute-call count. The
    bootstrap runs once per process and issues 2 ALTER/CREATE
    statements on the Lakebase client the first time a test hits
    approve/reject -- zeroing the flag ahead of each test makes call-
    count assertions stable across test ordering.
    """
    _reset_breakers_for_tests()
    # Mark the R5-01 DDL bootstrap as "already done" for this test so the
    # approve/reject path doesn't emit the two ALTER/CREATE statements
    # that would throw off execute-call-count assertions. The tests that
    # want to exercise the bootstrap itself call _reset_bootstrap_for_tests()
    # explicitly before their action.
    lakebase_bootstrap._APPROVAL_REQUEST_ID_BOOTSTRAPPED = True
    # Feature C: same posture for the assignment/follow-up column bootstrap
    # so the approve path doesn't emit its two ALTER statements and skew the
    # execute-call-count assertions below.
    lakebase_bootstrap._APPROVAL_FOLLOWUP_BOOTSTRAPPED = True
    job_trigger._reset_for_tests()
    yield
    _reset_breakers_for_tests()
    _reset_bootstrap_for_tests()


@pytest.fixture
def override_deps():
    """Safely layer test-local dependency overrides on top of the
    session-scoped conftest overrides. Snapshots the current bindings
    for ``get_audit_store`` / ``get_lakebase_client``, applies the
    caller's replacements, and restores the snapshot on teardown --
    so ``app.dependency_overrides.pop(...)`` doesn't accidentally
    strip the InMemoryAuditStore / _FakeLakebaseClient bindings the
    rest of the session relies on.
    """
    saved: dict[Any, Any] = {}

    def _apply(*, audit: Any = None, lakebase: Any = None) -> None:
        if audit is not None:
            saved[get_audit_store] = app.dependency_overrides.get(get_audit_store)
            app.dependency_overrides[get_audit_store] = lambda: audit
        if lakebase is not None:
            saved[get_lakebase_client] = app.dependency_overrides.get(get_lakebase_client)
            app.dependency_overrides[get_lakebase_client] = lambda: lakebase

    yield _apply

    for key, original in saved.items():
        if original is None:
            app.dependency_overrides.pop(key, None)
        else:
            app.dependency_overrides[key] = original


def test_reject_writes_approval_and_audit_rows(override_deps) -> None:
    """POST /api/outreach/reject inserts into mip_app.approvals AND
    writes an OUTREACH_REJECT audit event."""
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.execute = MagicMock()
    # R6-19: the server derives a fallback ``request_id`` for legacy
    # clients (no body field), so the idempotency lookup runs on every
    # request. Pin fetchone to None so this first call sees no prior
    # approval and proceeds with the INSERT + audit write.
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    # R6 actor-spoof fix: actor attribution is the edge-authenticated
    # identity (X-Forwarded-Email), not the request body. Body ``actor``
    # is retained for backcompat but ignored for audit writes.
    resp = client.post(
        "/api/outreach/reject",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "evidence_ids": ["ev-001", "ev-002", "ev-003"],
            "rationale_code": "opt_out",
            "rationale": "Borrower opted out",
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"] is True
    assert body["approval_id"]  # non-empty UUID string
    assert body["audit_event_id"]

    # mip_app.approvals write: exactly one execute() call with
    # action='reject' and our actor.
    approval_calls = [
        call
        for call in fake_lakebase.execute.call_args_list
        if "INSERT INTO mip_app.approvals" in call.args[0]
    ]
    assert len(approval_calls) == 1
    sql, params = approval_calls[0].args
    assert "INSERT INTO mip_app.approvals" in sql
    assert params["action"] == "reject"
    assert params["actor_email"] == "lo@example.com"
    assert params["borrower_id"] == "B-48291"
    assert params["offer_code"] == "heloc"
    assert params["rationale"] == "opt out: Borrower opted out"
    # approval_id in the INSERT must match the id we returned.
    assert params["approval_id"] == body["approval_id"]

    # action_audit write: exactly one OUTREACH_REJECT event.
    events = audit.list(limit=50)
    assert len(events) == 1
    evt = events[0]
    assert evt.event_type == "OUTREACH_REJECT"
    assert evt.action == "outreach.reject"
    assert evt.entity_type == "approval"
    assert evt.entity_id == body["approval_id"]
    assert evt.actor == "lo@example.com"
    assert evt.evidence_ids == ["ev-001", "ev-002", "ev-003"]
    assert evt.subject_clip is not None
    assert evt.payload_json["borrower_id"] == "B-48291"
    assert evt.payload_json["offer_code"] == "heloc"
    assert evt.payload_json["rationale_code"] == "opt_out"
    assert evt.payload_json["rationale"] == "opt out: Borrower opted out"


def test_reject_rejects_evidence_ids_not_owned_by_borrower(override_deps) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    override_deps(audit=audit, lakebase=fake_lakebase)

    response = TestClient(app).post(
        "/api/outreach/reject",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "evidence_ids": ["ev-001", "ev-other-borrower"],
            "rationale_code": "low_intent",
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert response.status_code == 422, response.text
    assert "must exactly match the borrower recommendation" in response.json()["detail"]
    fake_lakebase.execute.assert_not_called()
    assert audit.list(limit=10) == []


def test_reject_rejects_partial_recommendation_evidence(override_deps) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    override_deps(audit=audit, lakebase=fake_lakebase)

    response = TestClient(app).post(
        "/api/outreach/reject",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "evidence_ids": ["ev-001"],
            "rationale_code": "low_intent",
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert response.status_code == 422, response.text
    assert "must exactly match the borrower recommendation" in response.json()["detail"]
    fake_lakebase.execute.assert_not_called()
    assert audit.list(limit=10) == []


def test_approve_reject_unknown_borrower_fail_closed_before_lakebase(override_deps) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    for path, body in (
        ("/api/outreach/approve", {"borrower_id": "B-DOES-NOT-EXIST", "offer_code": "heloc"}),
        (
            "/api/outreach/reject",
            {
                "borrower_id": "B-DOES-NOT-EXIST",
                "offer_code": "heloc",
                "rationale_code": "low_intent",
            },
        ),
    ):
        response = client.post(path, json=body, headers={"X-Forwarded-Email": "lo@example.com"})
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Borrower B-DOES-NOT-EXIST not found"

    fake_lakebase.execute.assert_not_called()
    fake_lakebase.fetchone.assert_not_called()
    assert audit.list(limit=10) == []


def test_reject_scrubs_free_text_before_decision_and_audit_rows(override_deps) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    override_deps(audit=audit, lakebase=fake_lakebase)

    raw_rationale = (
        "Borrower emailed jane@example.com, phone 555-123-4567, "
        "SSN 123-45-6789, address 123 Main St."
    )
    client = TestClient(app)
    resp = client.post(
        "/api/outreach/reject",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "rationale_code": "data_quality",
            "rationale": raw_rationale,
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )
    assert resp.status_code == 200, resp.text

    approval_call = next(
        call
        for call in fake_lakebase.execute.call_args_list
        if "INSERT INTO mip_app.approvals" in call.args[0]
    )
    _sql, params = approval_call.args
    persisted_rationale = params["rationale"]
    assert persisted_rationale.startswith("data quality: ")
    assert "[EMAIL-REDACTED]" in persisted_rationale
    assert "[PHONE-REDACTED]" in persisted_rationale
    assert "[SSN-REDACTED]" in persisted_rationale
    assert "[ADDRESS-REDACTED]" in persisted_rationale
    for raw in ("jane@example.com", "555-123-4567", "123-45-6789", "123 Main St"):
        assert raw not in persisted_rationale

    evt = audit.list(limit=1)[0]
    audit_rationale = evt.payload_json["rationale"]
    assert audit_rationale == persisted_rationale


def test_draft_outreach_is_relationship_and_channel_aware() -> None:
    client = TestClient(app)

    current = client.post(
        "/api/outreach/draft",
        json={"borrower_id": "B-65102", "channel": "email"},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )
    assert current.status_code == 200, current.text
    current_body = current.json()["body"]
    assert current.json()["generation_mode"] in {"supervisor", "governed_fallback"}
    assert current.json()["generator_label"]
    assert current.json()["strategy_summary"]
    assert current.json()["evidence_summary"]
    assert "Summit Mortgage customer" in current_body
    assert "public-record" not in current_body
    assert "may qualify" not in current_body
    assert "[first name]" not in current_body
    assert "NMLS #123456" in current_body
    assert "Equal Housing" in current_body
    assert current.json()["offer_code"] in {
        "retention",
        "nurture",
        "refi",
        "refi_plus_heloc",
        "cash_out",
    }

    sms = client.post(
        "/api/outreach/draft",
        json={"borrower_id": "B-65102", "channel": "sms"},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )
    assert sms.status_code == 200, sms.text
    sms_body = sms.json()["body"]
    assert sms.json()["subject"] is None
    assert len(sms_body) <= 160
    assert "STOP" in sms_body
    assert "\n" not in sms_body


def test_draft_outreach_uses_configured_lender_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(outreach_mod.settings, "mip_lender_name", "Acme Mortgage")
    monkeypatch.setattr(outreach_mod.settings, "mip_tenant_id", "summit")

    response = TestClient(app).post(
        "/api/outreach/draft",
        json={"borrower_id": "B-65102", "channel": "email"},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert response.status_code == 200, response.text
    body = response.json()["body"]
    assert response.json()["subject"] == "A quick mortgage review from Acme Mortgage"
    assert "Acme Mortgage customer" in body
    assert "Summit Mortgage customer" not in body


def test_supervisor_draft_is_reconstructed_from_exact_durable_response(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[dict[str, Any]] = []

    def _fetchone(sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "FROM mip_app.tenant_disclosures" in sql:
            return _disclosure_row(params)
        if "INSERT INTO mip_app.generated_outreach_drafts" in sql:
            stored.append({"sql": sql, **params})
            return {
                "generation_id": params["generation_id"],
                "audit_event_id": params["audit_id"],
                "response_hash": params["response_hash"],
                "response_json": json.loads(params["response_json"]),
            }
        return None

    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone
    override_deps(audit=InMemoryAuditStore(), lakebase=fake_lakebase)
    monkeypatch.setattr(
        outreach_mod,
        "compose_intelligent_outreach",
        lambda **kwargs: SimpleNamespace(
            subject="A focused mortgage review",
            body=f"A review may help clarify your available options. {DISCLOSURE_BODY}",
            generation_mode="supervisor",
            generator_label="Supervisor-optimized message",
            strategy_summary="Lead with borrower autonomy and one focused review path.",
            evidence_summary=["Primary offer: home equity line of credit"],
            evidence_assets=["mip.gold.borrower_360"],
        ),
    )

    response = TestClient(app).post(
        "/api/outreach/draft",
        json={"borrower_id": "B-48291", "channel": "email"},
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert response.status_code == 200, response.text
    assert len(stored) == 1
    assert "INSERT INTO mip_app.action_audit" in stored[0]["sql"]
    assert json.loads(stored[0]["response_json"]) == response.json()
    assert response.json()["generation_mode"] == "supervisor"
    assert response.json()["strategy_summary"] == (
        "Lead with borrower autonomy and one focused review path."
    )
    assert "body" not in json.loads(stored[0]["metadata"])


def test_atomic_decision_rolls_back_if_audit_insert_fails(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = InMemoryAuditStore()
    lakebase = _AtomicLakebase(audit_fails=True)
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=lakebase)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "draft_subject": "Your mortgage review",
            "draft_body": APPROVAL_DRAFT_BODY,
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert resp.status_code == 503, resp.text
    assert lakebase.rolled_back is True
    assert lakebase.committed is False
    assert lakebase.committed_approvals == []


def test_atomic_conflict_does_not_write_audit_for_uninserted_approval(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = InMemoryAuditStore()
    existing_id = "11111111-1111-1111-1111-111111111111"
    lakebase = _AtomicLakebase(conflict=True, existing_approval_id=existing_id)
    trigger_calls: list[str] = []
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": trigger_calls.append(reason),
    )
    override_deps(audit=audit, lakebase=lakebase)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "request_id": "11111111-1111-4111-8111-111111111111",
            "draft_subject": "Your mortgage review",
            "draft_body": APPROVAL_DRAFT_BODY,
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["approval_id"] == existing_id
    assert resp.json()["audit_event_id"] == "22222222-2222-4222-8222-222222222222"
    assert lakebase.audit_insert_count == 0
    assert lakebase.committed_approvals == []
    assert trigger_calls == []


def test_atomic_conflict_rejects_request_id_for_different_decision(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = InMemoryAuditStore()
    lakebase = _AtomicLakebase(
        conflict=True,
        existing_row={
            "approval_id": "11111111-1111-1111-1111-111111111111",
            "borrower_id": "B-48294",
            "action": "reject",
            "actor_email": "other@example.com",
        },
    )
    trigger_calls: list[str] = []
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": trigger_calls.append(reason),
    )
    override_deps(audit=audit, lakebase=lakebase)

    client = TestClient(app)
    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "request_id": "11111111-1111-4111-8111-111111111111",
            "draft_subject": "Your mortgage review",
            "draft_body": APPROVAL_DRAFT_BODY,
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert response.status_code == 409, response.text
    assert "different outreach decision" in response.json()["detail"]
    assert lakebase.audit_insert_count == 0
    assert lakebase.committed_approvals == []
    assert audit.list(limit=10) == []
    assert trigger_calls == []


def test_approve_audit_captures_decision_inputs(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.execute = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "draft_subject": "Your mortgage review",
            "draft_body": APPROVAL_DRAFT_BODY,
        },
        headers={
            "X-Forwarded-Email": "lo@example.com",
            "X-Correlation-ID": "forensic-approve-audit",
        },
    )

    assert response.status_code == 200, response.text
    events = audit.list(limit=10, event_type="APPROVE")
    assert len(events) == 1
    assert events[0].correlation_id == response.headers["X-Correlation-ID"]
    assert set(events[0].payload_json["decision_inputs"]) == set(DECISION_INPUT_KEYS)


def test_reject_schedules_lifecycle_sync_trigger(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reject endpoint must fire the same debounced sync trigger
    the approve path uses, so the funnel metric view reflects
    rejected-borrower counts through the event-triggered lifecycle path
    or an explicit Admin Data operations repair run."""
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    # R6-19: fetchone runs on every request now (server-derived fallback
    # key), so pin it to None for the no-prior-approval path.
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure

    calls: list[dict[str, Any]] = []

    def _spy(background: Any, *, reason: str = "approval") -> None:
        calls.append({"reason": reason})

    monkeypatch.setattr(outreach_mod, "enqueue_lifecycle_trigger", _spy)
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/reject",
        json={"borrower_id": "B-48291", "actor": "anonymous", "rationale_code": "low_intent"},
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    # The reason should identify this as a reject so downstream
    # structured logs can distinguish approve vs reject triggers.
    assert calls[0]["reason"] == "rejection"


def test_reject_surfaces_503_on_lakebase_failure(override_deps) -> None:
    """A Lakebase execute() failure surfaces as 503, not a silent fall-
    back. Matches the approve endpoint's no-silent-fallback contract."""
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.execute.side_effect = LakebaseError("simulated postgres outage")
    # R6-19: server-derived fallback request_id always triggers lookup;
    # pin fetchone to None so the INSERT path is the one that fails.
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/reject",
        json={"borrower_id": "B-48291", "actor": "anonymous", "rationale_code": "low_intent"},
    )
    assert resp.status_code == 503, resp.text
    # Audit store should not have recorded anything if the
    # decision-row insert failed first.
    assert audit.list(limit=10) == []


def test_approve_forwards_final_subject_and_body_into_audit_metadata(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit finding 2026-04-22: the Offer Orchestrator now forwards
    the approver's edited textarea body on approve. It must land in the
    audit metadata so compliance can reconstruct the released copy."""
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    # R6-19: server-derived fallback request_id path now runs the lookup.
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure

    # Silence the lifecycle-sync trigger so this test doesn't import
    # the real SDK.
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    draft = APPROVAL_DRAFT_BODY
    resp = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "actor": "anonymous",
            "draft_subject": "Your mortgage review",
            "draft_body": draft,
        },
    )
    assert resp.status_code == 200, resp.text
    events = audit.list(limit=5)
    assert len(events) == 1
    assert events[0].payload_json.get("draft_subject") == "Your mortgage review"
    assert events[0].payload_json.get("draft_body") == draft


@pytest.mark.parametrize(
    "protected_copy",
    [
        "Women homeowners",
        "African American homeowners",
        "Latina homeowners",
        "Catholic homeowners",
        "Widowed homeowners",
    ],
)
def test_approve_rejects_protected_class_language_before_write(
    protected_copy: str,
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    response = TestClient(app).post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "draft_subject": "Your mortgage review",
            "draft_body": f"{protected_copy} should call for a review. {DISCLOSURE_BODY}",
        },
    )

    assert response.status_code == 422
    assert "protected-class" in response.json()["detail"]
    assert audit.list(limit=5) == []


@pytest.mark.parametrize(
    "unsafe_copy",
    [
        "You are pre-approved. Review your options today.",
        "Ask about our best rate. Schedule a review.",
        "Act now before this limited-time opportunity expires. Call today.",
        "Senior citizens should contact us for a review.",
        "We prioritize borrowers by source of income. Schedule a review.",
        "Contact John Smith to discuss your options.",
        "contact john smith to discuss your options.",
        "CONTACT JOHN SMITH to discuss your options.",
    ],
)
def test_approve_rejects_unsupported_or_identity_shaped_borrower_copy_before_write(
    unsafe_copy: str,
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    response = TestClient(app).post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "draft_subject": "Your mortgage review",
            "draft_body": f"{unsafe_copy} {DISCLOSURE_BODY}",
        },
    )

    assert response.status_code == 422, response.text
    assert audit.list(limit=5) == []


# ---------------------------------------------------------------------------
# R5-01 idempotency contract
# ---------------------------------------------------------------------------


def test_approve_idempotent_on_retry_with_same_request_id(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R5-01: a second /approve with the same ``request_id`` must NOT
    write a second row. The first call inserts; the second looks up the
    existing approval via the partial unique index and short-circuits
    before INSERT + before emitting a duplicate audit event.
    """
    audit = InMemoryAuditStore()

    # A stateful fake: track what was "inserted" so fetchone can return
    # the existing approval_id on the second call, the way the real
    # Postgres lookup would.
    inserted: dict[str, dict[str, Any]] = {}

    def _execute(sql: str, params: dict[str, Any]) -> None:
        _record_non_atomic_approval(inserted, sql, params)

    def _fetchone(sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "FROM mip_app.tenant_disclosures" in sql:
            return _disclosure_row(params)
        if "WHERE request_id" in sql:
            rid = params.get("request_id")
            if rid and rid in inserted:
                return inserted[rid]
            return None
        return None

    fake_lakebase = MagicMock()
    fake_lakebase.execute.side_effect = _execute
    fake_lakebase.fetchone.side_effect = _fetchone

    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    body = {
        "borrower_id": "B-48291",
        "actor": "anonymous",
        "request_id": "22222222-2222-4222-8222-222222222222",
        "draft_subject": "Your mortgage review",
        "draft_body": APPROVAL_DRAFT_BODY,
    }
    first = client.post("/api/outreach/approve", json=body)
    repo = app.dependency_overrides[get_outreach_repository]()
    borrower_lookup = MagicMock(side_effect=AssertionError("replay queried mutable UC borrower"))
    monkeypatch.setattr(repo, "find_borrower", borrower_lookup)
    second = client.post("/api/outreach/approve", json=body)
    payload_conflict = client.post(
        "/api/outreach/approve",
        json={**body, "rationale": "A different reviewed decision"},
    )
    actor_conflict = client.post(
        "/api/outreach/approve",
        json=body,
        headers={"X-Forwarded-Email": "other@example.com"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert payload_conflict.status_code == 409, payload_conflict.text
    assert actor_conflict.status_code == 409, actor_conflict.text
    # Same approval_id returned on both calls -- the idempotency
    # contract's observable guarantee.
    assert first.json()["approval_id"] == second.json()["approval_id"]
    borrower_lookup.assert_not_called()

    # Exactly one INSERT -- the second call short-circuited.
    approval_inserts = [
        call
        for call in fake_lakebase.execute.call_args_list
        if "INSERT INTO mip_app.approvals" in call.args[0]
    ]
    assert len(approval_inserts) == 1

    # Exactly one audit event -- the second call must not emit a
    # duplicate APPROVE row into the ledger.
    events = audit.list(limit=5)
    assert len([e for e in events if e.event_type == "APPROVE"]) == 1


def test_reject_idempotent_on_retry_with_same_request_id(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject-path twin of the approve idempotency test above."""
    audit = InMemoryAuditStore()
    inserted: dict[str, dict[str, Any]] = {}

    def _execute(sql: str, params: dict[str, Any]) -> None:
        _record_non_atomic_approval(inserted, sql, params)

    def _fetchone(sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "FROM mip_app.tenant_disclosures" in sql:
            return _disclosure_row(params)
        if "WHERE request_id" in sql:
            rid = params.get("request_id")
            if rid and rid in inserted:
                return inserted[rid]
        return None

    fake_lakebase = MagicMock()
    fake_lakebase.execute.side_effect = _execute
    fake_lakebase.fetchone.side_effect = _fetchone

    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="rejection": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    body = {
        "borrower_id": "B-48291",
        "actor": "anonymous",
        "rationale_code": "low_intent",
        "request_id": "33333333-3333-4333-8333-333333333333",
    }
    first = client.post("/api/outreach/reject", json=body)
    repo = app.dependency_overrides[get_outreach_repository]()
    borrower_lookup = MagicMock(side_effect=AssertionError("replay queried mutable UC borrower"))
    campaign_lookup = MagicMock(side_effect=AssertionError("replay queried mutable campaign state"))
    monkeypatch.setattr(repo, "find_borrower", borrower_lookup)
    monkeypatch.setattr(outreach_mod, "_resolve_governed_campaign_variant", campaign_lookup)
    second = client.post("/api/outreach/reject", json=body)
    payload_conflict = client.post(
        "/api/outreach/reject",
        json={**body, "rationale_code": "data_quality"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert payload_conflict.status_code == 409, payload_conflict.text
    assert first.json()["approval_id"] == second.json()["approval_id"]
    campaign_lookup.assert_not_called()
    borrower_lookup.assert_not_called()

    approval_inserts = [
        call
        for call in fake_lakebase.execute.call_args_list
        if "INSERT INTO mip_app.approvals" in call.args[0]
    ]
    assert len(approval_inserts) == 1
    assert len([e for e in audit.list(limit=5) if e.event_type == "OUTREACH_REJECT"]) == 1


def test_request_id_conflict_for_different_decision_is_rejected(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.return_value = {
        "approval_id": "11111111-1111-1111-1111-111111111111",
        "borrower_id": "B-48294",
        "action": "approve",
        "actor_email": "other@example.com",
    }
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "request_id": "44444444-4444-4444-8444-444444444444",
            "draft_subject": "Your mortgage review",
            "draft_body": APPROVAL_DRAFT_BODY,
        },
        headers={"X-Forwarded-Email": "lo@example.com"},
    )

    assert response.status_code == 409
    assert "different outreach decision" in response.json()["detail"]
    fake_lakebase.execute.assert_not_called()
    assert audit.list(limit=10) == []


@pytest.mark.parametrize(
    ("path", "payload", "response_flag"),
    [
        (
            "/api/outreach/approve",
            {
                "borrower_id": "B-48291",
                "offer_code": "heloc",
                "channel": "email",
                "rationale": "Reviewed by the operator",
                "draft_subject": "Your mortgage review",
                "draft_body": APPROVAL_DRAFT_BODY,
                "assigned_to_email": "lo01@summit.example",
                "follow_up_in_days": 3,
                "request_id": "88888888-8888-4888-8888-888888888881",
            },
            "approved",
        ),
        (
            "/api/outreach/reject",
            {
                "borrower_id": "B-48291",
                "offer_code": "heloc",
                "channel": "email",
                "rationale_code": "low_intent",
                "rationale": "Reviewed by the operator",
                "request_id": "88888888-8888-4888-8888-888888888882",
            },
            "rejected",
        ),
    ],
)
def test_concurrent_decision_replay_returns_complete_winner_once(
    path: str,
    payload: dict[str, Any],
    response_flag: str,
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lakebase = _ConcurrentDecisionLakebase()
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=InMemoryAuditStore(), lakebase=lakebase)
    headers = {"X-Forwarded-Email": "lo@example.com"}

    def _post(_: int) -> Any:
        return TestClient(app).post(path, json=payload, headers=headers)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(_post, range(8)))

    assert {response.status_code for response in responses} == {200}
    bodies = [response.json() for response in responses]
    assert all(body == bodies[0] for body in bodies)
    assert bodies[0][response_flag] is True
    assert bodies[0]["approval_id"]
    assert bodies[0]["audit_event_id"]
    if response_flag == "approved":
        assert bodies[0]["assigned_to_email"] == "lo01@summit.example"
        assert bodies[0]["follow_up_at"] is not None
    assert len(lakebase.approvals) == 1
    assert lakebase.audit_count == 1

    mismatch = dict(payload)
    if response_flag == "approved":
        mismatch["rationale"] = "A different reviewed decision"
    else:
        mismatch["rationale_code"] = "data_quality"
    conflict = TestClient(app).post(path, json=mismatch, headers=headers)
    assert conflict.status_code == 409
    assert "different outreach decision" in conflict.json()["detail"]
    assert len(lakebase.approvals) == 1
    assert lakebase.audit_count == 1


def test_fallback_request_id_binds_the_full_decision_payload(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lakebase = _ConcurrentDecisionLakebase()
    monkeypatch.setattr(outreach_mod.time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=InMemoryAuditStore(), lakebase=lakebase)
    base = {
        "borrower_id": "B-48291",
        "draft_subject": "Your mortgage review",
        "draft_body": APPROVAL_DRAFT_BODY,
    }
    headers = {"X-Forwarded-Email": "lo@example.com"}

    first = TestClient(app).post(
        "/api/outreach/approve",
        json={**base, "rationale": "First reviewed intent"},
        headers=headers,
    )
    second = TestClient(app).post(
        "/api/outreach/approve",
        json={**base, "rationale": "Second reviewed intent"},
        headers=headers,
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["approval_id"] != second.json()["approval_id"]
    assert len(lakebase.approvals) == 2
    assert len(set(lakebase.approvals)) == 2
    assert lakebase.audit_count == 2


def test_approve_without_request_id_same_minute_collapses_to_one_row(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-19: legacy callers that omit ``request_id`` get a server-derived
    deterministic fallback keyed on (actor, borrower, action, minute).

    Two same-minute POSTs from the same actor for the same borrower now
    collapse to one row (matches operator intent: a double-click should
    not double-book). Cross-minute retries stay distinct -- the test
    below covers that.
    """
    audit = InMemoryAuditStore()
    inserted: dict[str, dict[str, Any]] = {}

    def _execute(sql: str, params: dict[str, Any]) -> None:
        _record_non_atomic_approval(inserted, sql, params)

    def _fetchone(sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "FROM mip_app.tenant_disclosures" in sql:
            return _disclosure_row(params)
        if "WHERE request_id" in sql:
            rid = params.get("request_id")
            if rid and rid in inserted:
                return inserted[rid]
        return None

    fake_lakebase = MagicMock()
    fake_lakebase.execute.side_effect = _execute
    fake_lakebase.fetchone.side_effect = _fetchone

    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    # Pin the clock so both POSTs land in the same minute-bucket.
    monkeypatch.setattr(outreach_mod.time, "time", lambda: 1_700_000_000.0)
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    body = {
        "borrower_id": "B-48291",
        "draft_subject": "Your mortgage review",
        "draft_body": APPROVAL_DRAFT_BODY,
    }
    headers = {"X-Forwarded-Email": "lo@example.com"}
    first = client.post("/api/outreach/approve", json=body, headers=headers)
    second = client.post("/api/outreach/approve", json=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    # Same-minute duplicates collapse to one approval_id.
    assert first.json()["approval_id"] == second.json()["approval_id"]
    # Exactly one INSERT -- the second call short-circuited via the
    # server-derived fallback key.
    approval_inserts = [
        call
        for call in fake_lakebase.execute.call_args_list
        if "INSERT INTO mip_app.approvals" in call.args[0]
    ]
    assert len(approval_inserts) == 1


def test_approve_without_request_id_cross_minute_produces_two_rows(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6-19 cross-minute contract: an explicit re-submit ~60s later
    intentionally opens a new decision -- the fallback key changes
    bucket so the second POST writes a distinct row.
    """
    audit = InMemoryAuditStore()
    inserted: dict[str, dict[str, Any]] = {}

    def _execute(sql: str, params: dict[str, Any]) -> None:
        _record_non_atomic_approval(inserted, sql, params)

    def _fetchone(sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if "FROM mip_app.tenant_disclosures" in sql:
            return _disclosure_row(params)
        if "WHERE request_id" in sql:
            rid = params.get("request_id")
            if rid and rid in inserted:
                return inserted[rid]
        return None

    fake_lakebase = MagicMock()
    fake_lakebase.execute.side_effect = _execute
    fake_lakebase.fetchone.side_effect = _fetchone

    # Start clock, bump 61s between calls.
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(outreach_mod.time, "time", lambda: clock["t"])
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    body = {
        "borrower_id": "B-48291",
        "draft_subject": "Your mortgage review",
        "draft_body": APPROVAL_DRAFT_BODY,
    }
    headers = {"X-Forwarded-Email": "lo@example.com"}
    first = client.post("/api/outreach/approve", json=body, headers=headers)
    clock["t"] += 61.0  # bump to the next minute bucket
    second = client.post("/api/outreach/approve", json=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["approval_id"] != second.json()["approval_id"]
    approval_inserts = [
        call
        for call in fake_lakebase.execute.call_args_list
        if "INSERT INTO mip_app.approvals" in call.args[0]
    ]
    assert len(approval_inserts) == 2


# ---------------------------------------------------------------------------
# R5-23 -- request bodies must never leak into logs
# ---------------------------------------------------------------------------


def test_approve_body_not_in_logs(
    override_deps,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5-23: if anyone ever cranks log level to DEBUG, request bodies
    must not leak to stdout / log fixtures. We POST a draft_body
    carrying a distinctive marker and assert NO captured log record
    contains it.

    Covers: the audit write accepts the body (via scrub_free_text) and
    structured logs carry the event metadata, but neither the request
    body nor ``str(exc)`` from a LakebaseError should ever emit the raw
    marker string.
    """
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure

    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    # Distinctive sentinel that could not appear in any legitimate log
    # line -- if it does, a log path is echoing the request body.
    sentinel = "test-secret-leak-canary-r5-23"
    client = TestClient(app)

    import logging as _logging

    caplog.set_level(_logging.DEBUG)
    resp = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "actor": f"{sentinel}@example.com",
            "draft_subject": "Your mortgage review",
            "draft_body": f"{sentinel} {APPROVAL_DRAFT_BODY}",
        },
    )
    assert resp.status_code == 200, resp.text

    # No captured log record's raw message or formatted text may
    # contain the sentinel. The sentinel DOES land in the audit
    # ledger (by design -- that's governance-reconstructable copy),
    # but the ledger is not a log fixture.
    for record in caplog.records:
        assert sentinel not in record.getMessage(), (
            f"leaked request body into log: {record.name} {record.levelname} "
            f"{record.getMessage()!r}"
        )
        # Also check the structured extras the emit() helper attaches.
        for key, value in record.__dict__.items():
            if isinstance(value, str):
                assert (
                    sentinel not in value
                ), f"leaked request body into log extra {key!r}: {value!r}"


def test_generated_draft_fails_closed_when_durable_proof_cannot_be_written(
    override_deps,
) -> None:
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()
    fake_lakebase.fetchone.side_effect = _fetchone_none_or_disclosure
    override_deps(audit=audit, lakebase=fake_lakebase)

    response = TestClient(app).post(
        "/api/outreach/draft",
        json={"borrower_id": "B-48291", "channel": "email"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "lakebase is temporarily unavailable"
    assert audit.list(limit=10) == []
