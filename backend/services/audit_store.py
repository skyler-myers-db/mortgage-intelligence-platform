"""Audit store -- Lakebase-backed append-only ledger.

Slice-5 cutover: replaces the in-memory list with a Postgres-backed
writer against ``mip_app.action_audit``. The router contract is
unchanged -- ``write(...)`` returns an ``AuditEvent`` and ``list(limit)``
returns events in descending event-time order.

No silent fallback: when Lakebase is unreachable, ``LakebaseAuditStore``
methods raise ``LakebaseError`` (from ``backend.services.lakebase``).
The audit router catches that and surfaces HTTP 503; Slice 6 adds the
retry / circuit-breaker layer so transient network hiccups don't panic
the demo.

Actor attribution: governance §4 requires the real authenticated user,
not ``"demo-user"``. Databricks Apps forwards the workspace user in
``X-Forwarded-Email``; ``resolve_actor(request)`` extracts it and falls
back to ``settings.default_actor`` with a logged warning so operators
can spot dev/test paths in production logs. The router chain is:
routers read ``Request`` -> call ``resolve_actor`` -> pass to
``audit_store.write(actor=...)``.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from fastapi import Request

from backend.config.settings import settings
from backend.schemas.audit import AuditEvent
from backend.services.lakebase import LakebaseClient, get_lakebase_client

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Protocol -- routers depend on this, not on a concrete class.
# ----------------------------------------------------------------------


@runtime_checkable
class AuditStore(Protocol):
    """Minimal audit surface. Kept narrow so swapping the backing store
    (in-memory for tests, Lakebase for production) is a factory edit.
    """

    def write(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_json: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        event_type: str | None = None,
        subject_clip: str | None = None,
        subject_segment: str | None = None,
        request_id: str | None = None,
    ) -> AuditEvent: ...

    def list(self, limit: int = 50) -> list[AuditEvent]: ...


# ----------------------------------------------------------------------
# Shared helpers.
# ----------------------------------------------------------------------


def resolve_actor(request: Request | None) -> str:
    """Read the workspace identity forwarded by Databricks Apps.

    Logs a WARNING when the header is absent so operators can spot
    dev/test traffic in production logs. The fallback value is
    ``settings.default_actor`` so audit rows are never written with
    ``"demo-user"`` or NULL in the authenticated actor column.
    """
    if request is not None:
        email = request.headers.get("X-Forwarded-Email")
        if email:
            return email
        user = request.headers.get("X-Forwarded-User")
        if user:
            return user
    log.warning(
        "audit_store.resolve_actor: no X-Forwarded-Email header -- "
        "falling back to settings.default_actor=%s",
        settings.default_actor,
    )
    return settings.default_actor


def _coerce_event_type(event_type: str | None, action: str) -> str:
    """Governance §4 wants canonical verbs (VIEW_BORROWER / APPROVE /
    DRAFT_OUTREACH / RECOMMEND_OFFER / RUN_GENIE / VIEW_LEADS). When a
    caller passes only ``action`` (the pre-Slice-5 contract), we
    upper-case it and replace the dot separator. e.g.
    ``"outreach.approve"`` -> ``"OUTREACH_APPROVE"``.
    """
    if event_type:
        return event_type
    return action.replace(".", "_").replace("-", "_").upper()


# ----------------------------------------------------------------------
# In-memory store -- kept for unit tests and as a reference impl. The
# production factory always returns ``LakebaseAuditStore``; tests that
# want a fast, no-network audit surface instantiate this directly and
# inject via FastAPI dependency_overrides.
# ----------------------------------------------------------------------


class InMemoryAuditStore:
    """Deterministic in-memory audit ledger. Tests only."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def write(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_json: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        event_type: str | None = None,
        subject_clip: str | None = None,
        subject_segment: str | None = None,
        request_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=f"evt-{uuid4().hex[:12]}",
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json or {},
            evidence_ids=evidence_ids or [],
            created_at=datetime.now(UTC).isoformat(),
            event_type=_coerce_event_type(event_type, action),
            subject_clip=subject_clip,
            subject_segment=subject_segment,
            request_id=request_id,
        )
        self._events.append(event)
        return event

    def list(self, limit: int = 50) -> list[AuditEvent]:
        return list(reversed(self._events[-limit:]))


# ----------------------------------------------------------------------
# Lakebase-backed store -- production path.
# ----------------------------------------------------------------------


_INSERT_SQL = """
INSERT INTO mip_app.action_audit (
    event_type, actor_email, entity_type, entity_id,
    subject_clip, subject_segment, request_id,
    evidence_ids, metadata
) VALUES (
    %(event_type)s, %(actor_email)s, %(entity_type)s, %(entity_id)s,
    %(subject_clip)s, %(subject_segment)s, %(request_id)s,
    %(evidence_ids)s, %(metadata)s::jsonb
)
RETURNING audit_id, event_at
"""

