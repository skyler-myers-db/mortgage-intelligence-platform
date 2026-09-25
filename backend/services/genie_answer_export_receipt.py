"""GENIE_ANSWER_EXPORT receipt: the one ledger write behind a Genie CSV download.

Audit 2026-09-21 genie-06 (slice 2). A Genie answer's rows can be downloaded
as CSV, built in the browser from the rows the answer already holds. The
browser asks for this receipt BEFORE the download starts and downloads
nothing without it. The row is written through the governed ``AuditStore``
chain (PII denylist, key allowlist, per-key value policy) under the
server-owned ``GENIE_ANSWER_EXPORT`` event type, which ``POST
/api/audit/event`` refuses from a client.

Ownership first, fail closed: the message must be a durable
``mip_app.genie_messages`` row of THIS actor's conversation, recorded from a
trusted source (the five non-persistable sources never are). Anything else,
including another actor's real message, is answered 404 with a constant
detail and NOTHING is written, so the route cannot be used to forge an export
row against a conversation the caller does not own.

What the row proves:

* ``actor``              -- the edge-forwarded identity (never a body field);
* ``conversation_id`` / ``message_id`` -- the answer, verified as owned;
* ``exported_row_count`` -- the rows the file holds;
* ``row_count``          -- the row count the answer (or section) reports;
* ``csv_sha256``         -- the file bytes, hashed in the browser;
* ``columns_sha256``     -- the file's column keys, hashed in the browser.
The two digests are RECORDED, not verified: the server never sees the bytes
or the rows (as for LEAD_EXPORT's csv_sha256).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.schemas.audit import AuditEvent
from backend.schemas.genie_export import GenieAnswerExportReceipt, GenieAnswerExportReceiptRequest
from backend.services.audit_store import AuditStore
from backend.services.genie_audit import genie_audit_entity_id_from_parts
from backend.services.lakebase import LakebaseClient

GENIE_ANSWER_EXPORT_EVENT_TYPE = "GENIE_ANSWER_EXPORT"
GENIE_ANSWER_EXPORT_ENTITY_TYPE = "genie_message"
_ACTION_BY_SCOPE = {"answer": "genie.answer_export", "section": "genie.section_export"}

# Trusted sources such as trusted_sql are accepted (unlike the feedback
# guard's source = 'genie'); the five non-persistable sources never are.
GENIE_EXPORT_OWNERSHIP_SQL = """
SELECT messages.row_count
FROM mip_app.genie_messages AS messages
JOIN mip_app.genie_sessions AS sessions
  ON sessions.actor_email = messages.actor_email
 AND sessions.conversation_id = messages.conversation_id
WHERE messages.actor_email = %(actor_email)s
  AND messages.conversation_id = %(conversation_id)s
  AND messages.message_id = %(message_id)s
  AND messages.source NOT IN ('degraded', 'policy_blocked', 'refused', 'data_gap', 'out_of_footprint')
LIMIT 1
"""


class GenieExportNotFound(LookupError):
    """No owned, trusted Genie answer matches the declaration."""


class GenieExportCountMismatch(ValueError):
    """The declared row counts do not describe the owned answer."""


def owned_answer_row_count(
    lakebase: LakebaseClient,
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
) -> int:
    """The stored row count of the actor's own trusted answer, or refuse."""

    row: dict[str, Any] | None = lakebase.fetchone(
        GENIE_EXPORT_OWNERSHIP_SQL,
        {"actor_email": actor, "conversation_id": conversation_id, "message_id": message_id},
    )
    if row is None:
        raise GenieExportNotFound("no owned trusted Genie answer for this declaration")
    return int(row.get("row_count") or 0)


def verify_genie_export_counts(payload: GenieAnswerExportReceiptRequest, stored_row_count: int) -> None:
    """Refuse a declaration whose counts cannot describe the owned answer."""

    reported = payload.answer_row_count
    if reported is not None and payload.row_count > reported:
        raise GenieExportCountMismatch("row_count exceeds the answer's reported row count")
    if payload.scope == "answer" and stored_row_count > 0 and stored_row_count != reported:
        raise GenieExportCountMismatch("answer_row_count does not match the recorded answer")


def write_genie_answer_export_receipt(
    store: AuditStore,
    lakebase: LakebaseClient,
    *,
    actor: str,
    payload: GenieAnswerExportReceiptRequest,
) -> GenieAnswerExportReceipt:
    """Verify ownership and counts, then write exactly one GENIE_ANSWER_EXPORT row."""

    stored_row_count = owned_answer_row_count(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
    )
    verify_genie_export_counts(payload, stored_row_count)
    event: AuditEvent = store.write(
        actor=actor,
        action=_ACTION_BY_SCOPE[payload.scope],
        entity_type=GENIE_ANSWER_EXPORT_ENTITY_TYPE,
        entity_id=genie_audit_entity_id_from_parts(
            conversation_id=payload.conversation_id,
            message_id=payload.message_id,
        ),
        payload_json={
            "conversation_id": payload.conversation_id,
            "message_id": payload.message_id,
            "exported_row_count": payload.row_count,
            "row_count": payload.answer_row_count,
            "csv_sha256": payload.csv_sha256,
            "columns_sha256": payload.columns_sha256,
        },
        event_type=GENIE_ANSWER_EXPORT_EVENT_TYPE,
    )
    return GenieAnswerExportReceipt(
        audit_event_id=event.event_id,
        actor=event.actor,
        scope=payload.scope,
        row_count=payload.row_count,
        csv_sha256=payload.csv_sha256,
        columns_sha256=payload.columns_sha256,
        recorded_at=event.created_at or datetime.now(UTC).isoformat(),
    )
