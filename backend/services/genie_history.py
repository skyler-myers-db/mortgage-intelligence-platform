"""Read-side of Ask Genie conversation history, backed by Lakebase.

The chat UI can reopen a previous conversation, so the durable
``mip_app.genie_messages`` ledger now carries, per turn, the guarded question
and the exact governed ``GenieMessageResponse`` that was returned.

Governance posture for the stored payload (deliberate change of the original
"identifiers and proof metadata only" rule, 2026-08-06):

* Only turns whose ``source`` is NOT in the refused/degraded/blocked set are
  recorded at all, so a prompt that tripped the guard battery is never stored.
* A stored question has already cleared the PII / identity / protected-class /
  injection prompt guards.
* A stored answer has already cleared ``genie_response_has_unsafe_visible_text``
  and row-level PII-key redaction, which is why the read path re-serves it
  without re-scanning.
* Signed action tokens are stripped before persistence: replaying a transcript
  must never hand back authorization to run a governed write action. A user who
  wants to act re-asks and gets a fresh, freshly-authorized turn.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_answers import (
    GenieMessageResponse,
    GenieSessionDetailResponse,
    GenieSessionListResponse,
    GenieSessionSummary,
    GenieSessionTurn,
)
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit

log = logging.getLogger("mip-genie")

#: History list page size. The sidebar shows recent conversations, not an
#: archive browser.
GENIE_HISTORY_SESSION_LIMIT = 20
#: Hard cap on turns replayed for one conversation.
GENIE_HISTORY_TURN_LIMIT = 100
#: List title length.
GENIE_HISTORY_TITLE_MAX = 80
#: Stored question length (the request model already caps the prompt at 4,000).
GENIE_HISTORY_QUESTION_MAX = 4_000
#: Serialized payload budget per turn. A governed answer with a full result
#: table is a few tens of KiB; this bounds a pathological one.
GENIE_HISTORY_PAYLOAD_MAX_BYTES = 256 * 1024
#: Row count kept when a payload has to be trimmed to fit the budget.
GENIE_HISTORY_TRIMMED_ROWS = 50

_TRIMMED_ROWS_GAP = (
    "Replayed from history: some result rows were trimmed to bound the stored "
    "payload. Re-ask the question for the complete result set."
)
_DROPPED_ROWS_GAP = (
    "Replayed from history: the result table was too large to store and is not "
    "shown. Re-ask the question for the complete result set."
)


GENIE_SESSION_LIST_SQL = """
SELECT s.conversation_id,
       s.updated_at AS last_activity_at,
       (SELECT count(*)
          FROM mip_app.genie_messages gm
         WHERE gm.actor_email = s.actor_email
           AND gm.conversation_id = s.conversation_id) AS turn_count,
       (SELECT gm.question_text
          FROM mip_app.genie_messages gm
         WHERE gm.actor_email = s.actor_email
           AND gm.conversation_id = s.conversation_id
           AND gm.question_text IS NOT NULL
         ORDER BY gm.created_at, gm.message_id
         LIMIT 1) AS first_question
  FROM mip_app.genie_sessions s
 WHERE s.actor_email = %(actor_email)s
 ORDER BY s.updated_at DESC
 LIMIT %(limit)s
"""

GENIE_SESSION_TURNS_SQL = """
SELECT question_text, response_json
  FROM mip_app.genie_messages
 WHERE actor_email = %(actor_email)s
   AND conversation_id = %(conversation_id)s
 ORDER BY created_at, message_id
 LIMIT %(limit)s
