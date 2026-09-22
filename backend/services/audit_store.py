"""Audit store -- Lakebase-backed append-only ledger.

Runtime writes through ``mip_app.action_audit``. ``resolve_actor`` reads the
Databricks Apps ``X-Forwarded-Email`` header and emits an observable fallback
warning when local/test paths use ``settings.default_actor``.

The metadata safety chain is split by responsibility: the reviewed
vocabulary and the typed violations live in ``audit_metadata_policy``, the
validators in ``audit_metadata_validation``, and the per-key public-value
assertion in ``audit_metadata_public_values``. This module keeps the store
itself -- ``build_safe_audit_metadata`` (the one entry point that runs the
whole chain), the ``AuditStore`` protocol, actor resolution, and the store
singleton -- and re-exports the chain, so ``backend.services.audit_store``
stays the single import surface for every caller.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from fastapi import Request

from backend.config.settings import settings
from backend.schemas.audit import AuditEvent as AuditEvent
from backend.schemas.common import validate_public_audit_action
from backend.services.audit_metadata_policy import (
    _ALLOWED_RESULT_FILTER_KEYS as _ALLOWED_RESULT_FILTER_KEYS,
)
from backend.services.audit_metadata_policy import (
    _RESULT_FILTER_NUMERIC_BOUNDS as _RESULT_FILTER_NUMERIC_BOUNDS,
)
from backend.services.audit_metadata_policy import (
    AuditMetadataValueViolation as AuditMetadataValueViolation,
)
from backend.services.audit_metadata_policy import (
    AuditMetadataViolation as AuditMetadataViolation,
)
from backend.services.audit_metadata_policy import (
    AuditPIIError as AuditPIIError,
)
from backend.services.audit_metadata_public_values import _assert_public_safe_values
from backend.services.audit_metadata_validation import (
    _assert_allowlisted,
    _assert_no_pii,
    _sanitize_metadata,
)
from backend.services.audit_metadata_validation import (
    _assert_result_filters_value_policy as _assert_result_filters_value_policy,
)
from backend.services.audit_metadata_validation import (
    _validate_top_level_audit_columns as _validate_top_level_audit_columns,
)
from backend.services.observability import emit

log = logging.getLogger(__name__)


# Process-local count of identity-header fallbacks. Tests exercise it via
# ``_reset_fallback_counter_for_tests`` + ``get_fallback_identity_count``.


_FALLBACK_IDENTITY_COUNT: int = 0


def get_fallback_identity_count() -> int:
    """Return the current process-local fallback-identity count."""
    return _FALLBACK_IDENTITY_COUNT


def _reset_fallback_counter_for_tests() -> None:
    """Test helper -- zero the counter between tests."""
    global _FALLBACK_IDENTITY_COUNT
    _FALLBACK_IDENTITY_COUNT = 0


def build_safe_audit_metadata(
    payload_json: dict[str, Any] | None,
    *,
    action: str,
) -> dict[str, Any]:
    """Return metadata after the shared audit safety policy has run.

    Most writes flow through ``LakebaseAuditStore.write``. A few Lakebase
    repository methods insert the business row and the audit row in one
    SQL statement for atomicity; those paths still need the same metadata
    scrub, denylist, allowlist, and value validation before binding JSONB.
    """

    validate_public_audit_action(action)
    metadata = _sanitize_metadata({**(payload_json or {}), "action": action})
    _assert_no_pii(metadata)
    _assert_allowlisted(metadata)
    _assert_public_safe_values(metadata)
    return metadata


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

    def list(
        self,
        limit: int = 50,
        *,
        offset: int = 0,
        after_sequence: int | None = None,
        snapshot_sequence: int | None = None,
        snapshot_token: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        entity_id: str | None = None,
        borrower_id: str | None = None,
        subject_clip: str | None = None,
        event_type: str | None = None,
        correlation_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditEvent]: ...


# ----------------------------------------------------------------------
# Shared helpers.
# ----------------------------------------------------------------------


_UNTRUSTED_EDGE_ACTOR: str = "unknown-actor@untrusted-edge"


def resolve_actor(request: Request | None) -> str:
    """Read the workspace identity forwarded by Databricks Apps.

    Logs a WARNING when the header is absent so operators can spot
    dev/test traffic in production logs. The fallback value is
    ``settings.default_actor`` so audit rows are never written with
    a placeholder string or NULL in the authenticated actor column.

    R5-09 trust boundary: when ``settings.trust_forwarded_headers`` is
    False we ignore ``X-Forwarded-Email`` / ``X-Forwarded-User`` entirely
    and attribute the row to a distinct marker string so an operator
    grepping audit rows can spot "this deploy does not trust the edge,
    actor is unknowable" at a glance. The default stays True because the
    Databricks Apps edge IS the authoritative identity stripper; the
    flag exists for unusual reverse-proxy deploys.
    """
    if request is not None and settings.trust_forwarded_headers:
        email = request.headers.get("X-Forwarded-Email")
        if email:
            return email
        user = request.headers.get("X-Forwarded-User")
        if user:
            return user
    if request is not None and not settings.trust_forwarded_headers:
        # Trust disabled: don't even read the headers. Return the
        # untrusted-edge marker so audit attribution stays honest.
        # Do NOT bump the fallback-identity counter -- this is an
        # intentional deploy posture, not an identity-header miss.
        return _UNTRUSTED_EDGE_ACTOR
    # Fallback path: bump the counter and emit a structured WARNING so
    # the event is observable in stdout JSON logs AND surfaced through
    # ``/api/health`` as ``fallback_identity_fallbacks_process_total``
    # (the legacy ``fallback_identity_fallbacks_total`` key is still
    # emitted for one cycle; R6-08 rename).
    global _FALLBACK_IDENTITY_COUNT
    _FALLBACK_IDENTITY_COUNT += 1
    emit(
        log,
        "identity_fallback",
        level=logging.WARNING,
        default_actor=settings.default_actor,
        fallback_count=_FALLBACK_IDENTITY_COUNT,
        message="audit_store.resolve_actor: no forwarded identity header",
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
# Factory -- single choke point for the FastAPI dependency graph.
# ----------------------------------------------------------------------


_AUDIT_STORE: AuditStore | None = None


def get_audit_store() -> AuditStore:
    """Lazy process-singleton audit store.

    Production path constructs a ``LakebaseAuditStore`` backed by the
    Lakebase client singleton. Tests override this factory in
    ``tests/conftest.py`` with the test fixture audit store so unit tests
    never touch Postgres.
    """
    global _AUDIT_STORE
    if _AUDIT_STORE is None:
        from backend.services.audit_lakebase_store import LakebaseAuditStore

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

    def list(self, limit: int = 50, **kwargs: Any) -> list[AuditEvent]:
        return get_audit_store().list(limit=limit, **kwargs)


audit_store: AuditStore = _AuditStoreProxy()
