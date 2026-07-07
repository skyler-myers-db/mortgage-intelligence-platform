"""Conversation-ownership guard for Genie surfaces.

Extracted verbatim from ``backend/api/genie.py`` (2026-07-07) so both the
message router and the feedback router can enforce the same boundary without
a router-to-router import. The SQL binds BOTH the actor (from the trusted
Databricks Apps identity edge) and the conversation id, so a caller can never
act on another actor's conversation.
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
