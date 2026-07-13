"""Thin Genie router.

Slice-7 posture: `/api/genie/message` delegates to
``DatabricksGenieRepository`` which wraps ``ResilientGenieClient`` with
an honest degraded response gated on the ``genie`` circuit breaker. Happy
path always queries the live Databricks Genie space; no local answer body,
metric, row, or recommendation is served while Genie is reconnecting.

Prior to the 2026-04-22 real-data walkthrough this router could bypass the
live Genie path. That regression has been corrected; production modules now
serve only live Genie/trusted-SQL answers or an explicit degraded-state message.
"""

import hashlib
import re
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.api import genie_guardrails as prompt_guardrails
from backend.config.settings import settings
from backend.services.audit_store import (
    AuditStore,
    get_audit_store,
    resolve_actor,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_actions import handle_genie_action, issue_response_action_tokens
from backend.services.genie_answers import (
    GenieActionRequest,
    GenieActionResponse,
    GenieMessageResponse,
    GenieProof,
    GenieStartResponse,
    load_sample_questions,
)
from backend.services.genie_audit import genie_audit_entity_id, genie_audit_entity_id_from_parts
from backend.services.genie_client import (
    GenieClientError,
    ResilientGenieClient,
    get_genie_client,
)
from backend.services.genie_message_policy import (
    GenieMessageRequest,
)
from backend.services.genie_message_policy import (
    genie_response_has_unsafe_visible_text as _genie_response_has_unsafe_visible_text,
)
from backend.services.genie_message_policy import (
    identity_prompt_match as _identity_prompt_match,
)
from backend.services.genie_message_policy import (
    protected_prompt_match as _protected_prompt_match,
)
from backend.services.genie_sales_ops import sales_ops_genie_response
from backend.services.genie_session_guard import assert_genie_conversation_owned
from backend.services.genie_source_gaps import source_gap_answer
from backend.services.genie_trusted_assets import trusted_assets
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.repositories import BorrowerRepository, GenieAnswerRepository
from backend.services.repositories.factory import (
    get_borrower_repository,
    get_genie_answer_repository,
)
from backend.services.resilience import DependencyDownError
from backend.services.sales_state import SalesStateStore
from backend.services.workspace_store import WorkspaceStore, get_workspace_store

router = APIRouter(prefix="/genie", tags=["genie"])

# Annotated[...] variant of Depends so ruff's B008 stays quiet (Depends
# is not a default *value*; it's FastAPI's dependency marker).
RepoDep = Annotated[GenieAnswerRepository, Depends(get_genie_answer_repository)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]
WorkspaceDep = Annotated[WorkspaceStore, Depends(get_workspace_store)]
BorrowerRepoDep = Annotated[BorrowerRepository, Depends(get_borrower_repository)]
GenieClientDep = Annotated[ResilientGenieClient, Depends(get_genie_client)]

_cross_lender_prompt_match = prompt_guardrails.cross_lender_prompt_match
_footprint_metadata_gap_match = prompt_guardrails.footprint_metadata_gap_match
_instruction_override_prompt_match = prompt_guardrails.instruction_override_prompt_match
_off_topic_prompt_match = prompt_guardrails.off_topic_prompt_match
_outside_footprint_match = prompt_guardrails.outside_footprint_match
_pii_prompt_match = prompt_guardrails.pii_prompt_match
_scope_bypass_prompt_match = prompt_guardrails.scope_bypass_prompt_match
_source_gap_prompt_match = prompt_guardrails.source_gap_prompt_match


def _safe_genie_audit_entity_id(
    payload: GenieMessageRequest,
    *,
    question_hash: str,
    message_id: str | None = None,
    fallback: str = "genie",
) -> str:
    return genie_audit_entity_id_from_parts(
        question=payload.question,
        conversation_id=payload.conversation_id,
        message_id=message_id,
        question_hash=question_hash,
        fallback=fallback,
    )


_LATEST_GENIE_SESSION_SQL = """
SELECT conversation_id
FROM mip_app.genie_sessions
WHERE actor_email = %(actor_email)s
  AND source NOT IN ('degraded', 'policy_blocked', 'refused', 'data_gap', 'out_of_footprint')
ORDER BY updated_at DESC
LIMIT 1
"""


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
  source, row_count, visualization_kind, trusted_assets, request_id
) VALUES (
  %(conversation_id)s, %(message_id)s, %(actor_email)s, %(question_hash)s,
  %(source)s, %(row_count)s, %(visualization_kind)s, %(trusted_assets)s,
  %(request_id)s
)
ON CONFLICT (conversation_id, message_id) DO NOTHING
"""


def _actor(request: Request) -> str:
    if settings.trust_forwarded_headers:
        email = request.headers.get("X-Forwarded-Email")
        if email:
            return email
        user = request.headers.get("X-Forwarded-User")
        if user:
            return user
        raise HTTPException(status_code=401, detail="genie action identity required")
    return resolve_actor(request)


def _safe_audit_write(store: AuditStore, **kwargs: Any) -> None:
    try:
        store.write(**kwargs)
    except Exception:
        # The answer itself must not fail because a best-effort read audit
        # row was unavailable. Governed write actions below still fail closed.
        return


def _required_audit_write(store: AuditStore, **kwargs: Any) -> None:
    try:
        store.write(**kwargs)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


def _latest_genie_conversation(
    lakebase: LakebaseClient,
    *,
    actor: str,
) -> str | None:
    try:
        row = lakebase.fetchone(_LATEST_GENIE_SESSION_SQL, {"actor_email": actor})
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if row is None:
        return None
    conversation_id = row.get("conversation_id")
    return str(conversation_id) if conversation_id else None


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
    params = {
        "actor_email": actor,
        "conversation_id": conversation_id,
        "last_message_id": message_id,
        "last_question_hash": question_hash,
        "source": response.source,
        "trusted_assets": response.trusted_assets,
        "question_hash": question_hash,
        "message_id": message_id,
        "row_count": int(response.row_count or 0),
        "visualization_kind": response.visualization.kind if response.visualization else None,
        "request_id": f"genie-{uuid4()}",
    }
    try:
        lakebase.execute(_GENIE_SESSION_UPSERT_SQL, params)
        lakebase.execute(_GENIE_MESSAGE_INSERT_SQL, params)
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc


def _finalize_genie_response(
    lakebase: LakebaseClient,
    *,
    actor: str,
    response: GenieMessageResponse,
) -> GenieMessageResponse:
    issue_response_action_tokens(response, actor=actor)
    _record_genie_session(lakebase, actor=actor, response=response)
    return response


def _policy_blocked_genie_output_response(
    payload: GenieMessageRequest,
    response: GenieMessageResponse,
) -> GenieMessageResponse:
    question_hash = (
        response.question_hash or hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
    )
    return GenieMessageResponse(
        conversation_id=response.conversation_id or payload.conversation_id or "",
        message_id=response.message_id,
        question="",
        question_hash=question_hash,
        answer=(
            "The generated response did not pass the governed output policy, so it was not "
            "displayed. Ask a scoped Module 0 question using aggregate mortgage signals."
        ),
        source="policy_blocked",
        trusted_assets=[],
        row_count=0,
        proof=GenieProof(
            source_assets=[],
            row_count=0,
            trusted=False,
            filters=[],
            known_data_gaps=["generated response blocked by the governed text-output policy"],
            conversation_id=response.conversation_id or payload.conversation_id,
            message_id=response.message_id,
        ),
        table_rows=[],
    )


def _block_unsafe_genie_output(
    audit: AuditStore,
    *,
    actor: str,
    payload: GenieMessageRequest,
    response: GenieMessageResponse,
) -> GenieMessageResponse:
    """Replace unsafe rendered output and record one non-PII policy event."""

    blocked = _policy_blocked_genie_output_response(payload, response)
    _required_audit_write(
        audit,
        actor=actor,
        action="genie.response_blocked",
        entity_type="genie_message",
        entity_id=_safe_genie_audit_entity_id(
            payload,
            question_hash=blocked.question_hash or "genie",
            message_id=blocked.message_id,
        ),
        payload_json={
            "conversation_id": blocked.conversation_id,
            "message_id": blocked.message_id,
            "question_hash": blocked.question_hash,
            "row_count": 0,
            "source_assets": [],
            "visualization_kind": None,
            "action_type": "response_blocked",
        },
        event_type="RUN_GENIE",
    )
    return blocked


def _refused_genie_response(
    *,
    payload: GenieMessageRequest,
    question_hash: str,
    answer: str,
    known_gap: str,
) -> GenieMessageResponse:
    # PII posture (external audit 2026-07-07): a refused prompt is not
    # round-tripped. The UI renders the question it already holds locally,
    # so the response carries only the hash — nothing typed by the user
    # appears anywhere in a refusal body.
    return GenieMessageResponse(
        conversation_id=payload.conversation_id or "",
        question="",
        answer=answer,
        source="refused",
        trusted_assets=[],
        question_hash=question_hash,
        row_count=0,
        proof=GenieProof(
            source_assets=[],
            row_count=0,
            trusted=False,
            filters=[],
            known_data_gaps=[known_gap],
            conversation_id=payload.conversation_id,
        ),
        table_rows=[],
    )


def _source_readiness_asset() -> str:
    return qualify("gold", "source_readiness")


def _is_outreach_writer_request(question: str) -> bool:
    q = question.lower()
    patterns = (
        r"\bwrite\b.*\b(email|sms|text|message|letter)\b",
        r"\b(email|sms|text|message|letter)\b.*\b(send|write|draft)\b",
        r"\bsend\b.*\b(email|sms|text|message|letter)\b",
        r"\bdraft\b.*\b(email|sms|text|message|letter)\b",
    )
    return any(re.search(pattern, q) for pattern in patterns)


@router.post("/start", response_model=GenieStartResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def genie_start(
    request: Request,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
    payload: dict[str, object] | None = None,
) -> GenieStartResponse:
    _ = payload
    actor = resolve_actor(request)
    return GenieStartResponse(
        conversation_id=_latest_genie_conversation(lakebase, actor=actor),
        trusted_assets=trusted_assets(),
        sample_questions=load_sample_questions()[:4],
    )


@router.post("/message", response_model=GenieMessageResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def genie_message(
    payload: GenieMessageRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    borrower_repo: BorrowerRepoDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieMessageResponse:
    actor = resolve_actor(request)
    assert_genie_conversation_owned(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
    )
    protected_term = _protected_prompt_match(payload.question)
    if protected_term:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.refused_prompt",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "refused_prompt",
                "refusal_reason": "protected_class",
            },
            event_type="RUN_GENIE",
        )
        response = _refused_genie_response(
            payload=payload,
            question_hash=question_hash,
            answer=(
                "For fair-lending compliance, I cannot segment, score, rank, "
                "or target borrowers using protected-class attributes or "
                "proxies. Ask for a permitted Module 0 strategy using trusted "
                "mortgage, lien, equity, segment, and offer signals."
            ),
            known_gap="prompt refused before Genie execution due protected-class term in the prompt",
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    override_match = prompt_guardrails.instruction_override_prompt_match(payload.question)
    if override_match:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.refused_prompt",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "refused_prompt",
                "refusal_reason": "instruction_override",
            },
            event_type="RUN_GENIE",
        )
        response = _refused_genie_response(
            payload=payload,
            question_hash=question_hash,
            answer=(
                "I cannot follow attempts to override system, developer, or "
                "safety instructions. Ask a scoped Module 0 mortgage analytics "
                "question over trusted lead, lien, equity, segment, and offer signals."
            ),
            known_gap="prompt refused before Genie execution due instruction-override pattern",
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    if _is_outreach_writer_request(payload.question):
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.outreach_guardrail",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "outreach_guardrail",
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            # Refusals never round-trip the prompt (external audit 2026-07-08:
            # this inline block predated _refused_genie_response and kept the
            # echo the helper-built refusals had already dropped).
            question="",
            answer=(
                "Use governed outreach workflow for borrower communications. "
                "Genie can size the cohort, explain why borrowers qualify, and "
                "create an audited draft campaign, but borrower-specific email "
                "or SMS copy must stay in the Offer Orchestrator / outreach "
                "review path with explicit human approval."
            ),
            source="refused",
            trusted_assets=[],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                filters=[],
                known_data_gaps=[],
                conversation_id=payload.conversation_id,
            ),
            follow_up_questions=load_sample_questions()[:2],
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    pii_match = prompt_guardrails.pii_prompt_match(payload.question)
    if pii_match is None and _identity_prompt_match(payload.question):
        pii_match = "person_name"
    if pii_match:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.refused_prompt",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "refused_prompt",
                "refusal_reason": "pii_request",
            },
            event_type="RUN_GENIE",
        )
        response = _refused_genie_response(
            payload=payload,
            question_hash=question_hash,
            answer=(
                "I do not return borrower names, street-level addresses, raw contact "
                "details, raw servicer strings, or other personal identifiers. Ask "
                "for aggregated counts or masked borrower IDs with score, segment, "
                "offer, and evidence instead."
            ),
            known_gap="prompt refused before Genie execution due PII request pattern",
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    scope_bypass_match = prompt_guardrails.scope_bypass_prompt_match(payload.question)
    if scope_bypass_match:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.refused_prompt",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "refused_prompt",
                "refusal_reason": "scope_bypass",
            },
            event_type="RUN_GENIE",
        )
        response = _refused_genie_response(
            payload=payload,
            question_hash=question_hash,
            answer=(
                "This space is read-only and scoped to governed Module 0 mortgage "
                "analytics over trusted assets. I cannot enumerate schemas, query "
                "outside the trusted assets, or run DDL/DML. Ask a scoped borrower, "
                "segment, geography, trigger, or offer question instead."
            ),
            known_gap="prompt refused before Genie execution due scope-bypass pattern",
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    source_gap_match = prompt_guardrails.source_gap_prompt_match(payload.question)
    if source_gap_match:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        source_readiness_asset = _source_readiness_asset()
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.source_gap",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [source_readiness_asset],
                "visualization_kind": None,
                "action_type": "source_gap",
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            question=payload.question,
            answer=source_gap_answer(
                payload.question,
                source_gap_match,
                source=source_readiness_asset,
            ),
            source="data_gap",
            trusted_assets=[source_readiness_asset],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[source_readiness_asset],
                row_count=0,
                trusted=False,
                filters=[],
                known_data_gaps=[
                    f"prompt classified as source-gap before Genie execution: {source_gap_match}"
                ],
                conversation_id=payload.conversation_id,
            ),
            follow_up_questions=load_sample_questions()[:2],
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    off_topic_match = prompt_guardrails.off_topic_prompt_match(payload.question)
    if off_topic_match:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.refused_prompt",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "refused_prompt",
                "refusal_reason": "out_of_scope",
            },
            event_type="RUN_GENIE",
        )
        response = _refused_genie_response(
            payload=payload,
            question_hash=question_hash,
            answer=(
                "I can answer questions about borrower segments, scores, geography, "
                "triggers, and offers across the current Cotality data coverage. "
                "That request is outside the Module 0 mortgage analytics scope."
            ),
            known_gap="prompt refused before Genie execution due off-topic pattern",
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    cross_lender_match = prompt_guardrails.cross_lender_prompt_match(payload.question)
    if cross_lender_match:
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.refused_prompt",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "refused_prompt",
                "refusal_reason": "out_of_scope",
            },
            event_type="RUN_GENIE",
        )
        response = _refused_genie_response(
            payload=payload,
            question_hash=question_hash,
            answer=(
                "I can answer questions about the tenant lender's borrower data: "
                "segments, scores, geography, triggers, and offers across the "
                "current Cotality data coverage. Requests for third-party lender "
                "or lead-vendor customer lists are outside the Module 0 mortgage "
                "analytics scope."
            ),
            known_gap=(
                "prompt refused before Genie execution due cross-lender customer-list pattern"
            ),
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    try:
        sales_ops_response = sales_ops_genie_response(
            lakebase,
            borrower_repo,
            actor=actor,
            question=payload.question,
            conversation_id=payload.conversation_id,
        )
        sales_ops_staff_labels = [
            member.display_label
            for member in SalesStateStore(lakebase).list_team(actor=actor)
        ] if sales_ops_response is not None else []
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    if sales_ops_response is not None:
        if _genie_response_has_unsafe_visible_text(
            sales_ops_response,
            allowed_literals=sales_ops_staff_labels,
        ):
            blocked = _block_unsafe_genie_output(
                audit,
                actor=actor,
                payload=payload,
                response=sales_ops_response,
            )
            return _finalize_genie_response(lakebase, actor=actor, response=blocked)
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.sales_ops_query",
            entity_type="genie_message",
            entity_id=genie_audit_entity_id(sales_ops_response),
            payload_json={
                "conversation_id": sales_ops_response.conversation_id,
                "message_id": sales_ops_response.message_id,
                "question_hash": sales_ops_response.question_hash,
                "row_count": sales_ops_response.row_count or 0,
                "source_assets": sales_ops_response.trusted_assets,
                "visualization_kind": None,
                "action_type": "sales_ops_query",
            },
            event_type="RUN_GENIE",
        )
        return _finalize_genie_response(lakebase, actor=actor, response=sales_ops_response)
    metadata_gap = prompt_guardrails.footprint_metadata_gap_match(payload.question)
    if metadata_gap is not None:
        state_name, state_code = metadata_gap
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.footprint_metadata_gap",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "footprint_metadata_gap",
                "requested_state": state_code,
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            question=payload.question,
            answer=(
                f"I cannot answer the {state_name} ({state_code}) scope because the "
                "current gold geography coverage is temporarily unavailable. I will not "
                "fall back to generic US-state metadata for a data-bearing answer; "
                "retry after the footprint and geography rollups reconnect."
            ),
            source="data_gap",
            trusted_assets=[],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                filters=[f"requested_state = {state_code}"],
                known_data_gaps=[
                    "Gold geography coverage unavailable; generic geography metadata is not a data scope."
                ],
                conversation_id=payload.conversation_id,
            ),
            follow_up_questions=load_sample_questions()[:2],
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    outside_footprint = prompt_guardrails.outside_footprint_match(payload.question)
    if outside_footprint is not None:
        state_name, state_code, footprint_codes = outside_footprint
        question_hash = hashlib.sha256(payload.question.encode("utf-8")).hexdigest()[:16]
        _ = background
        footprint_label = ", ".join(footprint_codes)
        footprint_view = (
            f"full {len(footprint_codes)}-state coverage view"
            if footprint_codes
            else "full coverage view"
        )
        _required_audit_write(
            audit,
            actor=actor,
            action="genie.outside_footprint",
            entity_type="genie_message",
            entity_id=_safe_genie_audit_entity_id(payload, question_hash=question_hash),
            payload_json={
                "conversation_id": payload.conversation_id,
                "message_id": None,
                "question_hash": question_hash,
                "row_count": 0,
                "source_assets": [],
                "visualization_kind": None,
                "action_type": "outside_footprint",
                "requested_state": state_code,
                "footprint_states": footprint_codes,
            },
            event_type="RUN_GENIE",
        )
        response = GenieMessageResponse(
            conversation_id=payload.conversation_id or "",
            question=payload.question,
            answer=(
                f"{state_name} ({state_code}) is outside the current refreshed data "
                f"footprint coverage ({footprint_label}). I will not treat that "
                f"coverage gap as zero borrower demand. Ask for one of the covered "
                f"states, or ask for the {footprint_view}."
            ),
            source="out_of_footprint",
            trusted_assets=[],
            question_hash=question_hash,
            row_count=0,
            proof=GenieProof(
                source_assets=[],
                row_count=0,
                trusted=False,
                filters=[f"requested_state = {state_code}"],
                known_data_gaps=[
                    f"{state_code} is outside the current refreshed data coverage: {footprint_label}"
                ],
                conversation_id=payload.conversation_id,
            ),
            follow_up_questions=load_sample_questions()[:2],
            table_rows=[],
        )
        return _finalize_genie_response(lakebase, actor=actor, response=response)
    # repo.respond() returns a GenieMessageResponse by contract; the
    # protocol annotates `object` only to dodge a forward-import cycle.
    try:
        result = repo.respond(payload.question, conversation_id=payload.conversation_id)
    except GenieClientError as exc:
        raise DependencyDownError(
            "genie",
            reason="genie client returned an unrecoverable response",
            last_error=exc,
            kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
        ) from exc
    if _genie_response_has_unsafe_visible_text(result):  # type: ignore[arg-type]
        blocked = _block_unsafe_genie_output(
            audit,
            actor=actor,
            payload=payload,
            response=result,  # type: ignore[arg-type]
        )
        return _finalize_genie_response(lakebase, actor=actor, response=blocked)
    _ = background
    _required_audit_write(
        audit,
        actor=actor,
        action="genie.run_query",
        entity_type="genie_message",
        entity_id=genie_audit_entity_id(result),
        payload_json={
            "conversation_id": result.conversation_id,
            "message_id": result.message_id,
            "question_hash": result.question_hash,
            "row_count": result.row_count or 0,
            "source_assets": result.trusted_assets,
            "visualization_kind": result.visualization.kind if result.visualization else None,
        },
        event_type="RUN_GENIE",
    )
    return _finalize_genie_response(lakebase, actor=actor, response=result)  # type: ignore[arg-type]


@router.post("/actions", response_model=GenieActionResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def genie_action(
    payload: GenieActionRequest,
    request: Request,
    audit: AuditDep,
    workspace: WorkspaceDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieActionResponse:
    _ = audit
    actor = _actor(request)
    return handle_genie_action(
        payload,
        actor=actor,
        workspace=workspace,
        lakebase=lakebase,
    )
