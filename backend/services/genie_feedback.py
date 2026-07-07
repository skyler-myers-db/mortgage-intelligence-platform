"""Genie answer feedback (thumbs up/down) service.

The ``/api/genie/feedback`` router delegates here so the router stays thin.
Two side effects happen, in this order:

1. Best-effort: post a governed comment on the Genie conversation message via
   the Genie comments API (``create_message_comment`` shape). A failure here
   MUST NOT fail the request -- feedback is captured in the audit ledger
   regardless of whether the upstream comment landed.
2. Required: write one ``GENIE_FEEDBACK`` audit row in a Lakebase transaction
   (the same in-transaction pattern the Growth Agent uses), so the feedback is
   durable and joinable to the request logs.

PII posture: the caller-supplied ``comment`` is validated public-safe upstream
(``backend.schemas.common``) and additionally routed through ``scrub_free_text``
before it is posted to Genie. The audit row never stores the comment text --
only ``comment_present`` (a bool) plus the governed conversation/message ids.
"""

from __future__ import annotations

import logging

from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.genie_audit import genie_audit_entity_id_from_parts
from backend.services.genie_client import ResilientGenieClient
from backend.services.lakebase import LakebaseClient
from backend.services.observability import emit
from backend.services.pii_redaction import scrub_free_text

log = logging.getLogger("backend.services.genie_feedback")

_HELPFUL_COMMENT = "MIP feedback: helpful"
_NOT_HELPFUL_COMMENT = "MIP feedback: not helpful"


def _governed_comment_text(*, helpful: bool, comment: str | None) -> str:
    """Compose the governed Genie comment text.

    Prefix is one of two fixed strings; any caller note is appended only after
    a defence-in-depth ``scrub_free_text`` pass so a slipped PII token in the
    note is redacted before it reaches the Genie comment surface.
    """
    prefix = _HELPFUL_COMMENT if helpful else _NOT_HELPFUL_COMMENT
    note = scrub_free_text((comment or "").strip()).strip()
    return f"{prefix} — {note}" if note else prefix


def _post_comment_best_effort(
    genie: ResilientGenieClient,
    *,
    conversation_id: str,
    message_id: str,
    text: str,
) -> bool:
    """Post the Genie message comment; never raise (best-effort side effect)."""
    try:
        posted = genie.post_message_comment(conversation_id, message_id, text)
    except Exception as exc:  # noqa: BLE001 - feedback must survive a comment failure
        emit(
            log,
            "genie_feedback_comment_failed",
            level=logging.WARNING,
            dependency="genie",
            outcome="degraded",
            exc_type=type(exc).__name__,
            exc_msg=str(exc)[:500],
        )
        return False
    if not posted:
        emit(
            log,
            "genie_feedback_comment_not_posted",
            level=logging.WARNING,
            dependency="genie",
            outcome="degraded",
        )
    return posted


def record_genie_feedback(
    lakebase: LakebaseClient,
    genie: ResilientGenieClient,
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
    helpful: bool,
    comment: str | None,
) -> str:
    """Post the best-effort Genie comment, write the audit row, return its id.

    Returns the ``audit_event_id`` of the durable ``GENIE_FEEDBACK`` row. The
    comment post is best-effort and its success is not part of the return
    contract -- the audit row is the source of truth.
    """
    _post_comment_best_effort(
        genie,
        conversation_id=conversation_id,
        message_id=message_id,
        text=_governed_comment_text(helpful=helpful, comment=comment),
    )
    entity_id = genie_audit_entity_id_from_parts(
        message_id=message_id,
        conversation_id=conversation_id,
        fallback="genie",
    )
    with lakebase.transaction() as conn:
        event = write_audit_event_in_transaction(
            conn,
            actor=actor,
            action="genie.feedback",
            entity_type="genie_message",
            entity_id=entity_id,
            payload_json={
                "conversation_id": conversation_id,
                "message_id": message_id,
                "helpful": helpful,
                "comment_present": bool((comment or "").strip()),
            },
            event_type="GENIE_FEEDBACK",
        )
    return event.event_id
