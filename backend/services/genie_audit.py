"""Audit helpers for Genie API responses."""

from __future__ import annotations

import hashlib

from backend.services.genie_answers import GenieMessageResponse


def genie_audit_entity_id(response: GenieMessageResponse) -> str:
    """Return a PII-safe nonblank id for every Genie audit row."""

    return (
        response.message_id
        or response.conversation_id
        or response.question_hash
        or hashlib.sha256(response.question.encode("utf-8")).hexdigest()[:16]
    )
