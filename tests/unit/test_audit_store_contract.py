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

import inspect
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services import audit_lakebase_store as lakebase_audit_mod
from backend.services import audit_store as audit_mod
from backend.services.audit_lakebase_store import LakebaseAuditStore
from backend.services.audit_store import (
    _coerce_event_type,
    _reset_fallback_counter_for_tests,
    get_fallback_identity_count,
    resolve_actor,
)
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore


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
        # R6-20 allowlist: ``opportunity_score`` is an approved metadata
        # key; ``score`` (used previously here) is not -- the allowlist
        # is deliberately narrow so reviewers explicitly sign off on
        # every new key. See ``_ALLOWED_METADATA_KEYS`` in audit_store.py.
        payload_json={"opportunity_score": 92},
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
        "correlation_id",
        "evidence_ids",
        "metadata",
    ):
        assert key in params, f"missing bind param: {key}"
    assert params["actor_email"] == "skyler@entrada.ai"
    assert params["event_type"] == "VIEW_BORROWER"
    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", params["subject_clip"])
    assert params["correlation_id"]
    assert params["evidence_ids"] == ["ev-1", "ev-2"]
    assert "opportunity_score" in params["metadata"]  # JSON-serialized
    # Round-tripped AuditEvent.
    assert event.actor == "skyler@entrada.ai"
    assert event.event_type == "VIEW_BORROWER"
    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", event.subject_clip or "")


def test_action_audit_schema_has_statement_level_append_only_trigger() -> None:
    """The ledger must reject UPDATE/DELETE even if the runtime role owns the table."""

    schema_sql = Path("lakebase/schema.sql").read_text(encoding="utf-8")

    assert "CREATE OR REPLACE FUNCTION mip_app.prevent_action_audit_mutation()" in schema_sql
    assert "RAISE EXCEPTION 'mip_app.action_audit is append-only" in schema_sql
    assert "USING ERRCODE = '42501'" in schema_sql
    assert "CREATE TRIGGER trg_action_audit_append_only" in schema_sql
    assert "BEFORE UPDATE OR DELETE ON mip_app.action_audit" in schema_sql
    assert "FOR EACH STATEMENT" in schema_sql
    assert "ADD COLUMN IF NOT EXISTS correlation_id TEXT" in schema_sql
    assert "CREATE INDEX IF NOT EXISTS idx_action_audit_correlation" in schema_sql


def test_every_backend_action_audit_insert_carries_correlation_id() -> None:
    """Every app-authored audit row must be joinable to request logs."""

    for path in Path("backend").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"INSERT\s+INTO\s+mip_app\.action_audit\s*\((?P<cols>.*?)\)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            cols = match.group("cols")
            assert "correlation_id" in cols, f"{path}:{match.start()} missing correlation_id"


_MUTATION_AUDIT_EXPECTATIONS: dict[str, tuple[str, ...]] = {
    # Explicit non-mutating POST/PUT surfaces.
    "put_rules": ("status_code=410", "Offer rules are governed"),
    "preview_portfolio": ("repo.preview",),
    "genie_start": ("_latest_genie_conversation",),
    "record_rum": ("mip_rum_enabled",),
    "post_force_degraded": ("audit.write(", "FORCE_DEGRADED"),
    "post_operation_run": ("_write_operation_audit(", "ADMIN_OPERATION_RUN"),
    # Governed writes or audit-emitting reads.
    "create_portfolio": ("repo.create(",),
    "patch_portfolio": ("repo.patch_status(",),
    "patch_campaign": ("repo.patch_status(",),
    "recommend_offer": ("_safe_audit_write",),
    "draft_outreach": ("_safe_audit_write",),
    "approve_outreach": ("_commit_outreach_decision_atomic(", "audit.write("),
    "reject_outreach": ("_commit_outreach_decision_atomic(", "audit.write("),
    "assign_lead": ("store.assign_lead(",),
    "distribute_leads": ("store.distribute(",),
    "log_disposition": ("store.log_disposition(",),
    "genie_message": ("_required_audit_write",),
    "genie_action": ("handle_genie_action(",),
    "log_event": ("store.write(",),
    "save_lead": ("store.save_lead(",),
    "delete_lead": ("store.delete_lead(",),
    "save_draft": ("store.save_draft(",),
    "delete_draft": ("store.delete_draft(",),
    "stage_activation": ("store.stage_borrower(", "_assert_activation_eligible("),
}


def test_every_mutation_route_has_audit_coverage_or_explicit_exemption() -> None:
    """New state-changing routes must update this reviewed audit manifest."""

    seen: set[object] = set()
    covered: set[str] = set()
    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        methods = getattr(route, "methods", set()) or set()
        if endpoint is None or endpoint in seen or not (methods & mutating):
            continue
        seen.add(endpoint)
        name = getattr(endpoint, "__name__", "")
        assert name in _MUTATION_AUDIT_EXPECTATIONS, (
            f"{getattr(route, 'path', '<unknown>')} maps to {name!r}; "
            "add an audit write or an explicit exemption to this manifest"
        )
        source = inspect.getsource(endpoint)
        missing = [
            token for token in _MUTATION_AUDIT_EXPECTATIONS[name] if token not in source
        ]
        assert not missing, f"{name} missing audit/exemption evidence tokens: {missing}"
        covered.add(name)

    assert set(_MUTATION_AUDIT_EXPECTATIONS) == covered


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


