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

import json
from datetime import UTC, datetime, timedelta
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
from backend.services.audit_decision_inputs import decision_inputs_from_borrower
from backend.services.audit_store import (
    AuditMetadataViolation,
    AuditPIIError,
    AuditStore,
    get_audit_store,
    resolve_actor,
)
from backend.services.disclosures import disclosure_audit_payload
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.job_trigger import enqueue_lifecycle_trigger
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.lakebase_bootstrap import (
    ensure_approval_followup_columns,
    ensure_approval_idempotency_column,
)
from backend.services.outreach_campaign_gate import (
    _compose_reject_rationale,
    _decision_evidence_ids,
    _enforce_contact_eligibility,
    _marketing_audit_payload,
    _resolve_disclosure_or_http,
    _resolve_governed_campaign_variant,
)
from backend.services.outreach_copy import _safe_offer_code
from backend.services.outreach_decision_commit import (
    _APPROVAL_FINALIZE,
    _APPROVAL_INSERT,
    _commit_outreach_decision_atomic,
    _derive_fallback_request_id,
    _lookup_existing_approval,
    _lookup_persisted_decision_replay,
    _supports_atomic_outreach_write,
)
from backend.services.outreach_decision_intent import (
    _approval_decision_intent,
    _approve_intent_matches_payload,
    _intent_hash,
    _reject_decision_intent,
    _reject_intent_matches_payload,
)
from backend.services.outreach_drafts import (
    _assert_disclosure_backed_draft_body,
    _assert_final_draft_subject,
    _outreach_draft_response_hash,
    _persist_generated_outreach_draft,
    _refresh_timestamp,
    _verified_generated_draft,
)
from backend.services.outreach_intelligence import compose_intelligent_outreach
from backend.services.pii_redaction import scrub_free_text
from backend.services.rbac import require_approver
from backend.services.repositories import (
    LeadRepository,
    OutreachRepository,
    get_lead_repository,
    get_outreach_repository,
)
from backend.services.sales_state import clear_sales_state_cache

router = APIRouter(prefix="/outreach", tags=["outreach"])

RepoDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]
LeadRepoDep = Annotated[LeadRepository, Depends(get_lead_repository)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


@router.post("/draft", response_model=OutreachDraft, responses=JSON_CONTENT_TYPE_RESPONSE)
def draft_outreach(
    payload: OutreachDraftRequest,
    request: Request,
    repo: RepoDep,
    lead_repo: LeadRepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> OutreachDraft:
    actor = resolve_actor(request)
    try:
        campaign_variant = _resolve_governed_campaign_variant(
            lakebase,
            request=request,
            actor=actor,
            campaign_id=payload.campaign_id,
            variant_name=payload.variant_name,
            channel=payload.channel,
            borrower_id=payload.borrower_id,
            lead_repo=lead_repo,
        )
    except HTTPException:
        raise
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    b = repo.find_borrower(payload.borrower_id)
    if b is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    _enforce_contact_eligibility(
        b,
        audit=audit,
        actor=actor,
        surface="outreach_draft",
    )
    disclosure = _resolve_disclosure_or_http(lakebase, borrower=b, channel=payload.channel)
    offer_code = _safe_offer_code(getattr(b, "recommended_offer_code", None))
    source_refreshed_at = str(getattr(b, "source_refreshed_at", "") or "").strip()
    if _refresh_timestamp(source_refreshed_at) is None:
        raise HTTPException(
            status_code=409,
            detail="Borrower source freshness is unavailable; refresh before generating outreach.",
        )
    try:
        draft = compose_intelligent_outreach(
            borrower=b,
            channel=payload.channel,
            disclosure=disclosure,
            campaign_variant=campaign_variant,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = OutreachDraft(
        generation_id=str(uuid4()),
        response_hash="0" * 64,
        source_refreshed_at=source_refreshed_at,
        borrower_id=b.borrower_id,
        campaign_id=payload.campaign_id,
        variant_name=payload.variant_name,
        campaign_treatment_fingerprint=(
            campaign_variant.treatment_fingerprint if campaign_variant is not None else None
        ),
        offer_code=offer_code,
        channel=payload.channel,
        subject=draft.subject if payload.channel in {"email", "direct_mail"} else None,
        body=draft.body,
        disclosure_version=disclosure.disclosure_version,
        disclosure_state=disclosure.state,
        marketing_eligible=True,
        generation_mode=draft.generation_mode,
        generator_label=draft.generator_label,
        strategy_summary=draft.strategy_summary,
        evidence_summary=draft.evidence_summary,
        evidence_assets=draft.evidence_assets,
    )
    response = response.model_copy(
        update={"response_hash": _outreach_draft_response_hash(response)}
    )
    try:
        return _persist_generated_outreach_draft(
            lakebase,
            actor=actor,
            borrower=b,
            payload=payload,
            response=response,
        )
    except (AuditMetadataViolation, AuditPIIError):
        raise
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc


@router.post(
    "/approve", response_model=OutreachApproveResponse, responses=JSON_CONTENT_TYPE_RESPONSE
)
def approve_outreach(
    payload: OutreachApproveRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    lead_repo: LeadRepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> OutreachApproveResponse:
    # R6 actor-spoof fix: attribution is always the edge-authenticated
    # identity from X-Forwarded-Email (via ``resolve_actor``). The
    # ``payload.actor`` body field is retained for backwards compatibility
    # with existing clients but is IGNORED for the audit row — a caller
    # that passes ``actor: "ceo@..."`` cannot masquerade. In local dev /
    # test paths without the header, ``resolve_actor`` returns
    # ``settings.default_actor`` and emits a structured warning so ops
    # sees the fallback in the log trail.
    #
    # 2026-06-11 audit P2-5 / 2026-08-07 platform audit F7: ``require_approver``
    # is FAIL-CLOSED. It admits only the exact identities in
    # MIP_APPROVER_EMAILS / MIP_ADMIN_EMAILS (group matching is a local/test
    # compatibility path only, per ``backend/services/rbac.py``). With the
    # default EMPTY allowlist it admits NOBODY -- every caller, including the
    # workspace owner who deployed the app, gets 403 "forbidden". An earlier
    # version of this comment claimed the opposite, and a deployment shipped
    # with no allowlist on the strength of it: approve/reject were unreachable
    # on the live app, which breaks the contracted approve -> audit demo flow.
    # Configuring the allowlist is a deployment requirement, not an option.
    # The admitted actor is the same edge-resolved identity used for the audit
    # row; ``payload.actor`` is never trusted for attribution.
    actor = require_approver(request)
    safe_rationale = scrub_free_text(payload.rationale) if payload.rationale else None
    safe_bulk_rationale = (
        scrub_free_text(payload.bulk_rationale) if payload.bulk_rationale else None
    )
    if payload.request_id:
        try:
            replay = _lookup_persisted_decision_replay(
                lakebase,
                payload.request_id,
                actor=actor,
                borrower_id=payload.borrower_id,
                action="approve",
                intent_matches_payload=lambda intent: _approve_intent_matches_payload(
                    intent,
                    payload=payload,
                    actor=actor,
                    safe_rationale=safe_rationale,
                    safe_bulk_rationale=safe_bulk_rationale,
                ),
            )
        except LakebaseError as exc:
            raise HTTPException(
                status_code=503,
                detail=safe_dependency_detail("lakebase"),
            ) from exc
        if replay is not None:
            return OutreachApproveResponse.model_validate(replay)

    borrower = repo.find_borrower(payload.borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    offer_code = payload.offer_code or _safe_offer_code(
        getattr(borrower, "recommended_offer_code", None)
    )
    ensure_approval_idempotency_column(lakebase)
    ensure_approval_followup_columns(lakebase)
    audit_evidence_ids = _decision_evidence_ids(
        payload.evidence_ids,
        borrower,
        action_label="Approval",
    )
    assigned_to_email = payload.assigned_to_email
    approval_rationale = (
        safe_rationale
        or safe_bulk_rationale
        or "Approved with governed recommendation and human review."
    )
    try:
        campaign_variant = _resolve_governed_campaign_variant(
            lakebase,
            request=request,
            actor=actor,
            campaign_id=payload.campaign_id,
            variant_name=payload.variant_name,
            channel=payload.channel,
            borrower_id=payload.borrower_id,
            lead_repo=lead_repo,
            require_approved_copy=True,
        )
    except HTTPException:
        raise
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    treatment_fingerprint = (
        campaign_variant.treatment_fingerprint if campaign_variant is not None else None
    )
    decision_intent = _approval_decision_intent(
        payload,
        actor=actor,
        offer_code=offer_code,
        evidence_ids=audit_evidence_ids,
        safe_rationale=safe_rationale,
        safe_bulk_rationale=safe_bulk_rationale,
        campaign_owner_email=(
            campaign_variant.campaign_owner_email if campaign_variant is not None else None
        ),
        campaign_treatment_fingerprint=treatment_fingerprint,
    )
    effective_request_id = payload.request_id or _derive_fallback_request_id(
        actor=actor,
        action="approve",
        decision_intent=decision_intent,
    )
    existing = _lookup_existing_approval(
        lakebase,
        effective_request_id,
        actor=actor,
        borrower_id=payload.borrower_id,
        action="approve",
        expected_intent=decision_intent,
    )
    if existing is not None:
        return OutreachApproveResponse.model_validate(existing)
    _enforce_contact_eligibility(
        borrower,
        audit=audit,
        actor=actor,
        surface="outreach_approve",
        request_id=effective_request_id,
    )
    disclosure = _resolve_disclosure_or_http(lakebase, borrower=borrower, channel=payload.channel)
    try:
        generated_draft, draft_edited = _verified_generated_draft(
            lakebase,
            payload=payload,
            actor=actor,
            borrower=borrower,
            offer_code=offer_code,
            campaign_variant=campaign_variant,
        )
    except LakebaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=safe_dependency_detail("lakebase"),
        ) from exc
    if generated_draft is None and payload.campaign_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Campaign-bound approval requires matching generated draft proof.",
        )
    proof_campaign_id = generated_draft.campaign_id if generated_draft is not None else None
    proof_variant_name = generated_draft.variant_name if generated_draft is not None else None
    approved_draft_body = _assert_disclosure_backed_draft_body(
        draft_body=payload.draft_body,
        disclosure=disclosure,
        channel=payload.channel,
    )
    approved_draft_subject = _assert_final_draft_subject(
        draft_subject=payload.draft_subject,
        channel=payload.channel,
    )
    # lakebase/schema.sql §approvals: approval_id is UUID, not an
    # `apr-<hex12>` synthetic. Passing the raw UUID string satisfies
    # Postgres's UUID cast; truncating it to 12 hex chars produced
    # `invalid input syntax for type uuid: "apr-..."` on INSERT.
    approval_id = str(uuid4())
    # Feature C: compute the follow-up timestamp from the requested window
    # (validated 1..30 by the schema). None when the approver did not ask
    # for a reminder. We only PERSIST this -- no scheduler/notification is
    # wired here.
    follow_up_at = (
        datetime.now(UTC) + timedelta(days=payload.follow_up_in_days)
        if payload.follow_up_in_days is not None
        else None
    )
    audit_payload: dict[str, Any] = {
        "approval_id": approval_id,
        "offer_code": offer_code,
        "borrower_id": payload.borrower_id,
        "channel": payload.channel,
        "campaign_id": proof_campaign_id,
        "variant_name": proof_variant_name,
        "campaign_treatment_fingerprint": treatment_fingerprint,
        "decision_inputs": decision_inputs_from_borrower(borrower),
        **_marketing_audit_payload(borrower),
        **disclosure_audit_payload(disclosure),
    }
    audit_payload["request_id"] = effective_request_id
    audit_payload["draft_body"] = approved_draft_body
    audit_payload["draft_subject"] = approved_draft_subject
    if generated_draft is not None:
        audit_payload["draft_generation_id"] = generated_draft.generation_id
        audit_payload["draft_response_hash"] = generated_draft.response_hash
        audit_payload["draft_source_refreshed_at"] = generated_draft.source_refreshed_at
        audit_payload["draft_edited"] = draft_edited
        audit_payload["draft_attribution"] = (
            f"human_edited_from_{generated_draft.generation_mode}"
            if draft_edited
            else generated_draft.generation_mode
        )
    audit_payload["rationale"] = approval_rationale
    if payload.bulk_id:
        audit_payload["bulk_id"] = payload.bulk_id
    if safe_bulk_rationale:
        audit_payload["bulk_rationale"] = safe_bulk_rationale
    # Feature C: record the assignment + follow-up in the audit metadata so
    # the governance ledger shows who the borrower was routed to and when a
    # follow-up was scheduled. ``assigned_to_email`` is internal-staff-email
    # validated by the audit store; ``follow_up_at`` is an ISO timestamp.
    if assigned_to_email:
        audit_payload["assigned_to_email"] = assigned_to_email
    if follow_up_at:
        audit_payload["follow_up_at"] = follow_up_at.isoformat()
    response_payload = {
        "approved": True,
        "approval_id": approval_id,
        "audit_event_id": "",
        "assigned_to_email": assigned_to_email,
        "follow_up_at": follow_up_at.isoformat() if follow_up_at else None,
        "draft_generation_id": (
            generated_draft.generation_id if generated_draft is not None else None
        ),
        "draft_edited": draft_edited if generated_draft is not None else None,
    }
    try:
        if _supports_atomic_outreach_write(lakebase):
            response_data, created_new = _commit_outreach_decision_atomic(
                lakebase,
                approval_id=approval_id,
                actor=actor,
                action="approve",
                borrower_id=payload.borrower_id,
                campaign_id=proof_campaign_id,
                variant_name=proof_variant_name,
                channel=payload.channel,
                offer_code=offer_code,
                rationale=approval_rationale,
                request_id=effective_request_id,
                audit_payload=audit_payload,
                evidence_ids=audit_evidence_ids,
                event_action="outreach.approve",
                event_type="APPROVE",
                audit_request_id=effective_request_id,
                decision_intent=decision_intent,
                campaign_proof_fingerprint=(
                    campaign_variant.campaign_proof_fingerprint
                    if campaign_variant is not None
                    else None
                ),
                response_payload=response_payload,
                subject_clip=borrower.clip_id,
                assigned_to_email=assigned_to_email,
                follow_up_at=follow_up_at,
            )
        elif proof_campaign_id is not None:
            raise HTTPException(
                status_code=503,
                detail=safe_dependency_detail("lakebase"),
            )
        else:
            lakebase.execute(
                _APPROVAL_INSERT,
                {
                    "approval_id": approval_id,
                    "campaign_id": proof_campaign_id,
                    "variant_name": proof_variant_name,
                    "channel": payload.channel,
                    "borrower_id": payload.borrower_id,
                    "offer_code": offer_code,
                    "action": "approve",
                    "actor_email": actor,
                    "rationale": approval_rationale,
                    "request_id": effective_request_id,
                    "assigned_to_email": assigned_to_email,
                    "follow_up_at": follow_up_at,
                    "decision_intent": decision_intent,
                    "decision_payload_hash": _intent_hash(decision_intent),
                },
            )
            event = audit.write(
                actor=actor,
                action="outreach.approve",
                entity_type="approval",
                entity_id=approval_id,
                payload_json=audit_payload,
                evidence_ids=audit_evidence_ids,
                event_type="APPROVE",
                subject_clip=borrower.clip_id,
                request_id=effective_request_id,
            )
            response_data = {
                **response_payload,
                "audit_event_id": event.event_id,
            }
            lakebase.execute(
                _APPROVAL_FINALIZE,
                {
                    "approval_id": approval_id,
                    "audit_event_id": event.event_id,
                    "decision_response": json.dumps(
                        response_data,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                },
            )
            created_new = True
    except LakebaseError as exc:
        # No silent fallback. The UI surfaces 503 as a retry banner;
        # the operator's next move is to check Lakebase status.
        # R5-03: constant string; structured log keeps the full ``str(exc)``
        # via ``from exc`` + the underlying LakebaseError WARNING.
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    response = OutreachApproveResponse.model_validate(response_data)
    if not created_new:
        return response
    clear_sales_state_cache()
    # The approval row is now committed in Lakebase. Kick the
    # ``mip_sync_lifecycle_state`` job to mirror it into
    # ``mip.gold.borrower_lifecycle_state`` so metric views + Genie
    # see the new state within minutes. ``enqueue_lifecycle_trigger`` logs
    # ``event=lifecycle_trigger_enqueued`` then schedules the trigger
    # on BackgroundTasks so the HTTP response ships first; a SIGTERM
    # between response commit and task execution drops the call
    # silently (BackgroundTasks has no drain). The enqueue log is the
    # breadcrumb; Admin Data operations is the operator repair path.
    if response.audit_event_id:
        enqueue_lifecycle_trigger(background, reason="approval")
    return response


@router.post("/reject", response_model=OutreachRejectResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def reject_outreach(
    payload: OutreachRejectRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
    lead_repo: LeadRepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
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
    # 2026-06-11 audit P2-5: optional approver allowlist, same as /approve.
    actor = require_approver(request)
    safe_rationale = _compose_reject_rationale(payload.rationale_code, payload.rationale)
    if payload.request_id:
        try:
            replay = _lookup_persisted_decision_replay(
                lakebase,
                payload.request_id,
                actor=actor,
                borrower_id=payload.borrower_id,
                action="reject",
                intent_matches_payload=lambda intent: _reject_intent_matches_payload(
                    intent,
                    payload=payload,
                    actor=actor,
                    safe_rationale=safe_rationale,
                ),
            )
        except LakebaseError as exc:
            raise HTTPException(
                status_code=503,
                detail=safe_dependency_detail("lakebase"),
            ) from exc
        if replay is not None:
            return OutreachRejectResponse.model_validate(replay)
    try:
        campaign_variant = _resolve_governed_campaign_variant(
            lakebase,
            request=request,
            actor=actor,
            campaign_id=payload.campaign_id,
            variant_name=payload.variant_name,
            channel=payload.channel,
            borrower_id=payload.borrower_id,
            lead_repo=lead_repo,
        )
    except HTTPException:
        raise
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    verified_campaign_id = campaign_variant.campaign_id if campaign_variant else None
    verified_variant_name = campaign_variant.variant_name if campaign_variant else None
    treatment_fingerprint = (
        campaign_variant.treatment_fingerprint if campaign_variant is not None else None
    )
    borrower = repo.find_borrower(payload.borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    offer_code = payload.offer_code or _safe_offer_code(
        getattr(borrower, "recommended_offer_code", None)
    )
    ensure_approval_idempotency_column(lakebase)
    audit_evidence_ids = _decision_evidence_ids(
        payload.evidence_ids,
        borrower,
        action_label="Rejection",
    )
    decision_intent = _reject_decision_intent(
        payload,
        actor=actor,
        offer_code=offer_code,
        evidence_ids=audit_evidence_ids,
        safe_rationale=safe_rationale,
        campaign_id=verified_campaign_id,
        variant_name=verified_variant_name,
        campaign_owner_email=(
            campaign_variant.campaign_owner_email if campaign_variant is not None else None
        ),
        campaign_treatment_fingerprint=treatment_fingerprint,
    )
    effective_request_id = payload.request_id or _derive_fallback_request_id(
        actor=actor,
        action="reject",
        decision_intent=decision_intent,
    )
    existing = _lookup_existing_approval(
        lakebase,
        effective_request_id,
        actor=actor,
        borrower_id=payload.borrower_id,
        action="reject",
        expected_intent=decision_intent,
    )
    if existing is not None:
        return OutreachRejectResponse.model_validate(existing)
    approval_id = str(uuid4())
    audit_payload: dict[str, Any] = {
        "approval_id": approval_id,
        "offer_code": offer_code,
        "borrower_id": payload.borrower_id,
        "channel": payload.channel,
        "campaign_id": verified_campaign_id,
        "variant_name": verified_variant_name,
        "campaign_treatment_fingerprint": treatment_fingerprint,
        "rationale_code": payload.rationale_code,
        **_marketing_audit_payload(borrower),
    }
    audit_payload["request_id"] = effective_request_id
    if safe_rationale:
        audit_payload["rationale"] = safe_rationale
    response_payload = {
        "rejected": True,
        "approval_id": approval_id,
        "audit_event_id": "",
    }
    try:
        if _supports_atomic_outreach_write(lakebase):
            response_data, created_new = _commit_outreach_decision_atomic(
                lakebase,
                approval_id=approval_id,
                actor=actor,
                action="reject",
                borrower_id=payload.borrower_id,
                campaign_id=verified_campaign_id,
                variant_name=verified_variant_name,
                channel=payload.channel,
                offer_code=offer_code,
                rationale=safe_rationale,
                request_id=effective_request_id,
                audit_payload=audit_payload,
                evidence_ids=audit_evidence_ids,
                event_action="outreach.reject",
                event_type="OUTREACH_REJECT",
                audit_request_id=effective_request_id,
                decision_intent=decision_intent,
                campaign_proof_fingerprint=(
                    campaign_variant.campaign_proof_fingerprint
                    if campaign_variant is not None
                    else None
                ),
                response_payload=response_payload,
                subject_clip=borrower.clip_id,
            )
        elif verified_campaign_id is not None:
            raise HTTPException(
                status_code=503,
                detail=safe_dependency_detail("lakebase"),
            )
        else:
            lakebase.execute(
                _APPROVAL_INSERT,
                {
                    "approval_id": approval_id,
                    "campaign_id": verified_campaign_id,
                    "variant_name": verified_variant_name,
                    "channel": payload.channel,
                    "borrower_id": payload.borrower_id,
                    "offer_code": offer_code,
                    "action": "reject",
                    "actor_email": actor,
                    "rationale": safe_rationale,
                    "request_id": effective_request_id,
                    # Feature C columns are approval-only; reject never
                    # assigns an LO or schedules a follow-up.
                    "assigned_to_email": None,
                    "follow_up_at": None,
                    "decision_intent": decision_intent,
                    "decision_payload_hash": _intent_hash(decision_intent),
                },
            )
            event = audit.write(
                actor=actor,
                action="outreach.reject",
                entity_type="approval",
                entity_id=approval_id,
                payload_json=audit_payload,
                evidence_ids=audit_evidence_ids,
                event_type="OUTREACH_REJECT",
                subject_clip=borrower.clip_id,
                request_id=effective_request_id,
            )
            response_data = {
                **response_payload,
                "audit_event_id": event.event_id,
            }
            lakebase.execute(
                _APPROVAL_FINALIZE,
                {
                    "approval_id": approval_id,
                    "audit_event_id": event.event_id,
                    "decision_response": json.dumps(
                        response_data,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            )
            created_new = True
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    response = OutreachRejectResponse.model_validate(response_data)
    if not created_new:
        return response
    clear_sales_state_cache()
    # Same debounced fire-and-forget sync the approve path uses so the
    # funnel / lifecycle views reflect rejected-borrower counts promptly.
    if response.audit_event_id:
        enqueue_lifecycle_trigger(background, reason="rejection")
    return response
