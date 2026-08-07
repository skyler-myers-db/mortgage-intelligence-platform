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
import logging
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
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_actions import (
    handle_genie_action,
    issue_response_action_tokens,
    normalize_live_campaign_run_marker,
)
from backend.services.genie_answers import (
    GenieActionRequest,
    GenieActionResponse,
    GenieMessageResponse,
    GenieProgressResponse,
    GenieStartResponse,
    GenieSubmitResponse,
    load_sample_questions,
)
from backend.services.genie_audit import genie_audit_entity_id
from backend.services.genie_client import (
    GenieClientError,
    ResilientGenieClient,
    get_genie_client,
)
from backend.services.genie_deterministic import (
    _block_unsafe_genie_output,
    _deterministic_genie_response,
    _required_audit_write,
    _safe_genie_audit_entity_id,
)
from backend.services.genie_message_policy import (
    GenieCompleteRequest,
    GenieMessageRequest,
    GenieProgressRequest,
)
from backend.services.genie_message_policy import (
    genie_response_has_unsafe_visible_text as _genie_response_has_unsafe_visible_text,
)
from backend.services.genie_progress import (
    build_genie_progress,
    genie_question_binding_hash,
    genie_question_hash,
    mint_genie_progress_token,
    verify_genie_progress_token,
)
from backend.services.genie_session_guard import (
    GENIE_MESSAGE_OWNERSHIP_SQL,
    assert_genie_conversation_owned,
)
from backend.services.genie_trusted_assets import trusted_assets
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.observability import emit
from backend.services.rbac import resolve_workflow_actor
from backend.services.repositories import BorrowerRepository, GenieAnswerRepository
from backend.services.repositories.factory import (
    get_borrower_repository,
    get_genie_answer_repository,
)
from backend.services.resilience import DependencyDownError
from backend.services.workspace_store import WorkspaceStore, get_workspace_store

log = logging.getLogger("mip-genie")

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


def _safe_audit_write(store: AuditStore, **kwargs: Any) -> None:
    try:
        store.write(**kwargs)
    except Exception:
        # The answer itself must not fail because a best-effort read audit
        # row was unavailable. Governed write actions below still fail closed.
        return


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
    try:
        live_campaign_run_marker = normalize_live_campaign_run_marker(
            request.headers.get("X-MIP-Live-Campaign-Run-Marker")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="live campaign run marker is invalid") from exc

    def finalize(response: GenieMessageResponse) -> GenieMessageResponse:
        return _finalize_genie_response(
            lakebase,
            actor=actor,
            response=response,
            live_campaign_run_marker=live_campaign_run_marker,
        )

    assert_genie_conversation_owned(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
    )
    deterministic = _deterministic_genie_response(
        payload,
        actor=actor,
        audit=audit,
        background=background,
        lakebase=lakebase,
        borrower_repo=borrower_repo,
    )
    if deterministic is not None:
        return finalize(deterministic)
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
        return finalize(blocked)
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
    return finalize(result)  # type: ignore[arg-type]




