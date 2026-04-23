"""Outreach API -- draft + approve.

Slice 5 landmarks:
* ``/draft`` emits a ``DRAFT_OUTREACH`` audit row so we can
  reconstruct which drafts were shown to the approver.
* ``/approve`` emits an ``APPROVE`` audit row AND inserts a row into
  ``mip_app.approvals`` so the governance ledger has both the
  point-in-time verb and the decision record.
* Approval is a **synchronous** Lakebase write (no background task):
  the caller needs the approval_id returned synchronously, and a
  failed approval must surface as 503 rather than silently drop.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachApproveResponse,
    OutreachDraft,
    OutreachDraftRequest,
    OutreachRejectRequest,
    OutreachRejectResponse,
)
from backend.services.audit_store import AuditStore, get_audit_store, resolve_actor
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.job_trigger import enqueue_lifecycle_trigger
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.lakebase_bootstrap import ensure_approval_idempotency_column
from backend.services.observability import emit
from backend.services.pii_redaction import scrub_free_text
from backend.services.repositories import OutreachRepository, get_outreach_repository

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/outreach", tags=["outreach"])

RepoDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def _safe_audit_write(store: AuditStore, **kwargs: Any) -> None:
    """Background-task audit writer -- never let an audit failure bubble.

    R5-18 widened the exception net from ``LakebaseError`` to ``Exception``
    because the background-task context has no caller to surface an error
    to; an unhandled exception here orphans the BackgroundTasks runner
    and FastAPI silently drops it. We emit a structured
    ``event=audit.dropped`` carrying only the exception CLASS NAME (never
    ``str(exc)``, which could echo payload data back into logs) so
    operators can spot the pattern without us widening the log-based PII
    surface.
    """
    try:
        store.write(**kwargs)
    except Exception as exc:  # noqa: BLE001 -- background path must not raise
        emit(
            log,
            "audit.dropped",
            dependency="lakebase",
            exc_type=type(exc).__name__,
            outcome="error",
        )


_APPROVAL_INSERT = """
INSERT INTO mip_app.approvals (
    approval_id, borrower_id, offer_code, action,
    actor_email, rationale, request_id
) VALUES (
    %(approval_id)s, %(borrower_id)s, %(offer_code)s, %(action)s,
    %(actor_email)s, %(rationale)s, %(request_id)s
)
ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
"""


_APPROVAL_LOOKUP_BY_REQUEST_ID = """
SELECT approval_id
FROM mip_app.approvals
WHERE request_id = %(request_id)s
LIMIT 1
"""


def _derive_fallback_request_id(
    *,
    actor: str,
    borrower_id: str,
    action: str,
    now_s: float | None = None,
) -> str:
    """Generate a deterministic fallback ``request_id`` for legacy clients.

    R6-19: the partial unique index on ``mip_app.approvals.request_id``
    only covers rows WHERE ``request_id IS NOT NULL``. A legacy caller
    that POSTs ``/approve`` or ``/reject`` without a ``request_id`` (no
    Idempotency-Key header plumbing, older mobile build, retry storm
    from a watchdog) therefore bypasses the idempotency contract
    completely: a double-submit writes two approvals.

    We close the loop by deriving a deterministic key from
    ``(actor, borrower_id, action, minute-bucket)`` and hashing to a
    stable 32-hex-char digest. Two requests from the same actor for the
    same borrower + action within the SAME minute collapse to one row
    (matches operator intent: a user who double-clicks Approve wants
    one approval). Two requests 61 seconds apart are treated as
    distinct (matches operator intent: a user who explicitly re-submits
    after waiting wants a new decision).

    ``now_s`` is injectable so tests can pin the minute bucket; in
    production it defaults to ``time.time()``.
    """
    t = now_s if now_s is not None else time.time()
    minute_bucket = int(t // 60)
    material = f"{actor}|{borrower_id}|{action}|{minute_bucket}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]  # noqa: S324 -- not a secret
    # Prefix so audit review can tell server-derived keys apart from
    # client-sent ones (which are typically UUIDs / opaque tokens).
    return f"auto-{digest}"


def _lookup_existing_approval(
    lakebase: LakebaseClient, request_id: str | None
) -> str | None:
    """Return an existing approval_id for this request_id, or None.

    R5-01: the idempotency contract is "same request_id => same
    approval_id, no duplicate write". Callers that want the retry-safe
    guarantee pass a ``request_id``; when they do and we already have a
    row in ``mip_app.approvals``, we return its approval_id without
    issuing a second INSERT or a second audit write.

    Safe to short-circuit with None when ``request_id`` is falsy --
    legacy callers keep their pre-R5-01 behaviour.
    """
    if not request_id:
        return None
    try:
        row = lakebase.fetchone(
            _APPROVAL_LOOKUP_BY_REQUEST_ID, {"request_id": request_id}
        )
    except LakebaseError:
        # Don't paper over the outage -- let the subsequent INSERT raise
        # and surface the real error as 503. Returning None here means
        # "we don't know if there's a duplicate"; the INSERT's ON
        # CONFLICT clause is the second line of defence.
        return None
    if row is None:
        return None
    approval_id = row.get("approval_id")
    return str(approval_id) if approval_id else None


@router.post("/draft", response_model=OutreachDraft)
def draft_outreach(
    payload: OutreachDraftRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    audit: AuditDep,
) -> OutreachDraft:
    b = repo.find_borrower(payload.borrower_id)
    if b is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    subject = f"{b.recommended_offer} opportunity for {b.display_name}"
    body = (
        f"Hi {b.display_name.split(' & ')[0]},\n\n"
        f"Based on recent public-record signals in {b.city}, {b.state}, you may qualify for "
        f"{b.recommended_offer}. {b.why_now}\n\n"
        "Reply to this note and a licensed officer will follow up. "
        "This draft is for human review only; no outreach has been sent."
    )
    background.add_task(
        _safe_audit_write,
        audit,
        actor=resolve_actor(request),
        action="draft_outreach",
        entity_type="outreach_draft",
        entity_id=b.borrower_id,
        payload_json={
            "channel": payload.channel,
            "offer_code": b.recommended_offer,
        },
        event_type="DRAFT_OUTREACH",
        subject_clip=b.clip_id,
    )
    return OutreachDraft(
        borrower_id=b.borrower_id,
        offer_code=f"OFFER-{b.borrower_id}",
        channel=payload.channel,
        subject=subject if payload.channel == "email" else None,
        body=body,
    )


@router.post("/approve", response_model=OutreachApproveResponse)
def approve_outreach(
    payload: OutreachApproveRequest,
    request: Request,
    background: BackgroundTasks,
    audit: AuditDep,
    lakebase: LakebaseDep,
) -> OutreachApproveResponse:
    # R6 actor-spoof fix: attribution is always the edge-authenticated
    # identity from X-Forwarded-Email (via ``resolve_actor``). The
    # ``payload.actor`` body field is retained for backwards compatibility
    # with existing clients but is IGNORED for the audit row — a caller
    # that passes ``actor: "ceo@..."`` cannot masquerade. In local dev /
    # test paths without the header, ``resolve_actor`` returns
    # ``settings.default_actor`` and emits a structured warning so ops
    # sees the fallback in the log trail.
    actor = resolve_actor(request)
    # R5-01 idempotency pre-check: if the caller sent a request_id and
    # we already wrote a row for it (the previous attempt succeeded
    # server-side but its 200 response was lost in flight), return the
    # existing approval_id and skip both the INSERT and the audit write.
    # The partial unique index on ``mip_app.approvals.request_id`` is
    # the backstop; this SELECT is the fast path that avoids emitting
    # a duplicate audit event for a retry.
    ensure_approval_idempotency_column(lakebase)
    # R6-19: legacy callers that omit ``request_id`` used to bypass the
    # idempotency index entirely, so a retry storm from a watchdog that
    # never learned Idempotency-Key would double-book an approval. We
    # derive a deterministic fallback from (actor, borrower_id, action,
    # minute-bucket) so same-minute duplicates collapse; cross-minute
    # retries are treated as distinct (see helper docstring).
    effective_request_id = payload.request_id or _derive_fallback_request_id(
        actor=actor, borrower_id=payload.borrower_id, action="approve",
    )
    existing = _lookup_existing_approval(lakebase, effective_request_id)
    if existing is not None:
        return OutreachApproveResponse(
            approved=True,
            approval_id=existing,
            audit_event_id="",
        )
    # lakebase/schema.sql §approvals: approval_id is UUID, not an
    # `apr-<hex12>` synthetic. Passing the raw UUID string satisfies
    # Postgres's UUID cast; truncating it to 12 hex chars produced
    # `invalid input syntax for type uuid: "apr-..."` on INSERT.
    approval_id = str(uuid4())
    # Governance §4: approvals live in both the ``approvals`` table
    # (durable decision record, queryable by campaign) AND the
    # ``action_audit`` table (append-only ledger). We write approvals
    # first so the audit row's ``entity_id`` (the approval_id) is a
    # valid FK-equivalent pointer.
    try:
        lakebase.execute(
            _APPROVAL_INSERT,
            {
                "approval_id": approval_id,
                "borrower_id": payload.borrower_id,
                "offer_code": payload.offer_code,
                "action": "approve",
                "actor_email": actor,
                "rationale": None,
                "request_id": effective_request_id,
            },
        )
        # Governance §4: the audit metadata mirrors what the approver
        # saw + what they committed to. ``draft_body`` (when supplied)
        # lets compliance reconstruct the exact outreach copy that was
        # released; we keep it out of the ``approvals`` table to avoid
        # bloating the decision ledger and because action_audit is the
        # append-only surface governance queries against.
        audit_payload: dict[str, Any] = {
            "approval_id": approval_id,
            "offer_code": payload.offer_code,
            "borrower_id": payload.borrower_id,
        }
        if payload.request_id:
            # Persist the idempotency key in the audit metadata too --
            # retrospective auditors can then correlate "same client
            # request, same approval_id" across the decision ledger and
            # the append-only audit log. Note: when ``payload.request_id``
            # is None we still wrote the server-derived fallback to the
            # approvals row (R6-19), but we don't expose that derived key
            # in the audit metadata -- it's an implementation detail, and
            # the audit row already has the approval_id pointer.
            audit_payload["request_id"] = payload.request_id
        if payload.draft_body:
            # Defence-in-depth: scrub obvious PII markers (SSN / phone / email /
            # street address) before the free-text body lands in the append-only
            # audit ledger. Governance posture says approvers shouldn't paste PII
            # into the draft in the first place, but an accidental paste
            # shouldn't become a durable PII leak.
            audit_payload["draft_body"] = scrub_free_text(payload.draft_body)
        event = audit.write(
            actor=actor,
            action="outreach.approve",
            entity_type="approval",
            entity_id=approval_id,
            payload_json=audit_payload,
            evidence_ids=payload.evidence_ids,
            event_type="APPROVE",
            request_id=payload.request_id,
        )
    except LakebaseError as exc:
        # No silent fallback. The UI surfaces 503 as a retry banner;
        # the operator's next move is to check Lakebase status.
        # R5-03: constant string; structured log keeps the full ``str(exc)``
        # via ``from exc`` + the underlying LakebaseError WARNING.
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
    # The approval row is now committed in Lakebase. Kick the
    # ``mip_sync_lifecycle_state`` job to mirror it into
    # ``mip.gold.borrower_lifecycle_state`` so metric views + Genie
    # see the new state within minutes instead of waiting for the
    # 04:00 daily fallback cron. ``enqueue_lifecycle_trigger`` logs
    # ``event=lifecycle_trigger_enqueued`` then schedules the trigger
    # on BackgroundTasks so the HTTP response ships first; a SIGTERM
    # between response commit and task execution drops the call
    # silently (BackgroundTasks has no drain) -- the enqueue log is
    # the breadcrumb and the daily 04:00 cron is the safety net.
    enqueue_lifecycle_trigger(background, reason="approval")
    return OutreachApproveResponse(
        approved=True,
        approval_id=approval_id,
        audit_event_id=event.event_id,
    )


@router.post("/reject", response_model=OutreachRejectResponse)
def reject_outreach(
    payload: OutreachRejectRequest,
    request: Request,
    background: BackgroundTasks,
    audit: AuditDep,
    lakebase: LakebaseDep,
) -> OutreachRejectResponse:
    """Governed borrower rejection — audit twin of ``/approve``.

    Audit finding 2026-04-22: the UI's "Reject" controls (Offer
    Orchestrator banner + LeadTable inline button) only mutated
    AppContext, so dropped borrowers left no durable trace. Compliance
    reviewers asking "who rejected this borrower and when" got silence.

    This endpoint closes that gap with the same two-write pattern the
    approve path uses:

    1. ``mip_app.approvals`` (action='reject') -- the decision record,
       queryable by campaign / borrower.
    2. ``mip_app.action_audit`` (event_type='OUTREACH_REJECT') -- the
       append-only ledger governance §4 queries against.

    The lifecycle-sync trigger fires on reject too so the funnel /
    lifecycle metric views reflect rejected-borrower counts without
    waiting for the 04:00 cron. The same debounce applies: clustered
    rejects coalesce into a single run_now call.

    Failures raise 503 (same contract as approve) so the UI's retry
    banner + resilience layer get to act; no silent fallback.
    """
    # R6 actor-spoof fix: same as /approve — attribution is always
    # ``resolve_actor(request)`` from the edge-authenticated identity.
    # Body ``payload.actor`` is retained for backcompat but ignored.
    actor = resolve_actor(request)
    # R5-01 idempotency: same contract as /approve. A re-POSTed reject
    # with the same ``request_id`` returns the existing approval_id
    # instead of writing a second decision row + duplicate audit event.
    ensure_approval_idempotency_column(lakebase)
    # R6-19: server-derived fallback for legacy clients that omit
    # ``request_id`` (see /approve for the full rationale).
    effective_request_id = payload.request_id or _derive_fallback_request_id(
        actor=actor, borrower_id=payload.borrower_id, action="reject",
    )
    existing = _lookup_existing_approval(lakebase, effective_request_id)
    if existing is not None:
        return OutreachRejectResponse(
            rejected=True,
            approval_id=existing,
            audit_event_id="",
        )
    approval_id = str(uuid4())
    try:
        lakebase.execute(
            _APPROVAL_INSERT,
            {
                "approval_id": approval_id,
                "borrower_id": payload.borrower_id,
                "offer_code": payload.offer_code,
                "action": "reject",
                "actor_email": actor,
                "rationale": payload.rationale,
                "request_id": effective_request_id,
            },
        )
        audit_payload: dict[str, Any] = {
            "approval_id": approval_id,
            "offer_code": payload.offer_code,
            "borrower_id": payload.borrower_id,
        }
        if payload.request_id:
            audit_payload["request_id"] = payload.request_id
        if payload.rationale:
            audit_payload["rationale"] = payload.rationale
        event = audit.write(
            actor=actor,
            action="outreach.reject",
            entity_type="approval",
            entity_id=approval_id,
            payload_json=audit_payload,
            evidence_ids=payload.evidence_ids,
            event_type="OUTREACH_REJECT",
            request_id=payload.request_id,
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
    # Same debounced fire-and-forget sync the approve path uses -- the
    # funnel / lifecycle views need to reflect rejected-borrower counts
    # without waiting on the daily cron.
    enqueue_lifecycle_trigger(background, reason="rejection")
    return OutreachRejectResponse(
        rejected=True,
        approval_id=approval_id,
        audit_event_id=event.event_id,
    )
