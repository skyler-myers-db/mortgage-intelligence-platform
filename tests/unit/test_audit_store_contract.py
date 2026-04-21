"""Unit tests for ``backend.services.audit_store``.

Slice 5 swaps the in-memory store for a Lakebase-backed one. These
tests use an injected fake ``LakebaseClient`` so they never open a
real Postgres connection.

Assertions:

* ``write(...)`` issues an INSERT with the right columns + named params.
* ``list(limit=N)`` issues a SELECT with ``ORDER BY event_at DESC``
  and ``LIMIT N`` and hydrates AuditEvent rows.
* ``resolve_actor`` reads ``X-Forwarded-Email`` and falls back to
  ``settings.default_actor`` with a WARNING on absent header.
* ``_coerce_event_type`` upper-cases dotted actions for governance §4
  canonical-verb alignment.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from backend.config.settings import settings
from backend.services import audit_store as audit_mod
from backend.services.audit_store import (
    InMemoryAuditStore,
    LakebaseAuditStore,
    _coerce_event_type,
    resolve_actor,
)


class _FakeRequest:
    """Minimal shape the router's Request parameter provides to resolve_actor."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _build_client_returning_row() -> MagicMock:
    client = MagicMock()
    client.fetchone.return_value = {
        "audit_id": uuid4(),
        "event_at": datetime.now(UTC),
    }
    client.fetchall.return_value = []
    return client


def test_write_issues_insert_with_named_params() -> None:
    client = _build_client_returning_row()
    store = LakebaseAuditStore(client=client)

    event = store.write(
        actor="skyler@entrada.ai",
        action="view_borrower_360",
        entity_type="borrower",
        entity_id="B-48291",
        payload_json={"score": 92},
        evidence_ids=["ev-1", "ev-2"],
        event_type="VIEW_BORROWER",
        subject_clip="abc123def456",
    )

    # One INSERT call, named-binding only.
    assert client.fetchone.call_count == 1
    sql, params = client.fetchone.call_args[0]
    assert "INSERT INTO mip_app.action_audit" in sql
    assert "RETURNING" in sql
    # Every expected column parameter is present.
    for key in (
        "event_type",
        "actor_email",
        "entity_type",
        "entity_id",
        "subject_clip",
        "subject_segment",
        "request_id",
        "evidence_ids",
        "metadata",
    ):
        assert key in params, f"missing bind param: {key}"
    assert params["actor_email"] == "skyler@entrada.ai"
    assert params["event_type"] == "VIEW_BORROWER"
    assert params["subject_clip"] == "abc123def456"
    assert params["evidence_ids"] == ["ev-1", "ev-2"]
    assert "score" in params["metadata"]  # JSON-serialized
    # Round-tripped AuditEvent.
    assert event.actor == "skyler@entrada.ai"
    assert event.event_type == "VIEW_BORROWER"
    assert event.subject_clip == "abc123def456"


def test_list_issues_select_ordered_desc_with_limit() -> None:
    client = _build_client_returning_row()
    # fetchall returns rows in whatever the SELECT produces -- we assert
    # on the query shape.
    client.fetchall.return_value = []
    store = LakebaseAuditStore(client=client)

    events = store.list(limit=25)

    assert events == []
    assert client.fetchall.call_count == 1
    args, kwargs = client.fetchall.call_args
    sql = args[0]
    params = args[1]
    assert "ORDER BY event_at DESC" in sql
    assert "LIMIT %(limit)s" in sql
    assert params == {"limit": 25}
    # belt-and-suspenders: fetchall is called with limit=25 too
    assert kwargs.get("limit", args[2] if len(args) > 2 else None) == 25


def test_list_hydrates_rows_into_audit_events() -> None:
    client = MagicMock()
    client.fetchone.return_value = None
    now = datetime.now(UTC)
    aid = uuid4()
    client.fetchall.return_value = [
        {
            "audit_id": aid,
            "event_type": "VIEW_BORROWER",
            "actor_email": "skyler@entrada.ai",
            "entity_type": "borrower",
            "entity_id": "B-48291",
            "subject_clip": "abc123",
            "subject_segment": None,
            "request_id": None,
            "evidence_ids": ["ev-1"],
            "metadata": {"action": "view_borrower_360", "score": 92},
            "event_at": now,
        }
    ]
    store = LakebaseAuditStore(client=client)

    events = store.list(limit=10)

    assert len(events) == 1
    e = events[0]
    assert e.event_id == str(aid)
    assert e.action == "view_borrower_360"
    assert e.event_type == "VIEW_BORROWER"
    assert e.subject_clip == "abc123"
    assert e.evidence_ids == ["ev-1"]
    # metadata "action" key is popped so it doesn't duplicate at
    # payload_json; the score survives.
    assert e.payload_json == {"score": 92}
    assert e.created_at == now.isoformat()


def test_resolve_actor_reads_x_forwarded_email() -> None:
    req = _FakeRequest({"X-Forwarded-Email": "alice@example.com"})
    assert resolve_actor(req) == "alice@example.com"


def test_resolve_actor_falls_back_to_default_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    req = _FakeRequest({})
    with caplog.at_level(logging.WARNING, logger="backend.services.audit_store"):
        actor = resolve_actor(req)
    assert actor == settings.default_actor
    assert any(
        "no X-Forwarded-Email" in rec.getMessage()
        for rec in caplog.records
    )


def test_resolve_actor_handles_null_request() -> None:
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(settings, "default_actor", "fallback@example.com")
        assert resolve_actor(None) == "fallback@example.com"


def test_coerce_event_type_preserves_explicit_value() -> None:
    assert _coerce_event_type("APPROVE", "outreach.approve") == "APPROVE"


def test_coerce_event_type_derives_from_action_when_missing() -> None:
    # Governance §4 wants upper snake-case canonical verbs.
    assert _coerce_event_type(None, "outreach.approve") == "OUTREACH_APPROVE"
    assert _coerce_event_type(None, "view-borrower-360") == "VIEW_BORROWER_360"


def test_in_memory_store_is_a_drop_in_for_the_protocol() -> None:
    store = InMemoryAuditStore()
    e = store.write(
        actor="a@b",
        action="view_borrower_360",
        entity_type="borrower",
        entity_id="B-1",
    )
    out = store.list(limit=50)
    assert out == [e]


def test_get_audit_store_returns_the_lakebase_impl_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In a non-test process the factory constructs LakebaseAuditStore.

    We reset the singleton and stub ``get_lakebase_client`` so the
    constructor doesn't try to open a real connection.
    """
    audit_mod._reset_audit_store_for_tests()
    stub_client: Any = MagicMock()
    monkeypatch.setattr(audit_mod, "get_lakebase_client", lambda: stub_client)
    try:
        store = audit_mod.get_audit_store()
        assert isinstance(store, LakebaseAuditStore)
    finally:
        audit_mod._reset_audit_store_for_tests()