@router.post(
    "/message/submit",
    response_model=GenieSubmitResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def genie_message_submit(
    payload: GenieMessageRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    borrower_repo: BorrowerRepoDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieSubmitResponse:
    """Async lifecycle step 1: guard the prompt, then create the message.

    Runs the identical deterministic battery as the synchronous endpoint
    (via the shared ``_deterministic_genie_response``). Deterministic turns
    — refusals, sales-ops, footprint, degraded fallbacks — resolve inline
    with ``completed=True`` so they stay instant and fully audited. Live
    turns return ``(conversation_id, message_id)`` plus a signed progress
    token that authorizes the in-flight window; session ownership is still
    recorded only at completion, exactly like the sync path.
    """
    actor = resolve_actor(request)
    try:
        live_campaign_run_marker = normalize_live_campaign_run_marker(
            request.headers.get("X-MIP-Live-Campaign-Run-Marker")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="live campaign run marker is invalid") from exc
    assert_genie_conversation_owned(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
    )
    deterministic = _deterministic_genie_response(
        payload,
        actor=actor,
        audit=audit,
        background=background,
        lakebase=lakebase,
        borrower_repo=borrower_repo,
    )
    if deterministic is not None:
        response = _finalize_genie_response(
            lakebase,
            actor=actor,
            response=deterministic,
            live_campaign_run_marker=live_campaign_run_marker,
        )
        return GenieSubmitResponse(
            completed=True,
            conversation_id=response.conversation_id or None,
            message_id=response.message_id,
            question_hash=response.question_hash,
            response=response,
        )
    def _inline_repo_resolution() -> GenieSubmitResponse:
        """Resolve the turn through the repository, sync-endpoint style.

        Shared by the legacy interceptor-first posture and the Genie-down
        fallback. Mirrors the synchronous live tail exactly: output-policy
        check, the genie.run_query audit row (QA/adversarial review 2026-07-31
        — a resolved turn must never lack one), and finalize.
        """
        result = repo.respond(payload.question, conversation_id=payload.conversation_id)
        if _genie_response_has_unsafe_visible_text(result):  # type: ignore[arg-type]
            resolved = _block_unsafe_genie_output(
                audit,
                actor=actor,
                payload=payload,
                response=result,  # type: ignore[arg-type]
            )
        else:
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
                    "visualization_kind": (
                        result.visualization.kind if result.visualization else None
                    ),
                },
                event_type="RUN_GENIE",
            )
            resolved = result  # type: ignore[assignment]
        response = _finalize_genie_response(
            lakebase,
            actor=actor,
            response=resolved,  # type: ignore[arg-type]
            live_campaign_run_marker=live_campaign_run_marker,
        )
        return GenieSubmitResponse(
            completed=True,
            conversation_id=response.conversation_id or None,
            message_id=response.message_id,
            question_hash=response.question_hash,
            response=response,
        )

    if not settings.mip_genie_live_first:
        # Legacy/emergency posture (offline or rate-limited booth operation):
        # the synchronous endpoint consults the reviewed canonical catalog
        # BEFORE any live Genie call. The async lifecycle honors the same
        # posture by resolving the whole turn here instead of creating a live
        # message (QA review H2 — submit previously bypassed the posture).
        return _inline_repo_resolution()
    try:
        conversation_id, message_id = get_genie_client().submit_message(
            payload.question, conversation_id=payload.conversation_id
        )
    except DependencyDownError:
        # Genie is unavailable right now (breaker open / retries exhausted on
        # the submission call). Resolve the turn synchronously through the
        # same repository pipeline as the sync endpoint — reviewed canonical
        # fallback when one applies, honest degraded message otherwise.
        return _inline_repo_resolution()
    except GenieClientError as exc:
        raise DependencyDownError(
            "genie",
            reason="genie client returned an unrecoverable response",
            last_error=exc,
            kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
        ) from exc
    question_hash = genie_question_hash(payload.question)
    question_binding_hash = genie_question_binding_hash(payload.question)
    # The live submission itself mutates external state (a message now exists
    # in the governed Genie conversation) even if the browser never completes
    # the turn, so it gets its own durable audit row. Completion writes the
    # existing genie.run_query row exactly like the synchronous path.
    _required_audit_write(
        audit,
        actor=actor,
        action="genie.message_submitted",
        entity_type="genie_message",
        entity_id=_safe_genie_audit_entity_id(
            payload,
            question_hash=question_hash,
            message_id=message_id,
        ),
        payload_json={
            "conversation_id": conversation_id,
            "message_id": message_id,
            "question_hash": question_hash,
            "row_count": 0,
            "source_assets": [],
            "visualization_kind": None,
            "action_type": "message_submitted",
        },
        event_type="RUN_GENIE",
    )
    return GenieSubmitResponse(
        completed=False,
        conversation_id=conversation_id,
        message_id=message_id,
        progress_token=mint_genie_progress_token(
            actor=actor,
            conversation_id=conversation_id,
            message_id=message_id,
            # Full 256-bit binding in the token; the wire/audit field keeps
            # the established short label.
            question_hash=question_binding_hash,
        ),
        question_hash=question_hash,
    )


