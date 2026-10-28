"""PRINT_EXPORT receipt: the one ledger write behind an Export PDF.

Audit responsive-08 (W5d). Printing a dossier or a page of the ledger is a
copy leaving the screen, so Export PDF asks for this receipt BEFORE it opens
the print dialog, exactly as a CSV download waits for its export receipt.
The row is written through the governed ``AuditStore`` chain (PII denylist,
key allowlist, per-key value policy) under ``PRINT_EXPORT``, a server-owned
event type ``POST /api/audit/event`` refuses from a client.

What the row proves, and nothing more:

* Borrower 360: ``print_surface``, ``printed_row_count`` (always 1) and the
  masked ``borrower_id``; the entity is the borrower.
* Audit ledger: ``print_surface``, ``printed_row_count`` and the KEYED
  ``filter_fingerprint`` of the explorer query (``audit_fingerprint``), the
  value AUDIT_EXPORT stores. The entity is ``print-`` plus the fingerprint's
  letter image (``audit_entity_ids``), never its raw hex, which can read as a
  phone number and get the row refused. With no fingerprint key the receipt
  is refused and no row is written (fail closed, like AUDIT_EXPORT).

Never stored: the filters themselves, an id list or any row content. The
server never sees the printed page, so the row records the request, not the
paper.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.schemas.audit import AuditEvent
from backend.schemas.print_receipt import PrintReceipt, PrintReceiptRequest
from backend.services.audit_entity_ids import audit_safe_letters
from backend.services.audit_fingerprint import (
    AUDIT_LEDGER_FINGERPRINT_DOMAIN,
    keyed_filter_fingerprint,
)
from backend.services.audit_pagination import audit_filter_fingerprint
from backend.services.audit_store import AuditStore

PRINT_EXPORT_EVENT_TYPE = "PRINT_EXPORT"
PRINT_EXPORT_ACTION = "print.export"


class PrintFingerprintUnavailable(RuntimeError):
    """No fingerprint key is configured: a ledger print cannot be recorded keyed."""


def write_print_receipt(
    store: AuditStore,
    *,
    actor: str,
    payload: PrintReceiptRequest,
) -> PrintReceipt:
    """Write exactly one ``PRINT_EXPORT`` row for the page about to print."""

    fingerprint: str | None = None
    if payload.surface == "borrower_360":
        borrower_id = payload.borrower_id
        if borrower_id is None:  # the request model refuses this; never trust a bypass
            raise ValueError("a Borrower 360 print names its borrower")
        entity_type = "borrower"
        entity_id = borrower_id
        metadata: dict[str, object] = {
            "print_surface": payload.surface,
            "printed_row_count": 1,
            "borrower_id": borrower_id,
        }
    else:
        fingerprint = keyed_filter_fingerprint(
            audit_filter_fingerprint(dict(payload.filters)),
            domain=AUDIT_LEDGER_FINGERPRINT_DOMAIN,
        )
        if fingerprint is None:
            raise PrintFingerprintUnavailable("print fingerprint key is not configured")
        entity_type = "audit_ledger"
        entity_id = f"print-{audit_safe_letters(fingerprint)}"
        metadata = {
            "print_surface": payload.surface,
            "printed_row_count": payload.row_count,
            "filter_fingerprint": fingerprint,
        }
    event: AuditEvent = store.write(
        actor=actor,
        action=PRINT_EXPORT_ACTION,
        entity_type=entity_type,
        entity_id=entity_id,
        payload_json=metadata,
        event_type=PRINT_EXPORT_EVENT_TYPE,
    )
    return PrintReceipt(
        audit_event_id=event.event_id,
        actor=event.actor,
        surface=payload.surface,
        row_count=payload.row_count,
        borrower_id=payload.borrower_id,
        filter_fingerprint=fingerprint,
        recorded_at=event.created_at or datetime.now(UTC).isoformat(),
    )