_SELECT_SQL = """
SELECT audit_id, event_type, actor_email, entity_type, entity_id,
       subject_clip, subject_segment, request_id,
       evidence_ids, metadata, event_at
FROM mip_app.action_audit
ORDER BY event_at DESC
LIMIT %(limit)s
"""


class LakebaseAuditStore:
    """Audit store backed by the Lakebase ``mip_app.action_audit`` table.

    Each ``write`` opens a short transaction, INSERTs one row, and
    returns the AuditEvent with the server-assigned UUID + timestamp.
    ``list`` selects ORDER BY event_at DESC LIMIT N.
    """

    def __init__(self, client: LakebaseClient | None = None) -> None:
        # Accept an injected client for unit testability; default to
        # the process-singleton.
        self._client = client or get_lakebase_client()

    def write(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload_json: dict[str, Any] | None = None,
        evidence_ids: list[str] | None = None,
        event_type: str | None = None,
        subject_clip: str | None = None,
        subject_segment: str | None = None,
        request_id: str | None = None,
    ) -> AuditEvent:
        payload = payload_json or {}
        # Governance §4: metadata JSONB gets the "action" verb + any
        # caller-supplied payload, but we never smuggle PII in here --
        # routers that pass ``payload_json`` are expected to have
        # already stripped borrower names / addresses. Callers who pass
        # a score + thresholds bundle are safe.
        metadata = {"action": action, **payload}
        params: dict[str, Any] = {
            "event_type": _coerce_event_type(event_type, action),
            "actor_email": actor,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "subject_clip": subject_clip,
            "subject_segment": subject_segment,
            "request_id": request_id,
            "evidence_ids": list(evidence_ids or []),
            "metadata": json.dumps(metadata),
        }
        row = self._client.fetchone(_INSERT_SQL, params)
        if row is None:
            # Should be impossible with RETURNING, but guard anyway --
            # the Protocol promises an AuditEvent, not None.
            raise RuntimeError("Lakebase INSERT returned no row")
        return AuditEvent(
            event_id=str(row["audit_id"]),
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload,
            evidence_ids=list(evidence_ids or []),
            created_at=row["event_at"].isoformat(),
            event_type=params["event_type"],
            subject_clip=subject_clip,
            subject_segment=subject_segment,
            request_id=request_id,
        )

    def list(self, limit: int = 50) -> list[AuditEvent]:
        rows = self._client.fetchall(_SELECT_SQL, {"limit": limit}, limit=limit)
        out: list[AuditEvent] = []
        for row in rows:
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                # psycopg usually returns JSONB as dict, but fall back.
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}
            action = metadata.pop("action", row["event_type"])
            out.append(
                AuditEvent(
                    event_id=str(row["audit_id"]),
                    actor=row["actor_email"],
                    action=action,
                    entity_type=row.get("entity_type") or "",
                    entity_id=row.get("entity_id") or "",
                    payload_json=metadata,
                    evidence_ids=list(row.get("evidence_ids") or []),
                    created_at=row["event_at"].isoformat(),
                    event_type=row["event_type"],
                    subject_clip=row.get("subject_clip"),
                    subject_segment=row.get("subject_segment"),
                    request_id=row.get("request_id"),
                )
            )
        return out


# ----------------------------------------------------------------------
# Factory -- single choke point for the FastAPI dependency graph.
# ----------------------------------------------------------------------


_AUDIT_STORE: AuditStore | None = None


def get_audit_store() -> AuditStore:
    """Lazy process-singleton audit store.

    Production path constructs a ``LakebaseAuditStore`` backed by the
    Lakebase client singleton. Tests override this factory in
    ``tests/conftest.py`` with an ``InMemoryAuditStore`` so unit tests
    never touch Postgres.
    """
    global _AUDIT_STORE
    if _AUDIT_STORE is None:
        _AUDIT_STORE = LakebaseAuditStore()
    return _AUDIT_STORE


def _reset_audit_store_for_tests() -> None:
    """Test helper -- drop the cached audit store so factory overrides stick."""
    global _AUDIT_STORE
    _AUDIT_STORE = None


# ----------------------------------------------------------------------
# Legacy shim -- the pre-Slice-5 audit router imported a module-level
# ``audit_store`` instance. We keep the name for back-compat but route
# all reads / writes through ``get_audit_store()`` so test overrides on
# the factory still take effect. The audit router migrates to the
# factory in this slice; the shim exists only so any stale import
# doesn't explode during the deploy window.
# ----------------------------------------------------------------------


class _AuditStoreProxy:
    def write(self, **kwargs: Any) -> AuditEvent:
        return get_audit_store().write(**kwargs)

    def list(self, limit: int = 50) -> list[AuditEvent]:
        return get_audit_store().list(limit=limit)


audit_store: AuditStore = _AuditStoreProxy()
