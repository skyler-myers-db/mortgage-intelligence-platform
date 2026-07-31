"""Live-progress lifecycle for in-flight Genie messages.

The synchronous `/api/genie/message` call hides the Genie Conversation API
lifecycle inside one blocking request, which is why the UI historically
showed a static "waiting" card. This module powers the async trio
(`/message/submit` → `/message/progress` → `/message/complete`):

* **Progress tokens** — HMAC-signed claims binding (actor, conversation,
  message, question-hash, expiry) minted at submit. They authorize the
  in-flight window: completed turns are recorded to Lakebase session
  ownership only at finalize (unchanged from the sync path), so without the
  token nobody — including a different workspace actor guessing ids — can
  watch or complete someone else's turn. Signing reuses the audited Genie
  action-token key path (rotation + previous-key grace included).
* **Stage mapping** — the raw Genie message status enum (platform-authored,
  never model text) translated into a bounded, server-owned stage
  vocabulary for the UI timeline.
* **Progress building** — one polled message body → `GenieProgressResponse`
  with the same private-thought → public-process translation the completed
  answer uses (`genie_reasoning_trace_from_thoughts`), plus the generated
  SQL that the completed proof would expose anyway, surfaced early and
  capped. Raw model thoughts never cross the API boundary here either.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import HTTPException

from backend.services.genie_answers import GenieProgressResponse
from backend.services.genie_client import (
    _GENIE_SUCCESS_STATES,
    _GENIE_TERMINAL_ERROR_STATES,
)
from backend.services.repositories.databricks_genie_policy_helpers import (
    genie_reasoning_trace_from_thoughts,
)
from backend.services.repositories.databricks_genie_trust import (
    sql_is_disclosable_preview,
)

#: Fifteen minutes covers the longest observed Genie turn (warehouse cold
#: start included) with margin, while keeping a leaked token short-lived.
_PROGRESS_TOKEN_TTL_S = 15 * 60

_PROGRESS_TOKEN_KIND = "genie_progress"

#: Longest SQL preview the progress body will carry. The completed proof
#: exposes the full statement; the live preview is for orientation, not
#: copy-paste archaeology, and a capped body keeps 1–2s polling cheap.
_SQL_PREVIEW_MAX_CHARS = 4_000

#: Server-owned stage vocabulary. Keys are the Genie message-status enum
#: values observed on the Conversation API; everything unknown maps to the
#: conservative "working" stage rather than inventing a lifecycle.
_STAGE_BY_STATUS: dict[str, tuple[str, str]] = {
    "SUBMITTED": ("understanding", "Genie accepted the question"),
    "FETCHING_METADATA": ("understanding", "Reading governed table metadata"),
    "FILTERING_CONTEXT": ("understanding", "Selecting the governed mortgage context"),
    "ASKING_AI": ("drafting", "Drafting a governed SQL plan"),
    "PENDING_WAREHOUSE": ("executing", "Waking the SQL warehouse"),
    "EXECUTING_QUERY": ("executing", "Running the governed query"),
    "COMPLETED": ("complete", "Answer ready — verifying and formatting"),
    "FAILED": ("failed", "Genie could not complete this question"),
    "CANCELED": ("failed", "The Genie turn was cancelled"),
    "CANCELLED": ("failed", "The Genie turn was cancelled"),
    "EXPIRED": ("failed", "The Genie turn expired before it finished"),
    "TIMEOUT": ("failed", "The Genie turn timed out"),
    "QUERY_RESULT_EXPIRED": ("failed", "The Genie result expired before pickup"),
}

#: Terminal classification DERIVES from the client's polling sets (QA H3:
#: a hand-maintained copy drifted the day it was written — CANCELED/
#: EXPIRED/TIMEOUT were terminal to the client but "working" here, leaving
#: the browser polling a dead turn for the full client-side ceiling).
_TERMINAL_SUCCESS = frozenset(_GENIE_SUCCESS_STATES)
_TERMINAL_FAILURE = frozenset(_GENIE_TERMINAL_ERROR_STATES)

_GENERIC_FAILURE_HINT = (
    "Genie could not complete this turn. Retry, or rephrase the question."
)

_FAILURE_HINTS: dict[str, str] = {
    "FAILED": "Genie reported a failure for this turn. Retry, or rephrase the question.",
    "CANCELED": "This Genie turn was cancelled before it finished.",
    "CANCELLED": "This Genie turn was cancelled before it finished.",
    "EXPIRED": "This Genie turn expired before it finished. Ask the question again.",
    "TIMEOUT": "This Genie turn timed out. Ask the question again.",
    "QUERY_RESULT_EXPIRED": "The Genie result expired before pickup. Ask the question again.",
}


def genie_question_hash(question: str) -> str:
    """Same short-hash shape the sync route stamps into audits."""

    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def genie_question_binding_hash(question: str) -> str:
    """Full-strength digest for the token's question binding.

    The 16-hex audit label is fine as a label but weak as a cryptographic
    pin (2^64 second-preimage), and a forged question at complete would
    flow into the SQL-repair path, which posts new derived messages to
    Genie. Tokens therefore bind the full 256-bit digest; audits keep the
    established short form.
    """

    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def mint_genie_progress_token(
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
    question_hash: str,
) -> str:
    from backend.services.genie_actions import genie_claims_key_id, sign_genie_claims

    claims = {
        "kind": _PROGRESS_TOKEN_KIND,
        "kid": genie_claims_key_id(),
        "actor": actor,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "question_hash": question_hash,
        "exp": int(time.time()) + _PROGRESS_TOKEN_TTL_S,
    }
    return sign_genie_claims(claims)


def verify_genie_progress_token(
    token: str,
    *,
    actor: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Return verified claims or raise 400/403.

    The signature check (including key rotation grace) lives in the shared
    decoder; this layer enforces kind, binding, and expiry so a valid action
    token can never double as a progress token or vice versa.
    """

    from backend.services.genie_actions import decode_genie_claims

    claims = decode_genie_claims(token)
    if str(claims.get("kind") or "") != _PROGRESS_TOKEN_KIND:
        raise HTTPException(status_code=400, detail="Genie progress token is invalid")
    try:
        expires_at = int(claims.get("exp") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= int(time.time()):
        raise HTTPException(status_code=400, detail="Genie progress token has expired")
    if (
        str(claims.get("actor") or "") != actor
        or str(claims.get("conversation_id") or "") != conversation_id
        or str(claims.get("message_id") or "") != message_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Genie progress token does not authorize this message",
        )
    return claims


def _extract_progress_fields(message: dict[str, Any]) -> tuple[list[dict[str, str]], str | None]:
    """Pull raw thoughts + the first generated SQL from a partial message.

    Deliberately tolerant: mid-flight messages carry partially-populated
    attachments. Only structure is read here; thought text goes straight to
    the same public-translation helper the completed answer uses.
    """

    thoughts: list[dict[str, str]] = []
    sql_query: str | None = None
    for att in message.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        query = att.get("query") or {}
        if not isinstance(query, dict):
            continue
        for raw_thought in query.get("thoughts") or []:
            if not isinstance(raw_thought, dict):
                continue
            content = str(raw_thought.get("content") or "").strip()
            if not content:
                continue
            kind = str(raw_thought.get("thought_type") or "thought").strip()
            thoughts.append({"kind": kind, "content": content})
        if sql_query is None and query.get("query"):
            sql_query = str(query["query"]).strip()
    return thoughts, sql_query


def build_genie_progress(message: dict[str, Any]) -> GenieProgressResponse:
    raw_status = str(message.get("status") or message.get("state") or "").strip().upper()
    stage, stage_label = _STAGE_BY_STATUS.get(
        raw_status, ("working", "Genie is working on the question")
    )
    thoughts, sql_query = _extract_progress_fields(message)
    failed = raw_status in _TERMINAL_FAILURE
    terminal = failed or raw_status in _TERMINAL_SUCCESS
    # R5-02 posture: upstream error envelopes routinely quote warehouse
    # error text (column names, predicate values), so no raw error string
    # ships on the wire — a canned per-status hint only. The full envelope
    # stays in backend logs via the complete/resume path.
    error_hint = (
        _FAILURE_HINTS.get(raw_status, _GENERIC_FAILURE_HINT) if failed else None
    )
    # Fail-closed disclosure gate (2026-07-31 adversarial review): only show
    # mid-flight SQL that the completed answer's trust policy would also
    # disclose. A statement destined for policy_blocked never hits the wire.
    disclosable_sql = (
        sql_query[:_SQL_PREVIEW_MAX_CHARS]
        if sql_query and sql_is_disclosable_preview(sql_query)
        else None
    )
    return GenieProgressResponse(
        # Closed vocabulary on the wire: statuses outside the stage map echo
        # as UNKNOWN rather than round-tripping arbitrary upstream strings.
        status=raw_status if raw_status in _STAGE_BY_STATUS else "UNKNOWN",
        stage=stage,
        stage_label=stage_label,
        terminal=terminal,
        failed=failed,
        reasoning_trace=genie_reasoning_trace_from_thoughts(thoughts),
        sql_preview=disclosable_sql,
        error_hint=error_hint,
    )
