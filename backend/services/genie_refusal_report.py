""""This was legitimate" reports for governed Genie refusals.

A lender who believes a refusal was a false positive files the coarse
``refusal_reason`` and the ``refusal_report_hash`` the refused turn carried.
The report row and its ``GENIE_REFUSAL_REPORT`` audit event commit in one
Lakebase transaction (audit 2026-09-21 ``genie-05``).

The report row never carries text. Only when the reporter explicitly chooses
"Report with my question" (D-audit-reads-d) does the route offer the question,
and only after it hash-matched the report and passed the personal-details
gate. This module then binds it to a real refusal (a RUN_GENIE refused,
blocked or outreach-guardrail row the same actor received within 30 days),
scrubs it and writes it once into the sibling ``genie_refusal_report_texts``
table, where it expires after 90 days (nulled by the sweep, never deleted).
Logs carry ``has_text`` only; the text never reaches a log line, the audit
metadata, a prompt, a URL or ``genie_messages``.

One report row per (actor, question hash, family): a second click, a retry,
or a double-submit collapses onto the first report instead of inflating
counts, and attaches text at most once.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.schemas._validators_person_names import contains_human_name_shape
from backend.schemas._validators_unsafe_text import contains_mechanical_pii_or_raw_identifier
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_metadata_policy import _AUDIT_HUMAN_IDENTITY_DIRECTIVE_RE
from backend.services.genie_audit import (
    audit_safe_letter_digest,
    audit_safe_letters,
    genie_audit_entity_id_from_parts,
)
from backend.services.genie_message_policy import identity_prompt_match
from backend.services.genie_prompt_guardrails import pii_prompt_match
from backend.services.genie_refusal_reason import (
    GENIE_REFUSAL_AUDIT_CODES,
    refusal_ledger_action_types,
)
from backend.services.lakebase import LakebaseClient
from backend.services.observability import emit

log = logging.getLogger(__name__)

RefusalTextDecline = Literal["capture_disabled", "personal_details", "no_matching_refusal"]

#: The sweep's batch size: bounded so a report or a read never waits on a
#: large backlog; the hourly loop repeats while a pass fills it.
REFUSAL_TEXT_SWEEP_LIMIT = 200
_MASKED_BORROWER_ID_RE = re.compile(r"\bB-[0-9A-Z]{13}\b")

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

_EXISTING_REPORT_SQL = """
SELECT report_id, audit_event_id
FROM mip_app.genie_refusal_reports
WHERE actor_email = %(actor_email)s
  AND question_hash = %(question_hash)s
  AND refusal_reason = %(refusal_reason)s
"""

_ATTACH_AUDIT_SQL = """
UPDATE mip_app.genie_refusal_reports
SET audit_event_id = %(audit_event_id)s
WHERE report_id = %(report_id)s
RETURNING report_id
"""

# Served by idx_action_audit_actor (actor_email, event_at DESC).
_LEDGER_PROBE_SQL = """
SELECT 1 AS hit
FROM mip_app.action_audit
WHERE actor_email = %(actor)s
  AND event_type = 'RUN_GENIE'
  AND metadata->>'question_hash' = %(hash16)s
  AND metadata->>'action_type' = ANY(%(types)s)
  AND event_at > now() - interval '30 days'
LIMIT 1
"""

_INSERT_TEXT_SQL = """
INSERT INTO mip_app.genie_refusal_report_texts (
    report_id, question_text, redacted, expires_at
) VALUES (
    %(report_id)s, %(question_text)s, %(redacted)s, now() + interval '90 days'
)
ON CONFLICT (report_id) DO NOTHING
RETURNING report_id
"""

_LIVE_TEXT_SQL = """
SELECT 1 AS live
FROM mip_app.genie_refusal_report_texts
WHERE report_id = %(report_id)s
  AND purged_at IS NULL
  AND expires_at > now()