@router.post(
    "/message/progress",
    response_model=GenieProgressResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def genie_message_progress(
    payload: GenieProgressRequest,
    request: Request,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieProgressResponse:
    """Async lifecycle step 2: one pure poll of the in-flight message.

    Token-authorized, side-effect free, and bounded: the response carries
    the platform status enum, our server-owned stage vocabulary, the same
    public process steps the completed answer would expose, and the
    generated SQL (already part of the completed proof contract) — never
    raw model thoughts or upstream error text.
    """
    # AUDIT EXEMPT: read-only progress poll — the token-authorized peek
    # mutates nothing; submission and completion carry the audit rows.
    actor = resolve_actor(request)
    verify_genie_progress_token(
        payload.progress_token,
        actor=actor,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
    )
    try:
        message = get_genie_client().peek_message(payload.conversation_id, payload.message_id)
    except (GenieClientError, TimeoutError, OSError) as exc:
        # TimeoutError/OSError: the breaker-free peek path bypasses the
        # resilience wrapper, so a socket read timeout would otherwise
        # surface as a raw 500 instead of the structured 503 (QA L1).
        raise DependencyDownError(
            "genie",
            reason="genie progress peek failed",
            last_error=exc,
            kind=DependencyDownError.KIND_RETRIES_EXHAUSTED,
        ) from exc
    return build_genie_progress(message)


@router.post(
    "/message/complete",
    response_model=GenieMessageResponse,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def genie_message_complete(
    payload: GenieCompleteRequest,
    request: Request,
    repo: RepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieMessageResponse:
    """Async lifecycle step 3: governed completion of the submitted turn.

    The token proves the same actor's submit created this exact message and
    the hash check pins ``question`` to the prompt that passed the guard
    battery there, so the output-policy check, audit write, action-token
    issuance, and session recording below stay byte-identical in meaning to
    the synchronous endpoint's live tail. No conversation-ownership lookup
    here: for a fresh conversation the Lakebase row intentionally does not
    exist until this very call finalizes — the token is the authorization.
    """
    actor = resolve_actor(request)
    try:
        live_campaign_run_marker = normalize_live_campaign_run_marker(
            request.headers.get("X-MIP-Live-Campaign-Run-Marker")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="live campaign run marker is invalid") from exc
    claims = verify_genie_progress_token(
        payload.progress_token,
        actor=actor,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
    )
    if genie_question_binding_hash(payload.question) != str(claims.get("question_hash") or ""):
        raise HTTPException(
            status_code=400,
            detail="question does not match the submitted Genie turn",
        )
    guard_payload = GenieMessageRequest(
        question=payload.question,
        conversation_id=payload.conversation_id,
    )
    try:
        result = repo.respond_existing(
            payload.question,
            conversation_id=payload.conversation_id,
            message_id=payload.message_id,
        )
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
            payload=guard_payload,
            response=result,  # type: ignore[arg-type]
        )
        return _finalize_genie_response(
            lakebase,
            actor=actor,
            response=blocked,
            live_campaign_run_marker=live_campaign_run_marker,
        )
    # Replay hygiene (2026-07-31 adversarial review): the stateless token
    # verifies for its full TTL, so a re-sent complete would otherwise write
    # a duplicate genie.run_query row for one turn and inflate RUN_GENIE
    # counts against one message id. The durable genie_messages row from the
    # first completion is the replay marker; repeats re-serve the governed
    # answer without a second audit row.
    already_recorded = False
    if result.message_id:
        try:
            already_recorded = (
                lakebase.fetchone(
                    GENIE_MESSAGE_OWNERSHIP_SQL,
                    {
                        "actor_email": actor,
                        "conversation_id": payload.conversation_id,
                        "message_id": result.message_id,
                    },
                )
                is not None
            )
        except LakebaseError:
            # Fail toward auditing: an unreadable ledger must never suppress
            # the audit row for a turn that may not have one yet.
            already_recorded = False
    if already_recorded:
        emit(
            log,
            "genie_complete_replayed",
            dependency="lakebase",
            outcome="deduplicated",
            conversation_id=payload.conversation_id,
            message_id=result.message_id,
        )
    else:
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
    return _finalize_genie_response(
        lakebase,
        actor=actor,
        response=result,  # type: ignore[arg-type]
        live_campaign_run_marker=live_campaign_run_marker,
    )


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
    actor = resolve_workflow_actor(request)
    return handle_genie_action(
        payload,
        actor=actor,
        workspace=workspace,
        lakebase=lakebase,
    )
