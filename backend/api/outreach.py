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
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.config.settings import settings
from backend.schemas._validators import assert_no_protected_class_marketing_text
from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachApproveResponse,
    OutreachDraft,
    OutreachDraftRequest,
    OutreachRejectRequest,
    OutreachRejectResponse,
)
from backend.schemas.portfolio_campaign import (
    assert_borrower_campaign_copy,
    assert_public_campaign_text,
)
from backend.services.audit_decision_inputs import decision_inputs_from_borrower
from backend.services.audit_lakebase_store import (
    build_audit_insert_params,
    write_audit_event_in_transaction,
)
from backend.services.audit_store import (
    AuditMetadataViolation,
    AuditPIIError,
    AuditStore,
    get_audit_store,
    resolve_actor,
)
from backend.services.disclosures import (
    MissingTenantDisclosureError,
    disclosure_audit_payload,
    resolve_tenant_disclosure,
)
from backend.services.eligibility import (
    DEFAULT_ELIGIBILITY_SOURCE,
    REASON_CONSENT_NOT_OPT_IN,
    REASON_FREQUENCY_CAP,
    EligibilityDecision,
    get_eligibility_service,
    safe_write_suppression_audit,
)
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.http_content import JSON_CONTENT_TYPE_RESPONSE, require_json_content_type
from backend.services.job_trigger import enqueue_lifecycle_trigger
from backend.services.lakebase import LakebaseClient, LakebaseError, get_lakebase_client
from backend.services.lakebase_bootstrap import (
    ensure_approval_followup_columns,
    ensure_approval_idempotency_column,
)
from backend.services.outreach_copy import _safe_offer_code
from backend.services.outreach_intelligence import compose_intelligent_outreach
from backend.services.pii_redaction import scrub_free_text
from backend.services.rbac import require_approver
from backend.services.repositories import OutreachRepository, get_outreach_repository
from backend.services.sales_state import clear_sales_state_cache

router = APIRouter(prefix="/outreach", tags=["outreach"])

RepoDep = Annotated[OutreachRepository, Depends(get_outreach_repository)]
AuditDep = Annotated[AuditStore, Depends(get_audit_store)]
LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


_APPROVAL_INSERT = """
INSERT INTO mip_app.approvals (
    approval_id, campaign_id, borrower_id, offer_code, action,
    actor_email, rationale, request_id, assigned_to_email, follow_up_at,
    decision_intent, decision_payload_hash
) VALUES (
    %(approval_id)s, %(campaign_id)s, %(borrower_id)s, %(offer_code)s, %(action)s,
    %(actor_email)s, %(rationale)s, %(request_id)s, %(assigned_to_email)s, %(follow_up_at)s,
    %(decision_intent)s, %(decision_payload_hash)s
)
ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
"""


_APPROVAL_INSERT_RETURNING = """
INSERT INTO mip_app.approvals (
    approval_id, campaign_id, borrower_id, offer_code, action,
    actor_email, rationale, request_id, assigned_to_email, follow_up_at,
    decision_intent, decision_payload_hash
) VALUES (
    %(approval_id)s, %(campaign_id)s, %(borrower_id)s, %(offer_code)s, %(action)s,
    %(actor_email)s, %(rationale)s, %(request_id)s, %(assigned_to_email)s, %(follow_up_at)s,
    %(decision_intent)s, %(decision_payload_hash)s
)
ON CONFLICT (request_id) WHERE request_id IS NOT NULL DO NOTHING
RETURNING approval_id
"""


_APPROVAL_LOOKUP_BY_REQUEST_ID = """
SELECT approval_id, borrower_id, action, actor_email, decision_intent,
       decision_payload_hash, decision_response, audit_event_id
FROM mip_app.approvals
WHERE request_id = %(request_id)s
LIMIT 1
"""