"""

SWEEP_EXPIRED_REFUSAL_TEXTS_SQL = """
UPDATE mip_app.genie_refusal_report_texts
SET question_text = NULL, purged_at = now()
WHERE report_id IN (
    SELECT report_id
    FROM mip_app.genie_refusal_report_texts
    WHERE purged_at IS NULL AND expires_at <= now()
    ORDER BY expires_at
    LIMIT %(limit)s
    FOR UPDATE SKIP LOCKED
)
RETURNING report_id
"""


@dataclass(frozen=True, slots=True)
class OfferedRefusalText:
    """A question the route already normalized, hash-matched and screened."""

    scrubbed: str
    redacted: bool


@dataclass(frozen=True, slots=True)
class GenieRefusalReportRecord:
    accepted: bool
    duplicate: bool
    report_id: str | None
    audit_event_id: str | None
    question_captured: bool = False
    declined: RefusalTextDecline | None = None


def _execute_one(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def sweep_expired_refusal_texts(conn: Any, limit: int = REFUSAL_TEXT_SWEEP_LIMIT) -> int:
    """Null up to ``limit`` expired question texts; return how many were purged.

    An UPDATE, never a DELETE: the report row, its audit rows and the text
    row's metadata stay; only the question goes (the purge-only trigger
    admits exactly this change). ``SKIP LOCKED`` lets concurrent sweeps share
    the backlog instead of queueing on each other.
    """

    rows = conn.execute(SWEEP_EXPIRED_REFUSAL_TEXTS_SQL, {"limit": limit}).fetchall()
    return len(rows)


def personal_details_in(*, normalized: str, scrubbed: str, refusal_reason: str) -> bool:
    """Whether an offered question names a person, a borrower or contact details.

    Detectors only, no new detection: the PII-request family itself, the
    prompt guards' PII and identity matches on the question as asked, and,
    on the scrubbed copy that would be stored, the borrower-copy contextual
    name, the human-name shape, the audit ledger's identity directive, the
    mechanical PII / raw identifier patterns (CLIP and Owner Link digit runs)
    and a masked borrower id. The protected-class arm is deliberately absent:
    that refused language is what the reviewers oversee.
    """

    return (
        refusal_reason == "pii_request"
        or pii_prompt_match(normalized) is not None
        or identity_prompt_match(normalized)
        or contains_borrower_copy_contextual_name(scrubbed)
        or contains_human_name_shape(scrubbed)
        or _AUDIT_HUMAN_IDENTITY_DIRECTIVE_RE.search(scrubbed) is not None
        or contains_mechanical_pii_or_raw_identifier(scrubbed)
        or _MASKED_BORROWER_ID_RE.search(scrubbed) is not None
    )


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
    A report never sends that text to the ledger, but ``question_hash`` is
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


def _ledger_has_refusal(conn: Any, *, actor: str, question_hash: str, refusal_reason: str) -> bool:
    row = _execute_one(
        conn,
        _LEDGER_PROBE_SQL,
        {
            "actor": actor,
            "hash16": question_hash[:16],
            "types": list(refusal_ledger_action_types(refusal_reason)),
        },
    )
    return row is not None


def _write_report_audit(
    conn: Any,
    *,
    actor: str,
    question_hash: str,
    refusal_reason: str,
    conversation_id: str | None,
    message_id: str | None,
    captured: bool,
    declined: RefusalTextDecline | None,
) -> str:
    # ``question_hash[:16]`` IS the ledger label of the refusal being
    # reported: the refused_prompt / response_blocked rows hash the same
    # question bytes (``refusal_report_hash``), so this row joins to its
    # refusal on ``question_hash`` and on ``entity_id``
    # (``refusal_ledger_entity_id``). The report row holds the full digest.
    # Only governed audit codes are written under ``refusal_reason``;
    # families without one (outreach, output policy, unknown) are still fully
    # recorded on the report row.
    payload_json: dict[str, Any] = {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "question_hash": question_hash[:16],
        "action_type": "refusal_report",
        "question_text_captured": captured,
    }
    if declined is not None:
        payload_json["question_text_declined"] = declined
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
    return str(event.event_id)


def record_genie_refusal_report(
    lakebase: LakebaseClient,
    *,
    actor: str,
    question_hash: str,
    refusal_reason: str,
    conversation_id: str | None,
    message_id: str | None,
    offered_text: OfferedRefusalText | None = None,
    declined: RefusalTextDecline | None = None,
) -> GenieRefusalReportRecord:
    """Insert the report, any consented text and their audit row atomically.

    ``offered_text`` is a question the route already accepted; it is stored
    only when the ledger holds this actor's matching refusal (else the report
    degrades to hash-only with ``no_matching_refusal``). ``declined`` carries
    the route's own decline reason when the text never reached this module.
    """

    params = {
        "actor_email": actor,
        "question_hash": question_hash,
        "refusal_reason": refusal_reason,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
    with lakebase.transaction() as conn:
        sweep_expired_refusal_texts(conn)
        accepted_text = offered_text
        if offered_text is not None and not _ledger_has_refusal(
            conn, actor=actor, question_hash=question_hash, refusal_reason=refusal_reason
        ):
            accepted_text, declined = None, "no_matching_refusal"
        inserted = _execute_one(conn, _INSERT_REPORT_SQL, params)
        if inserted is None:
            existing = _execute_one(conn, _EXISTING_REPORT_SQL, params)
            if existing is None:
                raise RuntimeError("genie refusal report vanished after its unique conflict")
            report_id = str(existing["report_id"])
            first_audit = existing.get("audit_event_id")
            if accepted_text is None or not _attach_text(conn, report_id, accepted_text):
                # Nothing new is stored, so nothing new is audited. The flag
                # still tells the truth: a retry of a captured report keeps
                # its question.
                captured = accepted_text is not None and (
                    _execute_one(conn, _LIVE_TEXT_SQL, {"report_id": report_id}) is not None
                )
                _log_report(duplicate=True, has_text=captured, declined=declined)
                return GenieRefusalReportRecord(
                    accepted=True,
                    duplicate=True,
                    report_id=report_id,
                    audit_event_id=str(first_audit) if first_audit is not None else None,
                    question_captured=captured,
                    declined=declined,
                )
            # One audit row per text row: the newly attached question gets
            # its own GENIE_REFUSAL_REPORT row; the report keeps its first.
            event_id = _write_report_audit(
                conn, actor=actor, captured=True, declined=None, **_audit_ids(params)
            )
            _log_report(duplicate=True, has_text=True, declined=None)
            return GenieRefusalReportRecord(
                accepted=True,
                duplicate=True,
                report_id=report_id,
                audit_event_id=event_id,
                question_captured=True,
            )
        report_id = str(inserted["report_id"])
        captured = accepted_text is not None and _attach_text(conn, report_id, accepted_text)
        event_id = _write_report_audit(
            conn, actor=actor, captured=captured, declined=declined, **_audit_ids(params)
        )
        attached = _execute_one(
            conn,
            _ATTACH_AUDIT_SQL,
            {"audit_event_id": event_id, "report_id": report_id},
        )
        if attached is None:
            raise RuntimeError("genie refusal report vanished before its audit link")
    _log_report(duplicate=False, has_text=captured, declined=declined)
    return GenieRefusalReportRecord(
        accepted=True,
        duplicate=False,
        report_id=report_id,
        audit_event_id=event_id,
        question_captured=captured,
        declined=declined,
    )


def _audit_ids(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_hash": params["question_hash"],
        "refusal_reason": params["refusal_reason"],
        "conversation_id": params["conversation_id"],
        "message_id": params["message_id"],
    }


def _attach_text(conn: Any, report_id: str, text: OfferedRefusalText) -> bool:
    row = _execute_one(
        conn,
        _INSERT_TEXT_SQL,
        {"report_id": report_id, "question_text": text.scrubbed, "redacted": text.redacted},
    )
    return row is not None


def _log_report(*, duplicate: bool, has_text: bool, declined: RefusalTextDecline | None) -> None:
    emit(
        log,
        "genie_refusal_reported",
        outcome="duplicate" if duplicate else "recorded",
        has_text=has_text,
        text_declined=declined,
    )
