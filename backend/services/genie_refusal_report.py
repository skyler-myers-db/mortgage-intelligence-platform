"""Hash-only "this was legitimate" reports for governed Genie refusals.

A lender who believes a refusal was a false positive files the coarse
``refusal_reason`` and the ``refusal_report_hash`` the refused turn carried.
The report row and its ``GENIE_REFUSAL_REPORT`` audit event commit in one
Lakebase transaction. Nothing typed by the user is accepted, stored, or
logged: the hash is the whole triage key (audit 2026-09-21 ``genie-05``;
register owner decision defaults to hash-only).

One row per (actor, question hash, family): a second click, a retry, or a
double-submit collapses onto the first report instead of inflating counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.genie_audit import (
    audit_safe_letter_digest,
    audit_safe_letters,
    genie_audit_entity_id_from_parts,
)
from backend.services.genie_refusal_reason import GENIE_REFUSAL_AUDIT_CODES
from backend.services.lakebase import LakebaseClient

_INSERT_REPORT_SQL = """
INSERT INTO mip_app.genie_refusal_reports (
    actor_email, question_hash, refusal_reason, conversation_id, message_id
) VALUES (
    %(actor_email)s, %(question_hash)s, %(refusal_reason)s,
    %(conversation_id)s, %(message_id)s
)
ON CONFLICT (actor_email, question_hash, refusal_reason) DO NOTHING
RETURNING report_id
"""

_ATTACH_AUDIT_SQL = """
UPDATE mip_app.genie_refusal_reports
SET audit_event_id = %(audit_event_id)s
WHERE report_id = %(report_id)s
RETURNING report_id
"""


@dataclass(frozen=True, slots=True)
class GenieRefusalReportRecord:
    accepted: bool
    duplicate: bool
    report_id: str | None
    audit_event_id: str | None


def _execute_one(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def refusal_ledger_entity_id(
    *,
    question_hash: str,
    conversation_id: str | None,
    message_id: str | None,
) -> str:
    """The ``entity_id`` the refused turn's own ledger row carries.

    The ledger builds it with ``genie_audit_entity_id_from_parts``: the first
    present id that is a public-safe audit identifier wins; a present id that
    is not (a Genie id or 16-hex label with a phone-shaped digit run) falls
    back to ``geniehash-`` + the letter digest SEEDED FROM THE QUESTION TEXT.
    A hash-only report never has that text, but ``question_hash`` is
    ``sha256(question)`` itself, so its letter image is the same fallback.
    Without this, a few percent of refusals (any whose id carries a
    ten-digit run) would not join their report on ``entity_id``.
    """

    label = question_hash[:16]
    entity_id = genie_audit_entity_id_from_parts(
        conversation_id=conversation_id,
        message_id=message_id,
        question_hash=label,
    )
    # With no question, ``from_parts`` seeds its fallback from the label;
    # swap that for the ledger's question-seeded one.
    if entity_id == f"geniehash-{audit_safe_letter_digest(label)}":
        return f"geniehash-{audit_safe_letters(question_hash)}"
    return entity_id


def record_genie_refusal_report(
    lakebase: LakebaseClient,
    *,
    actor: str,
    question_hash: str,
    refusal_reason: str,
    conversation_id: str | None,
    message_id: str | None,
) -> GenieRefusalReportRecord:
    """Insert the report and its audit row atomically; replays are no-ops."""

    with lakebase.transaction() as conn:
        inserted = _execute_one(
            conn,
            _INSERT_REPORT_SQL,
            {
                "actor_email": actor,
                "question_hash": question_hash,
                "refusal_reason": refusal_reason,
                "conversation_id": conversation_id,
                "message_id": message_id,
            },
        )
        if inserted is None:
            return GenieRefusalReportRecord(
                accepted=True, duplicate=True, report_id=None, audit_event_id=None
            )
        report_id = str(inserted["report_id"])
        # ``question_hash[:16]`` IS the ledger label of the refusal being
        # reported: the refused_prompt / response_blocked rows hash the same
        # question bytes (``refusal_report_hash``), so this row joins to its
        # refusal on ``question_hash`` and on ``entity_id``
        # (``refusal_ledger_entity_id``).
        # The report row holds the full digest. Only governed audit codes are
        # written under ``refusal_reason``; families without one (outreach,
        # output policy, unknown) are still fully recorded on the report row.
        payload_json: dict[str, Any] = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "question_hash": question_hash[:16],
            "action_type": "refusal_report",
        }
        audit_code = GENIE_REFUSAL_AUDIT_CODES.get(refusal_reason)
        if audit_code is not None:
            payload_json["refusal_reason"] = audit_code
        event = write_audit_event_in_transaction(
            conn,
            actor=actor,
            action="genie.refusal_reported",
            entity_type="genie_message",
            entity_id=refusal_ledger_entity_id(
                question_hash=question_hash,
                conversation_id=conversation_id,
                message_id=message_id,
            ),
            payload_json=payload_json,
            event_type="GENIE_REFUSAL_REPORT",
        )
        attached = _execute_one(
            conn,
            _ATTACH_AUDIT_SQL,
            {"audit_event_id": event.event_id, "report_id": report_id},
        )
        if attached is None:
            raise RuntimeError("genie refusal report vanished before its audit link")
    return GenieRefusalReportRecord(
        accepted=True,
        duplicate=False,
        report_id=report_id,
        audit_event_id=event.event_id,
    )
