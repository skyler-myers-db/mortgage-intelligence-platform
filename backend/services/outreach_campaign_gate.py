"""Campaign-treatment assignment and contact-eligibility gating for outreach:
the governed campaign variant resolution, marketing eligibility enforcement,
evidence-id reconciliation, and the tenant disclosure lookup."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from backend.config.settings import settings
from backend.schemas.portfolio_campaign import (
    assert_borrower_campaign_copy,
    assert_public_campaign_text,
)
from backend.services.audit_store import AuditStore
from backend.services.campaign_intelligence import (
    campaign_criteria_fingerprint,
    durable_campaign_variant_copy_verified,
)
from backend.services.campaign_targeting import campaign_contains_borrower
from backend.services.disclosures import (
    MissingTenantDisclosureError,
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
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.outreach_decision_intent import (
    _canonical_intent,
    _coerce_datetime,
    _intent_hash,
    _normalized_campaign_decision_proof,
)
from backend.services.outreach_intelligence import GovernedCampaignVariant
from backend.services.pii_redaction import scrub_free_text
from backend.services.rbac import require_admin
from backend.services.repositories import LeadRepository

_CAMPAIGN_ACCESS_LOOKUP = """
SELECT campaign_id::text, owner_email, status, json_contract_version, criteria,
       suppression_policy, holdout, household_dedup,
       treatment_state, treatment_materialization_id::text,
       treatment_algorithm_version, treatment_contract_fingerprint,
       treatment_fingerprint, treatment_source_snapshot_id,
       treatment_delta_version
FROM mip_app.campaigns
WHERE campaign_id = %(campaign_id)s::uuid
LIMIT 1
"""

_CAMPAIGN_VARIANT_LOOKUP = """
SELECT campaign_id::text, variant_name, channel, subject, body,
       generation_mode, generator_label, provenance_key_id,
       provenance_issued_at, provenance_expires_at, provenance_copy_hash,
       provenance_criteria_fingerprint, provenance_performance_fingerprint,
       provenance_token_digest
FROM mip_app.campaign_message_variants
WHERE campaign_id = %(campaign_id)s::uuid
  AND variant_name = %(variant_name)s
  AND channel = %(channel)s