def test_list_can_filter_by_borrower_id_metadata_or_entity() -> None:
    client = _build_client_returning_row()
    client.fetchall.return_value = []
    store = LakebaseAuditStore(client=client)

    store.list(limit=25, borrower_id="B-102FL7THC6Q3L")

    sql, params = client.fetchall.call_args.args[:2]
    assert "metadata->>'borrower_id'" in sql
    assert "entity_id = %(borrower_id)s" in sql
    assert params["borrower_id"] == "B-102FL7THC6Q3L"


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
            "subject_clip": "clip_ref_abc123def456",
            "subject_segment": None,
            "request_id": None,
            "correlation_id": "audit-contract-cid",
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
    assert e.subject_clip == "clip_ref_abc123def456"
    assert e.correlation_id == "audit-contract-cid"
    assert e.evidence_ids == ["ev-1"]
    # metadata "action" key is popped so it doesn't duplicate at
    # payload_json; the score survives.
    assert e.payload_json == {"score": 92}
    assert e.created_at == now.isoformat()


def test_list_masks_legacy_raw_subject_clip_values() -> None:
    """Legacy audit rows may predate the public-demo masking pass.

    Listing audit events is still an API egress path, so hydration masks
    any stored raw subject_clip before returning the AuditEvent.
    """
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
            "subject_clip": "1234567890",
            "subject_segment": None,
            "request_id": None,
            "correlation_id": None,
            "evidence_ids": [],
            "metadata": {"action": "view_borrower_360"},
            "event_at": now,
        }
    ]
    store = LakebaseAuditStore(client=client)

    events = store.list(limit=10)

    assert len(events) == 1
    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", events[0].subject_clip or "")


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
        getattr(rec, "mip_event", None) == "identity_fallback"
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


def test_default_actor_emits_warning_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The fallback path emits a structured WARNING so operators see
    un-attributed calls in stdout JSON and OTLP logs. The record carries
    the ``mip_event`` field consumed by ``StructuredFormatter`` so
    filtering is cheap in the sink.
    """
    _reset_fallback_counter_for_tests()
    with caplog.at_level(logging.WARNING, logger="backend.services.audit_store"):
        resolve_actor(None)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected at least one WARNING record"
    assert any(
        getattr(rec, "mip_event", None) == "identity_fallback" for rec in warnings
    ), "expected structured event identity_fallback"


def test_default_actor_increments_counter() -> None:
    """Each fallback bumps the process-local counter monotonically."""
    _reset_fallback_counter_for_tests()
    assert get_fallback_identity_count() == 0
    resolve_actor(None)
    resolve_actor(None)
    resolve_actor(None)
    assert get_fallback_identity_count() == 3
    # A request with the header does NOT bump the counter.
    req = _FakeRequest({"X-Forwarded-Email": "alice@example.com"})
    resolve_actor(req)
    assert get_fallback_identity_count() == 3


def test_health_reports_fallback_counter() -> None:
    """``/api/admin/health`` exposes the counter as
    ``fallback_identity_fallbacks_process_total`` (R6-08 rename; legacy
    ``fallback_identity_fallbacks_total`` still emitted for one cycle)
    so operators can surface it in dashboards without an extra scrape
    target.

    R6-09/Tranche-1: the diagnostic body is admin-gated, so we send
    both actor and group headers.
    """
    _reset_fallback_counter_for_tests()
    # Bump it twice via the service-level API so the test doesn't depend
    # on internal ordering of other tests.
    resolve_actor(None)
    resolve_actor(None)
    client = TestClient(app)
    response = client.get(
        "/api/admin/health",
        headers={"X-Forwarded-Email": "ops@example.com", "X-Forwarded-Groups": "mip-admin"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # R6-08 canonical key.
    assert "fallback_identity_fallbacks_process_total" in body
    assert body["fallback_identity_fallbacks_process_total"] >= 2
    # Legacy key still emitted for one cycle.
    assert "fallback_identity_fallbacks_total" in body
    assert body["fallback_identity_fallbacks_total"] >= 2


def test_get_audit_store_returns_the_lakebase_impl_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In a non-test process the factory constructs LakebaseAuditStore.

    We reset the singleton and stub ``get_lakebase_client`` so the
    constructor doesn't try to open a real connection.
    """
    audit_mod._reset_audit_store_for_tests()
    stub_client: Any = MagicMock()
    monkeypatch.setattr(lakebase_audit_mod, "get_lakebase_client", lambda: stub_client)
    try:
        store = audit_mod.get_audit_store()
        assert isinstance(store, LakebaseAuditStore)
    finally:
        audit_mod._reset_audit_store_for_tests()
