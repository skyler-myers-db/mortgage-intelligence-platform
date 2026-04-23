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

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api import outreach as outreach_mod
from backend.main import app
from backend.services import job_trigger
from backend.services.audit_store import InMemoryAuditStore, get_audit_store
from backend.services.lakebase import LakebaseError, get_lakebase_client
from backend.services.resilience import _reset_breakers_for_tests


@pytest.fixture(autouse=True)
def _reset_trigger_state():
    """Isolate the debounce cache across tests so each reject fires.

    Also resets the process-wide circuit breakers before AND after
    each test. The 503 test below intentionally raises 5 Lakebase
    failures to exercise the no-silent-fallback path; without this
    reset the 'lakebase' breaker stays OPEN and poisons every later
    test in the session that touches Lakebase.
    """
    _reset_breakers_for_tests()
    job_trigger._reset_for_tests()
    yield
    _reset_breakers_for_tests()


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
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/reject",
        json={
            "borrower_id": "B-48291",
            "offer_code": "HELOC-STD",
            "actor": "lo@example.com",
            "evidence_ids": ["ev-1", "ev-2"],
            "rationale": "Borrower opted out",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"] is True
    assert body["approval_id"]  # non-empty UUID string
    assert body["audit_event_id"]

    # mip_app.approvals write: exactly one execute() call with
    # action='reject' and our actor.
    assert fake_lakebase.execute.call_count == 1
    sql, params = fake_lakebase.execute.call_args.args
    assert "INSERT INTO mip_app.approvals" in sql
    assert params["action"] == "reject"
    assert params["actor_email"] == "lo@example.com"
    assert params["borrower_id"] == "B-48291"
    assert params["offer_code"] == "HELOC-STD"
    assert params["rationale"] == "Borrower opted out"
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
    assert evt.evidence_ids == ["ev-1", "ev-2"]
    assert evt.payload_json["borrower_id"] == "B-48291"
    assert evt.payload_json["offer_code"] == "HELOC-STD"
    assert evt.payload_json["rationale"] == "Borrower opted out"


def test_reject_schedules_lifecycle_sync_trigger(
    override_deps, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reject endpoint must fire the same debounced sync trigger
    the approve path uses, so the funnel metric view reflects
    rejected-borrower counts without waiting on the daily cron."""
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()

    calls: list[dict[str, Any]] = []

    def _spy(background: Any, *, reason: str = "approval") -> None:
        calls.append({"reason": reason})

    monkeypatch.setattr(outreach_mod, "enqueue_lifecycle_trigger", _spy)
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/reject",
        json={"borrower_id": "B-48291", "actor": "anonymous"},
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
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/reject",
        json={"borrower_id": "B-48291", "actor": "anonymous"},
    )
    assert resp.status_code == 503, resp.text
    # Audit store should not have recorded anything if the
    # decision-row insert failed first.
    assert audit.list(limit=10) == []


def test_approve_forwards_draft_body_into_audit_metadata(
    override_deps, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit finding 2026-04-22: the Offer Orchestrator now forwards
    the approver's edited textarea body on approve. It must land in the
    audit metadata so compliance can reconstruct the released copy."""
    audit = InMemoryAuditStore()
    fake_lakebase = MagicMock()

    # Silence the lifecycle-sync trigger so this test doesn't import
    # the real SDK.
    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )
    override_deps(audit=audit, lakebase=fake_lakebase)

    client = TestClient(app)
    draft = "Hi [first name], custom edited outreach copy."
    resp = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "actor": "anonymous",
            "draft_body": draft,
        },
    )
    assert resp.status_code == 200, resp.text
    events = audit.list(limit=5)
    assert len(events) == 1
    assert events[0].payload_json.get("draft_body") == draft
