"""The governed tail of a Genie turn: action tokens, then the session record.

Moved verbatim out of ``backend/api/genie.py`` (audit 2026-09-21 genie-01) so
the completion runner (``genie_completion_runner``) and the router share one
implementation. ``backend.api.genie`` re-imports every name, so existing
imports of ``_record_genie_session`` from the router keep working.
"""

import hashlib
from uuid import uuid4

from fastapi import HTTPException

from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_actions import issue_response_action_tokens
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_history import history_payload_json, history_question_text
from backend.services.lakebase import LakebaseClient

_GENIE_SESSION_UPSERT_SQL = """
INSERT INTO mip_app.genie_sessions (
  actor_email, conversation_id, last_message_id, last_question_hash,
  source, trusted_assets, updated_at
) VALUES (
  %(actor_email)s, %(conversation_id)s, %(last_message_id)s, %(last_question_hash)s,
  %(source)s, %(trusted_assets)s, now()
)
ON CONFLICT (actor_email, conversation_id) DO UPDATE SET
  last_message_id = EXCLUDED.last_message_id,
  last_question_hash = EXCLUDED.last_question_hash,
  source = EXCLUDED.source,
  trusted_assets = EXCLUDED.trusted_assets,
  updated_at = now()
"""

_GENIE_MESSAGE_INSERT_SQL = """
INSERT INTO mip_app.genie_messages (
  conversation_id, message_id, actor_email, question_hash,
  source, row_count, visualization_kind, trusted_assets, request_id,
  question_text, response_json
) VALUES (
  %(conversation_id)s, %(message_id)s, %(actor_email)s, %(question_hash)s,
  %(source)s, %(row_count)s, %(visualization_kind)s, %(trusted_assets)s,
  %(request_id)s, %(question_text)s, %(response_json)s::jsonb
)
ON CONFLICT (conversation_id, message_id) DO NOTHING
"""


def _record_genie_session(
    lakebase: LakebaseClient,
    *,
    actor: str,
    response: GenieMessageResponse,
) -> None:
    conversation_id = response.conversation_id
    if not conversation_id:
        return
    if response.source in {"degraded", "policy_blocked", "refused", "data_gap", "out_of_footprint"}:
        return
    question_hash = (
        response.question_hash or hashlib.sha256(response.question.encode("utf-8")).hexdigest()[:16]
    )
    message_id = response.message_id or f"{response.source}-{question_hash}"
    # A governed canonical overlay can preserve the native Conversation API
    # identity while presenting its re-verified answer as ``trusted_sql``.
    # Ownership for feedback belongs to that native message, not to the
    # presentation label. Deterministic fallbacks have no completed native
    # identity and retain their own source, so they cannot acquire feedback
    # rights accidentally.
    ownership_source = (
        "genie"
        if response.message_id and response.conversation_id and response.genie_status == "COMPLETED"
        else response.source
    )
    params = {
        "actor_email": actor,
        "conversation_id": conversation_id,
        "last_message_id": message_id,
        "last_question_hash": question_hash,
        "source": ownership_source,
        "trusted_assets": response.trusted_assets,
        "question_hash": question_hash,
        "message_id": message_id,
        "row_count": int(response.row_count or 0),
        "visualization_kind": response.visualization.kind if response.visualization else None,
        "request_id": f"genie-{uuid4()}",
        # History replay (2026-08-06). Both fields are already governed: the
        # question cleared the prompt guard battery and the answer cleared the
        # visible-text output policy plus row PII redaction. Signed action
        # tokens are stripped by ``history_payload_json``.
        "question_text": history_question_text(response.question),
        "response_json": history_payload_json(response),
    }
    try:
        if getattr(lakebase, "_supports_atomic_transactions", False):
            with lakebase.transaction() as conn:
                conn.execute(_GENIE_SESSION_UPSERT_SQL, params)
                conn.execute(_GENIE_MESSAGE_INSERT_SQL, params)
        else:
            # Minimal unit fakes retain the two-call surface. Every deployed
            # Lakebase client advertises atomic transaction support.
            lakebase.execute(_GENIE_SESSION_UPSERT_SQL, params)
            lakebase.execute(_GENIE_MESSAGE_INSERT_SQL, params)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


def _finalize_genie_response(
    lakebase: LakebaseClient,
    *,
    actor: str,
    response: GenieMessageResponse,
    live_campaign_run_marker: str | None = None,
) -> GenieMessageResponse:
    issue_response_action_tokens(
        response,
        actor=actor,
        live_campaign_run_marker=live_campaign_run_marker,
    )
    _record_genie_session(lakebase, actor=actor, response=response)
    return response
