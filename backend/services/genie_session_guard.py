"""Conversation and message ownership guards for Genie surfaces.

Extracted from ``backend/api/genie.py`` so Genie routes can enforce the same
boundary without router-to-router imports. Feedback uses the stricter message
guard: actor, conversation, and message must all match durable Lakebase rows.
"""

from __future__ import annotations

from fastapi import HTTPException

from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseClient, LakebaseError

GENIE_SESSION_OWNERSHIP_SQL = """
SELECT conversation_id
FROM mip_app.genie_sessions
WHERE actor_email = %(actor_email)s
  AND conversation_id = %(conversation_id)s
  AND source NOT IN ('degraded', 'policy_blocked', 'refused', 'data_gap', 'out_of_footprint')
LIMIT 1
"""

GENIE_MESSAGE_OWNERSHIP_SQL = """
SELECT messages.conversation_id, messages.message_id
FROM mip_app.genie_sessions AS sessions
JOIN mip_app.genie_messages AS messages
  ON messages.actor_email = sessions.actor_email
 AND messages.conversation_id = sessions.conversation_id
WHERE sessions.actor_email = %(actor_email)s
  AND sessions.conversation_id = %(conversation_id)s
  AND messages.message_id = %(message_id)s
  AND sessions.source = 'genie'
  AND messages.source = 'genie'
LIMIT 1
"""


def assert_genie_conversation_owned(
    lakebase: LakebaseClient,
    *,
    actor: str,
    conversation_id: str | None,
) -> None:
    if not conversation_id:
        return
    try:
        row = lakebase.fetchone(
            GENIE_SESSION_OWNERSHIP_SQL,
            {
                "actor_email": actor,
                "conversation_id": conversation_id,
            },
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if row is None:
        raise HTTPException(
            status_code=403,
            detail="conversation_id is not owned by the current actor",
        )


def assert_genie_message_owned(
    lakebase: LakebaseClient,
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
) -> None:
    """Require an actor-owned native Genie message before feedback side effects."""
    try:
        row = lakebase.fetchone(
            GENIE_MESSAGE_OWNERSHIP_SQL,
            {
                "actor_email": actor,
                "conversation_id": conversation_id,
                "message_id": message_id,
            },
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if row is None:
        raise HTTPException(
            status_code=403,
            detail="message_id is not owned by the current actor and conversation",
        )