"""


def history_question_text(question: str | None) -> str | None:
    """Bound the stored prompt; ``None`` when there is nothing to store."""
    text = (question or "").strip()
    if not text:
        return None
    return text[:GENIE_HISTORY_QUESTION_MAX]


def history_payload_json(response: GenieMessageResponse) -> str | None:
    """Serialize one governed answer for replay, bounded and de-authorized.

    Returns ``None`` when even a row-free payload exceeds the budget; the turn
    is still recorded for provenance, it just cannot be replayed.
    """
    payload = response.model_dump(mode="json")
    # Never persist signed action authorization. See module docstring.
    payload["actions"] = []
    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= GENIE_HISTORY_PAYLOAD_MAX_BYTES:
        return encoded

    rows = payload.get("table_rows") or []
    if isinstance(rows, list) and len(rows) > GENIE_HISTORY_TRIMMED_ROWS:
        payload["table_rows"] = rows[:GENIE_HISTORY_TRIMMED_ROWS]
        _append_history_gap(payload, _TRIMMED_ROWS_GAP)
        encoded = json.dumps(payload, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= GENIE_HISTORY_PAYLOAD_MAX_BYTES:
            return encoded

    payload["table_rows"] = []
    _append_history_gap(payload, _DROPPED_ROWS_GAP)
    encoded = json.dumps(payload, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= GENIE_HISTORY_PAYLOAD_MAX_BYTES:
        return encoded
    emit(
        log,
        "genie_history_payload_too_large",
        level=logging.WARNING,
        dependency="lakebase",
        outcome="skipped",
        conversation_id=response.conversation_id,
        message_id=response.message_id,
    )
    return None


def _append_history_gap(payload: dict[str, Any], gap: str) -> None:
    """Disclose a trim in the proof, so a replayed answer never overclaims."""
    proof = payload.get("proof")
    if not isinstance(proof, dict):
        return
    gaps = proof.get("known_data_gaps")
    if not isinstance(gaps, list):
        gaps = []
    if gap not in gaps:
        proof["known_data_gaps"] = [*gaps, gap]


def _history_title(question: object) -> str:
    text = str(question or "").strip()
    return text[:GENIE_HISTORY_TITLE_MAX]


def _iso(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def list_genie_sessions(
    lakebase: LakebaseClient,
    *,
    actor: str,
) -> GenieSessionListResponse:
    """Most recent conversations owned by ``actor``, newest first."""
    try:
        rows = lakebase.fetchall(
            GENIE_SESSION_LIST_SQL,
            {"actor_email": actor, "limit": GENIE_HISTORY_SESSION_LIMIT},
            limit=GENIE_HISTORY_SESSION_LIMIT,
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    sessions: list[GenieSessionSummary] = []
    for row in rows or []:
        conversation_id = str(row.get("conversation_id") or "")
        if not conversation_id:
            continue
        try:
            turn_count = max(int(row.get("turn_count") or 0), 0)
        except (TypeError, ValueError):
            turn_count = 0
        sessions.append(
            GenieSessionSummary(
                conversation_id=conversation_id,
                title=_history_title(row.get("first_question")),
                last_activity_at=_iso(row.get("last_activity_at")),
                turn_count=turn_count,
            )
        )
    return GenieSessionListResponse(sessions=sessions)


def genie_session_turns(
    lakebase: LakebaseClient,
    *,
    actor: str,
    conversation_id: str,
) -> GenieSessionDetailResponse:
    """Replayable turns of one conversation, in ask order.

    Scoped to ``actor``: another actor's conversation is indistinguishable
    from one that does not exist (404), so history cannot be probed.
    """
    try:
        rows = lakebase.fetchall(
            GENIE_SESSION_TURNS_SQL,
            {
                "actor_email": actor,
                "conversation_id": conversation_id,
                "limit": GENIE_HISTORY_TURN_LIMIT,
            },
            limit=GENIE_HISTORY_TURN_LIMIT,
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="genie conversation not found")
    turns: list[GenieSessionTurn] = []
    for row in rows:
        response = _decode_response(row.get("response_json"))
        if response is None:
            # Recorded before payload persistence, or unreadable after a
            # schema change. Provenance is intact; the turn just cannot be
            # replayed, and we do not invent one.
            continue
        turns.append(
            GenieSessionTurn(
                question=str(row.get("question_text") or response.question or ""),
                response=response,
            )
        )
    return GenieSessionDetailResponse(conversation_id=conversation_id, turns=turns)


def _decode_response(payload: object) -> GenieMessageResponse | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, dict):
        return None
    try:
        return GenieMessageResponse.model_validate(payload)
    except ValidationError:
        return None
