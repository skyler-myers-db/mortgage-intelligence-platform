"""LEAD_EXPORT receipt: the one ledger write behind a Lead Queue CSV download.

Audit tables-08 (2026-09-21): a product whose promise ends in "audit" let
operators download borrower lists with no ledger entry. The client now asks
this service for a receipt BEFORE the download starts. The receipt is written
through the same governed ``AuditStore`` chain as every other server-owned
event (PII denylist, key allowlist, per-key value policy), under the
``LEAD_EXPORT`` event type that ``POST /api/audit/event`` refuses to accept
from a client, so the ledger row can only come from here.

What the row proves:

* ``actor``              -- the edge-forwarded identity (never a body field).
* ``filter_fingerprint`` -- SHA-256 of the canonical JSON of the Lead Queue
  query parameters the rows were read with. It is the same canonical form
  ``handoff_filters_fingerprint`` uses for the Growth Agent handoff, so an
  auditor recomputes it from the CSV's ``# filters=`` metadata line.
* ``exported_row_count`` -- what the file holds, post eligibility gate.
* ``csv_sha256``         -- the file bytes, hashed in the browser. The server
  never sees the bytes, so this is RECORDED, not verified.
* ``borrower_ids_sha256`` -- the id list, hashed in the browser AND recomputed
  here from the ids the client sends; a mismatch refuses the receipt, so a
  client cannot declare one list and hash another.
* ``borrower_ids``       -- the masked ids themselves, so "who was in the file"
  is answerable without the file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from backend.schemas.audit import AuditEvent
from backend.schemas.lead_export import LeadExportReceipt, LeadExportReceiptRequest
from backend.services.audit_store import AuditStore
from backend.services.growth_agent_handoff import handoff_filters_fingerprint

LEAD_EXPORT_EVENT_TYPE = "LEAD_EXPORT"
LEAD_EXPORT_ACTION = "lead_queue.export"
LEAD_EXPORT_ENTITY_TYPE = "lead_queue"


class LeadExportDigestMismatch(ValueError):
    """The client's declaration does not describe the id list it sent."""


def borrower_ids_digest(borrower_ids: Sequence[str]) -> str:
    """SHA-256 of the compact JSON array of ids, in file order.

    Canonical form is ``JSON.stringify(ids)`` in the browser: no whitespace,
    ASCII only (the ids are validated ``B-`` tokens), order preserved.
    """

    canonical = json.dumps(list(borrower_ids), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lead_export_filter_fingerprint(filters: Mapping[str, str]) -> str:
    """Fingerprint of the queue's query parameters, key order independent."""

    return handoff_filters_fingerprint({key: filters[key] for key in sorted(filters)})


def verify_lead_export_declaration(payload: LeadExportReceiptRequest) -> None:
    """Refuse a declaration whose count or id digest does not match its ids."""

    if payload.row_count != len(payload.borrower_ids):
        raise LeadExportDigestMismatch("row_count does not match the borrower id list")
    if borrower_ids_digest(payload.borrower_ids) != payload.borrower_ids_sha256:
        raise LeadExportDigestMismatch("borrower_ids_sha256 does not match the borrower id list")


def write_lead_export_receipt(
    store: AuditStore,
    *,
    actor: str,
    payload: LeadExportReceiptRequest,
) -> LeadExportReceipt:
    """Verify the declaration and write exactly one ``LEAD_EXPORT`` row."""

    verify_lead_export_declaration(payload)
    fingerprint = lead_export_filter_fingerprint(payload.filters)
    event: AuditEvent = store.write(
        actor=actor,
        action=LEAD_EXPORT_ACTION,
        entity_type=LEAD_EXPORT_ENTITY_TYPE,
        entity_id=f"export-{fingerprint[:16]}",
        payload_json={
            "export_scope": payload.scope,
            "exported_row_count": payload.row_count,
            "csv_sha256": payload.csv_sha256,
            "borrower_ids_sha256": payload.borrower_ids_sha256,
            "filter_fingerprint": fingerprint,
            "borrower_ids": list(payload.borrower_ids),
        },
        event_type=LEAD_EXPORT_EVENT_TYPE,
    )
    return LeadExportReceipt(
        audit_event_id=event.event_id,
        actor=event.actor,
        scope=payload.scope,
        row_count=payload.row_count,
        csv_sha256=payload.csv_sha256,
        borrower_ids_sha256=payload.borrower_ids_sha256,
        filter_fingerprint=fingerprint,
        recorded_at=event.created_at or datetime.now(UTC).isoformat(),
    )
