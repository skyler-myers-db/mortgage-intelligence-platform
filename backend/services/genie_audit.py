"""Audit helpers for Genie API responses."""

from __future__ import annotations

import hashlib

from backend.schemas.common import validate_public_audit_identifier_or_none
from backend.services.genie_answers import GenieMessageResponse


def _letter_digest(seed: str) -> str:
    """Return a stable audit-safe digest with no phone-shaped digit runs."""

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    table = str.maketrans("0123456789abcdef", "abcdefghijklmnop")
    return digest.translate(table)


def _public_safe_entity_id(candidate: str | None, *, seed: str) -> str | None:
    if not candidate:
        return None
    try:
        value = validate_public_audit_identifier_or_none(candidate)
    except ValueError:
        value = None
    if value:
        return value
    return f"geniehash-{_letter_digest(seed)}"


def genie_audit_entity_id_from_parts(
    *,
    question: str | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    question_hash: str | None = None,
    fallback: str = "genie",
) -> str:
    """Return an audit-safe id from raw Genie response or guardrail fields."""

    seed = question or question_hash or message_id or conversation_id or fallback
    for candidate in (message_id, conversation_id, question_hash):
        safe = _public_safe_entity_id(candidate, seed=seed)
        if safe:
            return safe
    return f"geniehash-{_letter_digest(seed)}"


def genie_audit_entity_id(response: GenieMessageResponse) -> str:
    """Return a PII-safe nonblank id for every Genie audit row."""

    return genie_audit_entity_id_from_parts(
        question=response.question,
        conversation_id=response.conversation_id,
        message_id=response.message_id,
        question_hash=response.question_hash,
    )