_APPROVAL_FINALIZE = """
UPDATE mip_app.approvals
SET decision_response = %(decision_response)s::jsonb,
    audit_event_id = %(audit_event_id)s
WHERE approval_id = %(approval_id)s
  AND decision_response IS NULL
  AND audit_event_id IS NULL
RETURNING approval_id, decision_response, audit_event_id
"""


_GENERATED_OUTREACH_DRAFT_INSERT = """
WITH audit AS (
  INSERT INTO mip_app.action_audit (
    audit_id, event_type, actor_email, entity_type, entity_id,
    subject_clip, subject_segment, request_id, correlation_id, evidence_ids, metadata
  ) VALUES (
    %(audit_id)s, %(event_type)s, %(actor_email)s, %(entity_type)s, %(entity_id)s,
    %(subject_clip)s, %(subject_segment)s, %(request_id)s, %(correlation_id)s,
    %(evidence_ids)s, %(metadata)s::jsonb
  )
  RETURNING audit_id
),
persisted AS (
  INSERT INTO mip_app.generated_outreach_drafts (
    generation_id, audit_event_id, actor_email, borrower_id, campaign_id,
    variant_name, channel, offer_code, generation_mode, response_hash, response_json
  )
  SELECT
    %(generation_id)s, audit.audit_id, %(actor_email)s, %(borrower_id)s, %(campaign_id)s,
    %(variant_name)s, %(channel)s, %(offer_code)s, %(generation_mode)s,
    %(response_hash)s, %(response_json)s::jsonb
  FROM audit
  RETURNING generation_id, audit_event_id, response_hash, response_json
)
SELECT generation_id, audit_event_id, response_hash, response_json
FROM persisted
"""