LIMIT 1
"""


def _resolve_governed_campaign_variant(
    lakebase: LakebaseClient,
    *,
    request: Request,
    actor: str,
    campaign_id: str | None,
    variant_name: str | None,
    channel: str,
    borrower_id: str,
    lead_repo: LeadRepository,
    require_approved_copy: bool = False,
) -> GovernedCampaignVariant | None:
    """Authorize and load the exact normalized campaign variant."""

    if campaign_id is None and variant_name is None:
        return None
    if campaign_id is None or variant_name is None:
        raise HTTPException(
            status_code=422,
            detail="campaign_id and variant_name must be supplied together",
        )

    campaign = lakebase.fetchone(_CAMPAIGN_ACCESS_LOOKUP, {"campaign_id": campaign_id})
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    owner_email = str(campaign.get("owner_email") or "").strip().lower()
    if owner_email != actor.lower():
        try:
            require_admin(request)
        except HTTPException:
            raise HTTPException(status_code=404, detail="campaign not found") from None
    campaign_status = str(campaign.get("status") or "").strip().lower()
    if campaign_status not in {"draft", "pending_review", "approved", "live", "active"}:
        raise HTTPException(
            status_code=409,
            detail="Campaign lifecycle state does not allow outreach review.",
        )
    if require_approved_copy and campaign_status not in {"approved", "live", "active"}:
        raise HTTPException(
            status_code=409,
            detail="Campaign outreach cannot be approved before the campaign copy is approved.",
        )
    try:
        contract_version = int(campaign.get("json_contract_version") or 0)
    except (TypeError, ValueError):
        contract_version = 0
    if contract_version != 1 or (
        str(campaign.get("treatment_state") or "") != "ready"
        or str(campaign.get("treatment_algorithm_version") or "") != "campaign-treatment-v2"
    ):
        raise HTTPException(
            status_code=409,
            detail="Campaign must be rebuilt before it can be used for outreach.",
        )
    try:
        campaign_proof = _normalized_campaign_decision_proof(campaign)
        projected_criteria = campaign_proof["criteria"]
        projected_suppression = campaign_proof["suppression_policy"]
        treatment_fingerprint = str(campaign_proof["treatment_fingerprint"])
        is_member = campaign_contains_borrower(
            lead_repo,
            borrower_id=borrower_id,
            campaign_id=campaign_id,
            materialization_id=str(campaign_proof["treatment_materialization_id"]),
            delta_version=int(campaign_proof["treatment_delta_version"]),
            treatment_fingerprint=treatment_fingerprint,
            suppression_policy=projected_suppression,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Campaign targeting contract is invalid; rebuild the campaign.",
        ) from exc
    if not is_member:
        raise HTTPException(
            status_code=409,
            detail="Borrower is not in the saved campaign cohort.",
        )

    row = lakebase.fetchone(
        _CAMPAIGN_VARIANT_LOOKUP,
        {
            "campaign_id": campaign_id,
            "variant_name": variant_name,
            "channel": channel,
        },
    )
    if row is None:
        raise HTTPException(status_code=404, detail="campaign variant not found")

    row_campaign_id = str(row.get("campaign_id") or "").strip()
    row_variant_name = str(row.get("variant_name") or "").strip()
    row_channel = str(row.get("channel") or "").strip()
    if row_campaign_id != campaign_id or row_variant_name != variant_name or row_channel != channel:
        raise HTTPException(status_code=409, detail="campaign variant binding is invalid")
    try:
        subject_value = row.get("subject")
        subject = (
            assert_public_campaign_text(
                subject_value,
                field_name="campaign variant subject",
                max_length=120,
            )
            if subject_value is not None
            else None
        )
        body = assert_public_campaign_text(
            row.get("body") or "",
            field_name="campaign variant body",
            max_length=5000,
        )
        if subject:
            assert_borrower_campaign_copy(subject, field_name="campaign variant subject")
        assert_borrower_campaign_copy(body, field_name="campaign variant body")
        generator_label = assert_public_campaign_text(
            row.get("generator_label") or "Governed campaign variant",
            field_name="campaign variant generator_label",
            max_length=80,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="campaign variant content is invalid") from exc
    if not body:
        raise HTTPException(status_code=409, detail="campaign variant content is invalid")
    if require_approved_copy and not durable_campaign_variant_copy_verified(
        row,
        criteria_fingerprint=campaign_criteria_fingerprint(projected_criteria),
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Campaign variant copy is not bound to durable server proof; "
                "regenerate the campaign message before approval."
            ),
        )
    return GovernedCampaignVariant(
        campaign_id=campaign_id,
        variant_name=variant_name,
        channel=channel,
        subject=subject,
        body=body,
        generation_mode=str(row.get("generation_mode") or "operator"),
        generator_label=generator_label,
        treatment_fingerprint=treatment_fingerprint,
        campaign_owner_email=str(campaign_proof["owner_email"]),
        campaign_proof_fingerprint=_intent_hash(_canonical_intent(campaign_proof)),
    )


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
    let the client invent or selectively omit audit provenance. A supplied set
    must exactly match the canonical borrower evidence list. Legacy clients can
    omit the list and the endpoint will audit all borrower evidence instead.
    """
    borrower_ids = _borrower_evidence_ids(borrower)
    if not borrower_ids:
        raise HTTPException(
            status_code=422,
            detail=f"{action_label} requires borrower evidence; refresh the borrower recommendation before deciding.",
        )

    ids = list(dict.fromkeys(str(value).strip() for value in payload_ids if str(value).strip()))
    if not ids:
        return borrower_ids

    if set(ids) != set(borrower_ids) or len(ids) != len(borrower_ids):
        raise HTTPException(
            status_code=422,
            detail=f"{action_label} evidence_ids must exactly match the borrower recommendation.",
        )
    return borrower_ids


def _marketing_audit_payload(borrower: Any) -> dict[str, Any]:
    last_touch_at = _coerce_datetime(getattr(borrower, "last_touch_at", None))
    eligible_recontact_at = _coerce_datetime(getattr(borrower, "eligible_recontact_at", None))
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
