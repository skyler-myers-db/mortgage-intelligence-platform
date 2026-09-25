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

import logging
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.api import genie_guardrails as prompt_guardrails
from backend.config.settings import settings
from backend.services.audit_store import (
    AuditStore,
    get_audit_store,
    resolve_actor,
)
from backend.services.backpressure import adopt_request_slot
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.genie_actions import (
    handle_genie_action,
    normalize_live_campaign_run_marker,
)
from backend.services.genie_answers import (
    GenieActionRequest,
    GenieActionResponse,
    GenieCancelResponse,
    GenieCompletionJobStatus,
    GenieMessageResponse,
    GenieProgressResponse,
    GenieSessionDetailResponse,
    GenieSessionListResponse,
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
from backend.services.genie_completion_cancel import (
    GenieCancelRequest,
    request_cancel,
    require_completion_jobs,
)
from backend.services.genie_completion_delivery import job_status
from backend.services.genie_completion_durations import typical_seconds_for
from backend.services.genie_completion_jobs import (
    adoptable,
    completion_jobs_available,
    create_or_join,
    job_turn_ids_eligible,
    read_for_actor,
)
from backend.services.genie_completion_runner import (
    GenieCompleteAsyncRequest,
    GenieCompletionJobStatusRequest,
    GovernedTurn,
    await_joined_job,
    complete_governed_turn,
    run_completion_job,
    run_inline_job,
)
from backend.services.genie_deterministic import (
    _block_unsafe_genie_output,
    _deterministic_genie_response,
    _required_audit_write,
    _safe_genie_audit_entity_id,
)
from backend.services.genie_history import (
    genie_session_turns,
    list_genie_sessions,
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
    genie_turn_is_deep,
    mint_genie_progress_token,
    verify_genie_progress_token,
)
from backend.services.genie_session_guard import assert_genie_conversation_owned
from backend.services.genie_trusted_assets import trusted_assets
from backend.services.genie_turn_record import (  # noqa: F401 - compatibility re-exports
    _GENIE_MESSAGE_INSERT_SQL,
    _GENIE_SESSION_UPSERT_SQL,
    _finalize_genie_response,
    _record_genie_session,
)
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.observability import emit, get_correlation_id
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


@router.get("/sessions", response_model=GenieSessionListResponse)
def genie_sessions(
    request: Request,
    lakebase: LakebaseDep,
) -> GenieSessionListResponse:
    """The requesting actor's recent Ask Genie conversations, newest first.

    AUDIT EXEMPT: read-only listing of the caller's own conversation
    metadata. It mutates nothing, and the underlying turns each carry their
    own ``genie.run_query`` audit row from when they were answered.
    """
    return list_genie_sessions(lakebase, actor=resolve_actor(request))


@router.get("/sessions/{conversation_id}", response_model=GenieSessionDetailResponse)
def genie_session_detail(
    conversation_id: str,
    request: Request,
    lakebase: LakebaseDep,
) -> GenieSessionDetailResponse:
    """Replay one owned conversation as governed question/answer turns.

    Strictly actor-scoped: a conversation owned by anyone else is a 404, so
    history cannot be probed for other actors' activity. Answers are re-served
    exactly as they were governed at answer time (output policy + PII
    redaction already applied before persistence), minus the signed action
    tokens, which are never replayed.

    AUDIT EXEMPT: read-only replay of the caller's own recorded turns.
    """
    return genie_session_turns(
        lakebase,
        actor=resolve_actor(request),
        conversation_id=conversation_id,
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
        # Known now, from the question alone: lets the UI label the long
        # completion wait as deep research instead of "Answer ready".
        deep=genie_turn_is_deep(payload.question),
        completion_jobs=(
            job_turn_ids_eligible(conversation_id, message_id)
            and completion_jobs_available(lakebase)
        ),
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


def _verified_turn(
    payload: GenieCompleteRequest,
    request: Request,
) -> tuple[str, str | None, dict[str, Any]]:
    """Actor, live-campaign marker and token claims for a submitted turn.

    The token proves the same actor's submit created this exact message and
    the hash check pins ``question`` to the prompt that passed the guard
    battery there.
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
    return actor, live_campaign_run_marker, claims


@router.post(
    "/message/complete",
    response_model=GenieMessageResponse,
    responses={**JSON_CONTENT_TYPE_RESPONSE, 202: {"model": GenieCompletionJobStatus}},
)
def genie_message_complete(
    payload: GenieCompleteAsyncRequest,
    request: Request,
    repo: RepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieMessageResponse | JSONResponse:
    """Async lifecycle step 3: governed completion of the submitted turn.

    One job per (actor, conversation, message) makes the output-policy
    check, the RUN_GENIE audit write, action-token issuance and session
    recording single-shot: a reloaded or retried complete joins the job.
    ``respond_async`` answers 202 with the job's status (the browser polls
    ``/message/status``); older tabs get today's 200 answer. Without the job
    table (App ahead of its migration) an older tab's tail runs inline with no
    job; an async request is refused with a non-retryable 503, because the
    browser re-sends it and a job-less run could not be joined. No
    conversation-ownership lookup here: for a fresh conversation the Lakebase
    row intentionally does not exist until this very call finalizes — the
    token is the authorization.
    """
    actor, live_campaign_run_marker, claims = _verified_turn(payload, request)
    turn = GovernedTurn(
        actor=actor,
        question=payload.question,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        live_campaign_run_marker=live_campaign_run_marker,
        repo=repo,
        audit=audit,
        lakebase=lakebase,
    )
    if not (
        job_turn_ids_eligible(payload.conversation_id, payload.message_id)
        and completion_jobs_available(lakebase)
    ):
        if payload.respond_async:
            # The browser sends respond_async only after submit advertised
            # jobs, and it re-sends it (timeout, reload). A job-less inline
            # run could not be joined, so that re-send would run the governed
            # tail and write RUN_GENIE a second time. Refuse before any Genie
            # work; no retryable flag, so the transport does not re-send.
            emit(
                log,
                "genie_complete_async_refused",
                level=logging.WARNING,
                dependency="lakebase",
                outcome="refused",
            )
            raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase"))
        return complete_governed_turn(turn)
    enrollment = create_or_join(
        lakebase,
        actor=actor,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        question_hash=str(claims.get("question_hash") or ""),
        expires_at_epoch=int(claims.get("exp") or 0),
        deep=genie_turn_is_deep(payload.question),
    )
    # Risk 4 (genie-01): a queued job this process created but never ran (its
    # creating request failed before enqueueing it) is run by the retry.
    run_here = enrollment.created or adoptable(enrollment.job)
    if run_here and not enrollment.created:
        emit(log, "genie_job_enqueued", dependency="lakebase", outcome="adopted", job_id=enrollment.job.job_id)
    if payload.respond_async:
        if run_here:
            run_completion_job(
                turn,
                enrollment.job,
                slot=adopt_request_slot(request),
                correlation_id=get_correlation_id(),
            )
        status = job_status(
            enrollment.job,
            question=payload.question,
            actor=actor,
            live_campaign_run_marker=live_campaign_run_marker,
            typical_seconds=typical_seconds_for(lakebase, enrollment.job),
        )
        return JSONResponse(status_code=202, content=status.model_dump(mode="json"))
    if run_here:
        return run_inline_job(turn, enrollment.job)
    return await_joined_job(turn, enrollment.job)


@router.post(
    "/message/status",
    response_model=GenieCompletionJobStatus,
    responses=JSON_CONTENT_TYPE_RESPONSE,
)
def genie_message_status(
    payload: GenieCompletionJobStatusRequest,
    request: Request,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieCompletionJobStatus:
    """Poll the caller's own completion job; the answer once it succeeded.

    No Genie call. The stored answer is not re-scanned: it passed the output
    guard before it was stored, the same posture as history replay. Its
    question comes back from this hash-checked request and its actions are
    re-signed for the caller, since the job row holds neither.
    """
    # AUDIT EXEMPT: read-only poll of the caller's own completion job; the
    # job's completion wrote the turn's RUN_GENIE row exactly once.
    actor, live_campaign_run_marker, _claims = _verified_turn(payload, request)
    job = read_for_actor(
        lakebase,
        job_id=payload.job_id,
        actor=actor,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Genie completion job not found")
    return job_status(
        job,
        question=payload.question,
        actor=actor,
        live_campaign_run_marker=live_campaign_run_marker,
        typical_seconds=typical_seconds_for(lakebase, job),
    )


@router.post("/message/cancel", response_model=GenieCancelResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def genie_message_cancel(
    payload: GenieCancelRequest,
    request: Request,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> GenieCancelResponse:
    """The owner's Stop on a job-backed turn (audit genie-03).

    Token-authorized like the other job calls, with the submit's 16-hex
    question label instead of the question. An accepted cancel is audited as
    GENIE_TURN_CANCELLED in the same transaction as its flag
    (``request_cancel``); a repeat, a recorded answer or an ended job is a
    no-op with no audit row. ``cancelled`` means this app will not record
    the answer, never that Genie's own message was cancelled.
    """
    actor = resolve_actor(request)
    claims = verify_genie_progress_token(
        payload.progress_token,
        actor=actor,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
    )
    binding_hash = str(claims.get("question_hash") or "")
    if binding_hash[:16] != payload.question_hash:
        raise HTTPException(status_code=400, detail="question does not match the submitted Genie turn")
    require_completion_jobs(lakebase)  # 404 without the job table, 503 when Lakebase is down
    return request_cancel(lakebase, actor=actor, payload=payload, binding_hash=binding_hash)


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
