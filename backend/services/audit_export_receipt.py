"""AUDIT_EXPORT receipt: the one ledger write behind an audit-explorer CSV download.

Audit tables-10 (2026-09-30), the lead-export receipt pattern (tables-08)
applied to the ledger itself: exporting who-did-what is an auditable act. The
explorer asks for this receipt BEFORE the download starts. It is written
through the governed ``AuditStore`` chain (PII denylist, key allowlist,
per-key value policy) under ``AUDIT_EXPORT``, a server-owned event type
``POST /api/audit/event`` refuses from a client.

What the row proves:

* ``actor``              -- the edge-forwarded identity (never a body field).
* ``filter_fingerprint`` -- ``audit_filter_fingerprint`` of the explorer's
  query parameters (the canonical form the page cursors are bound to), KEYED
  through ``audit_fingerprint`` before it is stored or answered (W5c, 12.3):
  a dictionary of candidate filters never reproduces it. With no key the
  receipt is refused and no row is written (fail closed).
* ``exported_row_count`` -- what the file holds.
* ``csv_sha256``         -- the file bytes, hashed in the browser. The server
  never sees the bytes, so this is RECORDED, not verified.
* ``event_ids_sha256``   -- the event-id list, hashed in the browser AND
  recomputed here from the ids the client sends; a mismatch refuses the
  receipt. The ids themselves are NOT stored: the ledger rows they name are
  already in the ledger.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from backend.schemas.audit import AuditEvent, AuditExportReceipt, AuditExportReceiptRequest
from backend.services.audit_fingerprint import (
    AUDIT_LEDGER_FINGERPRINT_DOMAIN,
    keyed_filter_fingerprint,
)
from backend.services.audit_pagination import audit_filter_fingerprint
from backend.services.audit_store import AuditStore
from backend.services.audit_store_receipt import is_valid_audit_event_id

AUDIT_EXPORT_EVENT_TYPE = "AUDIT_EXPORT"
AUDIT_EXPORT_ACTION = "audit_explorer.export"
AUDIT_EXPORT_ENTITY_TYPE = "audit_ledger"


class AuditExportDeclarationMismatch(ValueError):
    """The client's declaration does not describe the event-id list it sent."""


class AuditExportInvalidEventIds(ValueError):
    """An id in the declaration is not an audit event id."""


class AuditExportFingerprintUnavailable(RuntimeError):
    """No fingerprint key is configured: the receipt cannot be written keyed."""


def event_ids_digest(event_ids: Sequence[str]) -> str:
    """SHA-256 of the compact JSON array of ids, in file order (``JSON.stringify``)."""

    canonical = json.dumps(list(event_ids), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_audit_export_declaration(payload: AuditExportReceiptRequest) -> None:
    """Refuse invalid ids, and a count or digest that does not match the ids."""

    if not all(is_valid_audit_event_id(event_id) for event_id in payload.event_ids):
        raise AuditExportInvalidEventIds("event_ids must be audit event ids")
    if payload.row_count != len(payload.event_ids):
        raise AuditExportDeclarationMismatch("row_count does not match the event id list")
    if event_ids_digest(payload.event_ids) != payload.event_ids_sha256:
        raise AuditExportDeclarationMismatch("event_ids_sha256 does not match the event id list")


def write_audit_export_receipt(
    store: AuditStore,
    *,
    actor: str,
    payload: AuditExportReceiptRequest,
) -> AuditExportReceipt:
    """Verify the declaration and write exactly one ``AUDIT_EXPORT`` row."""

    verify_audit_export_declaration(payload)
    fingerprint = keyed_filter_fingerprint(
        audit_filter_fingerprint(dict(payload.filters)),
        domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN,
    )
    if fingerprint is None:
        raise AuditExportFingerprintUnavailable("audit export fingerprint key is not configured")
    event: AuditEvent = store.write(
        actor=actor,
        action=AUDIT_EXPORT_ACTION,
        entity_type=AUDIT_EXPORT_ENTITY_TYPE,
        entity_id=f"export-{fingerprint[:16]}",
        payload_json={
            "exported_row_count": payload.row_count,
            "csv_sha256": payload.csv_sha256,
            "event_ids_sha256": payload.event_ids_sha256,
            "filter_fingerprint": fingerprint,
        },
        event_type=AUDIT_EXPORT_EVENT_TYPE,
    )
    return AuditExportReceipt(
        audit_event_id=event.event_id,
        actor=event.actor,
        row_count=payload.row_count,
        csv_sha256=payload.csv_sha256,
        event_ids_sha256=payload.event_ids_sha256,
        filter_fingerprint=fingerprint,
        recorded_at=event.created_at or datetime.now(UTC).isoformat(),
    )