def _canonical_intent(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _intent_hash(intent: str) -> str:
    return hashlib.sha256(intent.encode("utf-8")).hexdigest()


def _derive_fallback_request_id(
    *,
    actor: str,
    action: str,
    decision_intent: str,
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
    ``(actor, action, full normalized decision intent, minute-bucket)`` and hashing to a
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
    material = f"{actor}|{action}|{decision_intent}|{minute_bucket}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]  # noqa: S324 -- not a secret
    # Prefix so audit review can tell server-derived keys apart from
    # client-sent ones (which are typically UUIDs / opaque tokens).
    return f"auto-{digest}"


def _lookup_existing_approval(
    lakebase: LakebaseClient,
    request_id: str | None,
    *,
    actor: str,
    borrower_id: str,
    action: str,
    expected_intent: str,
) -> dict[str, Any] | None:
    """Return the complete response for an exact replay, or None.

    R5-01: the idempotency contract is "same request_id => same
    actor + borrower + action => same approval_id, no duplicate write".
    Reusing the same request_id for a different decision is rejected
    instead of silently returning another user's or borrower's approval.

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
    return _existing_approval_response_or_conflict(
        row,
        actor=actor,
        borrower_id=borrower_id,
        action=action,
        expected_intent=expected_intent,
    )


def _existing_approval_response_or_conflict(
    row: dict[str, Any] | None,
    *,
    actor: str,
    borrower_id: str,
    action: str,
    expected_intent: str,
) -> dict[str, Any] | None:
    if row is None:
        return None
    same_actor = str(row.get("actor_email") or "") == actor
    same_borrower = str(row.get("borrower_id") or "") == borrower_id
    same_action = str(row.get("action") or "") == action
    stored_intent = str(row.get("decision_intent") or "")
    stored_hash = str(row.get("decision_payload_hash") or "")
    same_intent = stored_intent == expected_intent and stored_hash == _intent_hash(expected_intent)
    if not (same_actor and same_borrower and same_action and same_intent):
        raise HTTPException(
            status_code=409,
            detail="request_id already belongs to a different outreach decision",
        )
    response_value = row.get("decision_response")
    if isinstance(response_value, str):
        try:
            response_value = json.loads(response_value)
        except json.JSONDecodeError:
            response_value = None
    if not isinstance(response_value, dict):
        raise HTTPException(
            status_code=409,
            detail="prior outreach decision cannot be reconstructed safely",
        )
    approval_id = str(row.get("approval_id") or "")
    audit_event_id = str(row.get("audit_event_id") or "")
    if (
        not approval_id
        or not audit_event_id
        or str(response_value.get("approval_id") or "") != approval_id
        or str(response_value.get("audit_event_id") or "") != audit_event_id
    ):
        raise HTTPException(
            status_code=409,
            detail="prior outreach decision cannot be reconstructed safely",
        )
    return response_value


def _supports_atomic_outreach_write(lakebase: LakebaseClient) -> bool:
    return getattr(lakebase, "_supports_atomic_transactions", False) is True and callable(
        getattr(lakebase, "transaction", None)
    )


def _commit_outreach_decision_atomic(
    lakebase: LakebaseClient,
    *,
    approval_id: str,
    actor: str,
    action: str,
    borrower_id: str,
    campaign_id: str | None,
    offer_code: str | None,
    rationale: str | None,
    request_id: str,
    audit_payload: dict[str, Any],
    evidence_ids: list[str],
    event_action: str,
    event_type: str,
    audit_request_id: str | None,
    decision_intent: str,
    response_payload: dict[str, Any],
    subject_clip: str | None = None,
    assigned_to_email: str | None = None,
    follow_up_at: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    """Write approval + audit in one Lakebase transaction."""
    try:
        with lakebase.transaction() as conn:
            row = conn.execute(
                _APPROVAL_INSERT_RETURNING,
                {
                    "approval_id": approval_id,
                    "campaign_id": campaign_id,
                    "borrower_id": borrower_id,
                    "offer_code": offer_code,
                    "action": action,
                    "actor_email": actor,
                    "rationale": rationale,
                    "request_id": request_id,
                    "assigned_to_email": assigned_to_email,
                    "follow_up_at": follow_up_at,
                    "decision_intent": decision_intent,
                    "decision_payload_hash": _intent_hash(decision_intent),
                },
            ).fetchone()
            if row is None:
                existing = conn.execute(
                    _APPROVAL_LOOKUP_BY_REQUEST_ID, {"request_id": request_id}
                ).fetchone()
                existing_response = _existing_approval_response_or_conflict(
                    existing,
                    actor=actor,
                    borrower_id=borrower_id,
                    action=action,
                    expected_intent=decision_intent,
                )
                if existing_response is not None:
                    return existing_response, False
                raise LakebaseError("Lakebase approval insert returned no row")

            row_approval_id = str(row.get("approval_id") or approval_id)
            payload_for_audit = {**audit_payload, "approval_id": row_approval_id}
            event = write_audit_event_in_transaction(
                conn,
                actor=actor,
                action=event_action,
                entity_type="approval",
                entity_id=row_approval_id,
                payload_json=payload_for_audit,
                evidence_ids=evidence_ids,
                event_type=event_type,
                subject_clip=subject_clip,
                request_id=audit_request_id,
            )
            final_response = {
                **response_payload,
                "approval_id": row_approval_id,
                "audit_event_id": event.event_id,
            }
            finalized = conn.execute(
                _APPROVAL_FINALIZE,
                {
                    "approval_id": row_approval_id,
                    "audit_event_id": event.event_id,
                    "decision_response": json.dumps(
                        final_response,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                },
            ).fetchone()
            if finalized is None:
                raise LakebaseError("Lakebase approval response could not be finalized")
            return final_response, True
    except (AuditMetadataViolation, AuditPIIError):
        raise
    except HTTPException:
        raise
    except LakebaseError:
        raise
    except Exception as exc:  # noqa: BLE001 -- normalize raw psycopg/fake-client errors
        raise LakebaseError("Lakebase atomic outreach decision failed") from exc


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _enforce_contact_eligibility(
    borrower: Any,
    *,
    audit: AuditStore,
    actor: str,
    surface: str,
    request_id: str | None = None,
) -> EligibilityDecision:
    """S1.4 single-interface eligibility gate for outreach paths.

    All contactability facts come from ``EligibilityService.evaluate``;
    this function branches only on the decision (never raw borrower
    fields), audits every suppression, and preserves the pinned HTTP
    contract: 422 for consent/suppression, 409 for frequency caps.

    The suppression audit write is synchronous (safe, non-raising):
    FastAPI drops BackgroundTasks when the handler raises HTTPException,
    so a background write would silently lose the suppression row.
    """
    decision = get_eligibility_service().evaluate(borrower)
    if decision.eligible:
        return decision
    safe_write_suppression_audit(
        audit,
        actor=actor,
        borrower_id=str(getattr(borrower, "borrower_id", "") or ""),
        decision=decision,
        surface=surface,
        request_id=request_id,
    )
    if decision.reason_code == REASON_CONSENT_NOT_OPT_IN:
        raise HTTPException(
            status_code=422,
            detail=f"borrower is not marketing-eligible: consent_status={decision.consent_status}",
        )
    if decision.reason_code == REASON_FREQUENCY_CAP and decision.earliest_recontact_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "borrower hit frequency cap; earliest re-contact "
                f"{decision.earliest_recontact_at.date().isoformat()}"
            ),
        )
    reason = decision.suppression_reason or "eligibility_not_proven"
    raise HTTPException(
        status_code=422,
        detail=f"borrower is not marketing-eligible: {reason}",
    )


def _borrower_evidence_ids(borrower: Any) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in (getattr(borrower, "evidence_ids", None) or [])
            if str(value).strip()
        )
    )


def _decision_evidence_ids(
    payload_ids: list[str],
    borrower: Any,
    *,
    action_label: str,
) -> list[str]:
    """Resolve borrower-owned evidence IDs for an outreach decision.

    Clients may pass the evidence refs the approver saw, but the API must not
    let the client invent audit provenance. If the payload supplies IDs, every
    one must belong to the canonical borrower evidence list. Legacy clients can
    omit the list and the endpoint will audit all borrower evidence instead.
    """
    borrower_ids = _borrower_evidence_ids(borrower)
    if not borrower_ids:
        raise HTTPException(
            status_code=422,
            detail=f"{action_label} requires borrower evidence; refresh the borrower recommendation before deciding.",
        )

    ids = list(
        dict.fromkeys(str(value).strip() for value in payload_ids if str(value).strip())
    )
    if not ids:
        return borrower_ids

    allowed = set(borrower_ids)
    invalid = [value for value in ids if value not in allowed]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"{action_label} evidence_ids must belong to the borrower recommendation.",
        )
    return ids


def _marketing_audit_payload(borrower: Any) -> dict[str, Any]:
    last_touch_at = _coerce_datetime(getattr(borrower, "last_touch_at", None))
    eligible_recontact_at = _coerce_datetime(
        getattr(borrower, "eligible_recontact_at", None)
    )
    return {
        "marketing_eligible": bool(getattr(borrower, "marketing_eligible", False)),
        "consent_status": str(getattr(borrower, "consent_status", "unknown") or "unknown"),
        "suppression_reason": getattr(borrower, "suppression_reason", None),
        "dnc": bool(getattr(borrower, "dnc", False)),
        "eligibility_source": str(
            getattr(borrower, "eligibility_source", None) or DEFAULT_ELIGIBILITY_SOURCE
        ),
        "last_touch_at": last_touch_at.isoformat() if last_touch_at else None,
        "eligible_recontact_at": (
            eligible_recontact_at.isoformat() if eligible_recontact_at else None
        ),
    }


def _resolve_disclosure_or_http(lakebase: LakebaseClient, *, borrower: Any, channel: str):
    try:
        return resolve_tenant_disclosure(
            lakebase,
            state=str(getattr(borrower, "state", "") or ""),
            channel=channel,
            tenant_id=settings.effective_tenant_id(),
        )
    except MissingTenantDisclosureError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc


def _compose_reject_rationale(code: str, note: str | None) -> str:
    label = code.replace("_", " ")
    clean_note = scrub_free_text(note) if note else None
    return f"{label}: {clean_note}" if clean_note else label


_DRAFT_PLACEHOLDER_PATTERNS: tuple[str, ...] = (
    r"\[(?:first|last|full)[_\s-]?name\]",
    r"\{(?:first|last|full)[_\s-]?name\}",
    r"\binsert governed\b",
)


def _assert_disclosure_backed_draft_body(
    *,
    draft_body: str | None,
    disclosure: Any,
    channel: str,
) -> str:
    """Require the approved body to be a concrete disclosure-backed draft.

    Approval is the governance boundary before an operator can queue outreach.
    A caller must approve the actual body shown to the human reviewer, and that
    body must carry the tenant disclosure block resolved for the borrower's
    state/channel. We reject instead of silently scrubbing when the body still
    contains unresolved placeholders or obvious PII-like text.
    """

    body = (draft_body or "").strip()
    if not body:
        raise HTTPException(
            status_code=422,
            detail="approved draft_body is required and must come from /api/outreach/draft",
        )
    lowered = body.lower()
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _DRAFT_PLACEHOLDER_PATTERNS):
        raise HTTPException(
            status_code=422,
            detail="approved draft_body contains unresolved placeholder copy",
        )
    disclosure_body = str(getattr(disclosure, "body", "") or "").strip()
    if not disclosure_body or disclosure_body not in body:
        raise HTTPException(
            status_code=422,
            detail="approved draft_body must include the configured tenant disclosure",
        )
    if scrub_free_text(body) != body:
        raise HTTPException(
            status_code=422,
            detail="approved draft_body contains PII-like text and cannot be audited",
        )
    try:
        assert_no_protected_class_marketing_text(body, field_name="approved draft_body")
        borrower_copy = body.replace(disclosure_body, " ").strip()
        assert_public_campaign_text(
            borrower_copy,
            field_name="approved draft_body",
            max_length=5000,
        )
        assert_borrower_campaign_copy(
            borrower_copy,
            field_name="approved draft text",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if channel == "sms" and len(body) > 160:
        raise HTTPException(
            status_code=422,
            detail="approved SMS draft_body must be 160 characters or fewer",
        )
    return body


def _assert_final_draft_subject(*, draft_subject: str | None, channel: str) -> str | None:
    """Validate the exact subject shown to the human approver.

    Email and direct mail require a concrete subject. SMS has no subject and
    rejects non-empty text so a client cannot audit copy that the channel will
    never deliver.
    """

    subject = (draft_subject or "").strip()
    if channel == "sms":
        if subject:
            raise HTTPException(status_code=422, detail="approved SMS drafts must not include a subject")
        return None
    if not subject:
        raise HTTPException(
            status_code=422,
            detail="approved draft_subject is required for email and direct mail",
        )
    if scrub_free_text(subject) != subject:
        raise HTTPException(
            status_code=422,
            detail="approved draft_subject contains PII-like text and cannot be audited",
        )
    try:
        assert_public_campaign_text(
            subject,
            field_name="approved draft_subject",
            max_length=120,
        )
        assert_borrower_campaign_copy(
            subject,
            field_name="approved draft_subject",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return subject


def _persist_generated_outreach_draft(
    lakebase: LakebaseClient,
    *,
    actor: str,
    borrower: Any,
    payload: OutreachDraftRequest,
    response: OutreachDraft,
) -> OutreachDraft:
    generation_id = str(uuid4())
    audit_id = str(uuid4())
    response_json = _canonical_intent(response.model_dump(mode="json"))
    response_hash = _intent_hash(response_json)
    audit_params = build_audit_insert_params(
        actor=actor,
        action="draft_outreach",
        entity_type="outreach_draft",
        entity_id=generation_id,
        payload_json={
            "channel": payload.channel,
            "offer_code": response.offer_code,
            "campaign_id": payload.campaign_id,
            "variant_name": payload.variant_name,
            "generation_mode": response.generation_mode,
            **_marketing_audit_payload(borrower),
            "disclosure_version": response.disclosure_version,
            "disclosure_state": response.disclosure_state,
        },
        event_type="DRAFT_OUTREACH",
        subject_clip=borrower.clip_id,
    )
    row = lakebase.fetchone(
        _GENERATED_OUTREACH_DRAFT_INSERT,
        {
            **audit_params,
            "audit_id": audit_id,
            "generation_id": generation_id,
            "borrower_id": response.borrower_id,
            "campaign_id": payload.campaign_id,
            "variant_name": payload.variant_name,
            "channel": response.channel,
            "offer_code": response.offer_code,
            "generation_mode": response.generation_mode,
            "response_hash": response_hash,
            "response_json": response_json,
        },
    )
    if row is None:
        raise LakebaseError("generated outreach draft was not persisted")
    stored_response = row.get("response_json")
    if isinstance(stored_response, str):
        try:
            stored_response = json.loads(stored_response)
        except json.JSONDecodeError as exc:
            raise LakebaseError("generated outreach draft response is invalid") from exc
    if not isinstance(stored_response, dict):
        raise LakebaseError("generated outreach draft response is missing")
    reconstructed = OutreachDraft.model_validate(stored_response)
    if (
        str(row.get("generation_id") or "") != generation_id
        or str(row.get("audit_event_id") or "") != audit_id
        or str(row.get("response_hash") or "") != response_hash
        or _canonical_intent(reconstructed.model_dump(mode="json")) != response_json
    ):
        raise LakebaseError("generated outreach draft proof does not match its response")
    return reconstructed


@router.post("/draft", response_model=OutreachDraft, responses=JSON_CONTENT_TYPE_RESPONSE)
def draft_outreach(
    payload: OutreachDraftRequest,
    request: Request,
    repo: RepoDep,
    audit: AuditDep,
    lakebase: LakebaseDep,
    _: Annotated[None, Depends(require_json_content_type)],
) -> OutreachDraft:
    actor = resolve_actor(request)
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
    draft = compose_intelligent_outreach(
        borrower=b,
        channel=payload.channel,
        disclosure=disclosure,
    )
    response = OutreachDraft(
        borrower_id=b.borrower_id,
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


@router.post("/approve", response_model=OutreachApproveResponse, responses=JSON_CONTENT_TYPE_RESPONSE)
def approve_outreach(
    payload: OutreachApproveRequest,
    request: Request,
    background: BackgroundTasks,
    repo: RepoDep,
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
    # 2026-06-11 audit P2-5: ``require_approver`` gates the decision when
    # the operator sets MIP_APPROVER_EMAILS; with the default empty
    # allowlist it admits every authenticated workspace user (documented
    # Module 0 demo posture) and returns the same edge-resolved actor.
    actor = require_approver(request)
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
    normalized_draft_body = (payload.draft_body or "").strip()
    normalized_draft_subject = (payload.draft_subject or "").strip() or None
    assigned_to_email = payload.assigned_to_email
    safe_rationale = scrub_free_text(payload.rationale) if payload.rationale else None
    safe_bulk_rationale = scrub_free_text(payload.bulk_rationale) if payload.bulk_rationale else None
    approval_rationale = (
        safe_rationale
        or safe_bulk_rationale
        or "Approved with governed recommendation and human review."
    )
    decision_intent = _canonical_intent(
        {
            "action": "approve",
            "actor": actor,
            "borrower_id": payload.borrower_id,
            "offer_code": offer_code,
            "channel": payload.channel,
            "campaign_id": payload.campaign_id,
            "variant_name": payload.variant_name,
            "evidence_ids": audit_evidence_ids,
            "rationale": safe_rationale,
            "bulk_id": payload.bulk_id,
            "bulk_rationale": safe_bulk_rationale,
            "draft_body": normalized_draft_body,
            "draft_subject": normalized_draft_subject,
            "assigned_to_email": assigned_to_email,
            "follow_up_in_days": payload.follow_up_in_days,
        }
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
        "campaign_id": payload.campaign_id,
        "variant_name": payload.variant_name,
        "decision_inputs": decision_inputs_from_borrower(borrower),
        **_marketing_audit_payload(borrower),
        **disclosure_audit_payload(disclosure),
    }
    audit_payload["request_id"] = effective_request_id
    audit_payload["draft_body"] = approved_draft_body
    audit_payload["draft_subject"] = approved_draft_subject
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
    }
    try:
        if _supports_atomic_outreach_write(lakebase):
            response_data, created_new = _commit_outreach_decision_atomic(
                lakebase,
                approval_id=approval_id,
                actor=actor,
                action="approve",
                borrower_id=payload.borrower_id,
                campaign_id=payload.campaign_id,
                offer_code=offer_code,
                rationale=approval_rationale,
                request_id=effective_request_id,
                audit_payload=audit_payload,
                evidence_ids=audit_evidence_ids,
                event_action="outreach.approve",
                event_type="APPROVE",
                audit_request_id=effective_request_id,
                decision_intent=decision_intent,
                response_payload=response_payload,
                subject_clip=borrower.clip_id,
                assigned_to_email=assigned_to_email,
                follow_up_at=follow_up_at,
            )
        else:
            lakebase.execute(
                _APPROVAL_INSERT,
                {
                    "approval_id": approval_id,
                    "campaign_id": payload.campaign_id,
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
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
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
    borrower = repo.find_borrower(payload.borrower_id)
    if borrower is None:
        raise HTTPException(status_code=404, detail=f"Borrower {payload.borrower_id} not found")
    offer_code = payload.offer_code or _safe_offer_code(
        getattr(borrower, "recommended_offer_code", None)
    )
    ensure_approval_idempotency_column(lakebase)
    safe_rationale = _compose_reject_rationale(payload.rationale_code, payload.rationale)
    audit_evidence_ids = _decision_evidence_ids(
        payload.evidence_ids,
        borrower,
        action_label="Rejection",
    )
    decision_intent = _canonical_intent(
        {
            "action": "reject",
            "actor": actor,
            "borrower_id": payload.borrower_id,
            "offer_code": offer_code,
            "channel": payload.channel,
            "campaign_id": payload.campaign_id,
            "variant_name": payload.variant_name,
            "evidence_ids": audit_evidence_ids,
            "rationale_code": payload.rationale_code,
            "rationale": safe_rationale,
        }
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
        "campaign_id": payload.campaign_id,
        "variant_name": payload.variant_name,
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
                campaign_id=payload.campaign_id,
                offer_code=offer_code,
                rationale=safe_rationale,
                request_id=effective_request_id,
                audit_payload=audit_payload,
                evidence_ids=audit_evidence_ids,
                event_action="outreach.reject",
                event_type="OUTREACH_REJECT",
                audit_request_id=effective_request_id,
                decision_intent=decision_intent,
                response_payload=response_payload,
                subject_clip=borrower.clip_id,
            )
        else:
            lakebase.execute(
                _APPROVAL_INSERT,
                {
                    "approval_id": approval_id,
                    "campaign_id": payload.campaign_id,
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
        raise HTTPException(
            status_code=503, detail=safe_dependency_detail("lakebase")
        ) from exc
    response = OutreachRejectResponse.model_validate(response_data)
    if not created_new:
        return response
    clear_sales_state_cache()
    # Same debounced fire-and-forget sync the approve path uses so the
    # funnel / lifecycle views reflect rejected-borrower counts promptly.
    if response.audit_event_id:
        enqueue_lifecycle_trigger(background, reason="rejection")
    return response
