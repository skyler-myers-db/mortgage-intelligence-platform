"""Generated outreach drafts: response hashing, governed copy verification of
the draft body and subject, replay of a persisted draft, and the draft insert."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from backend.config.settings import settings
from backend.schemas._validators_protected_class import assert_no_protected_class_marketing_text
from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachDraft,
    OutreachDraftRequest,
)
from backend.schemas.portfolio_campaign import (
    assert_borrower_campaign_copy,
    assert_public_campaign_text,
    remove_configured_public_lender_phrase,
)
from backend.services.audit_lakebase_store import build_audit_insert_params
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.outreach_campaign_gate import _marketing_audit_payload
from backend.services.outreach_decision_intent import (
    _canonical_intent,
    _coerce_datetime,
    _intent_hash,
)
from backend.services.outreach_intelligence import GovernedCampaignVariant
from backend.services.pii_redaction import scrub_free_text

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

_GENERATED_OUTREACH_DRAFT_LOOKUP = """
SELECT generation_id, audit_event_id, actor_email, borrower_id, campaign_id,
       variant_name, channel, offer_code, generation_mode, response_hash, response_json
FROM mip_app.generated_outreach_drafts
WHERE generation_id = %(generation_id)s
  AND actor_email = %(actor_email)s
  AND borrower_id = %(borrower_id)s
  AND campaign_id IS NOT DISTINCT FROM %(campaign_id)s
  AND variant_name IS NOT DISTINCT FROM %(variant_name)s
LIMIT 1
"""

_LOCAL_TEST_APP_ENVS = frozenset({"local", "test"})


def _outreach_draft_response_hash(response: OutreachDraft) -> str:
    payload = response.model_dump(mode="json", exclude={"response_hash"})
    if response.campaign_id is None and response.variant_name is None:
        # Preserve verification of campaign-less proofs emitted before these
        # fields existed. Bound drafts always retain both fields in the hash.
        payload.pop("campaign_id", None)
        payload.pop("variant_name", None)
        payload.pop("campaign_treatment_fingerprint", None)
    return _intent_hash(_canonical_intent(payload))


def _refresh_timestamp(value: Any) -> datetime | None:
    parsed = _coerce_datetime(value)
    return parsed.astimezone(UTC) if parsed is not None else None


def _verified_generated_draft(
    lakebase: LakebaseClient,
    *,
    payload: OutreachApproveRequest,
    actor: str,
    borrower: Any,
    offer_code: str,
    campaign_variant: GovernedCampaignVariant | None,
) -> tuple[OutreachDraft | None, bool]:
    """Load and verify the audited draft that the operator is approving.

    Production approvals must reference the exact generated artifact returned
    by ``/outreach/draft``. The final copy may be edited by the human reviewer,
    but the immutable origin, source snapshot, offer, and channel remain bound
    to the decision and are revalidated from Lakebase rather than trusted from
    request fields.
    """

    generation_id = (payload.draft_generation_id or "").strip()
    if not generation_id:
        if settings.app_env.strip().lower() in _LOCAL_TEST_APP_ENVS:
            return None, False
        raise HTTPException(
            status_code=422,
            detail="Approval requires the audited generated draft proof.",
        )

    row = lakebase.fetchone(
        _GENERATED_OUTREACH_DRAFT_LOOKUP,
        {
            "generation_id": generation_id,
            "actor_email": actor,
            "borrower_id": payload.borrower_id,
            "campaign_id": payload.campaign_id,
            "variant_name": payload.variant_name,
        },
    )
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="Generated draft proof was not found; regenerate the draft before approval.",
        )

    stored_json = row.get("response_json")
    if isinstance(stored_json, str):
        try:
            stored_json = json.loads(stored_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=409,
                detail="Generated draft proof is invalid; regenerate the draft before approval.",
            ) from exc
    if not isinstance(stored_json, dict):
        raise HTTPException(
            status_code=409,
            detail="Generated draft proof is invalid; regenerate the draft before approval.",
        )
    try:
        generated = OutreachDraft.model_validate(stored_json)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Generated draft proof is invalid; regenerate the draft before approval.",
        ) from exc

    stored_hash = str(row.get("response_hash") or "")
    expected_hash = _outreach_draft_response_hash(generated)
    proof_matches = (
        str(row.get("generation_id") or "") == generated.generation_id == generation_id
        and str(row.get("actor_email") or "") == actor
        and str(row.get("borrower_id") or "") == payload.borrower_id == generated.borrower_id
        and (str(row.get("campaign_id")) if row.get("campaign_id") is not None else None)
        == payload.campaign_id
        == generated.campaign_id
        and (str(row.get("variant_name")) if row.get("variant_name") is not None else None)
        == payload.variant_name
        == generated.variant_name
        and str(row.get("channel") or "") == payload.channel == generated.channel
        and str(row.get("offer_code") or "") == offer_code == generated.offer_code
        and stored_hash == generated.response_hash == payload.draft_response_hash == expected_hash
        and generated.source_refreshed_at == payload.draft_source_refreshed_at
        and generated.campaign_treatment_fingerprint
        == (campaign_variant.treatment_fingerprint if campaign_variant is not None else None)
    )
    if not proof_matches:
        raise HTTPException(
            status_code=409,
            detail="Generated draft proof does not match this approval; regenerate the draft.",
        )

    current_refresh = _refresh_timestamp(getattr(borrower, "source_refreshed_at", None))
    draft_refresh = _refresh_timestamp(generated.source_refreshed_at)
    if current_refresh is None or draft_refresh is None or current_refresh != draft_refresh:
        raise HTTPException(
            status_code=409,
            detail="Borrower data changed after the draft was generated; regenerate before approval.",
        )

    final_subject = (payload.draft_subject or "").strip() or None
    generated_subject = (generated.subject or "").strip() or None
    final_body = (payload.draft_body or "").strip()
    draft_edited = final_body != generated.body.strip() or final_subject != generated_subject
    if draft_edited:
        raise HTTPException(
            status_code=409,
            detail=(
                "Edited outreach copy cannot be approved from an older proof; "
                "regenerate an audited draft before approval."
            ),
        )
    return generated, draft_edited


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
        borrower_copy = body.replace(disclosure_body, " ").strip()
        assert_no_protected_class_marketing_text(
            remove_configured_public_lender_phrase(borrower_copy),
            field_name="approved draft_body",
        )
        assert_public_campaign_text(
            borrower_copy,
            field_name="approved draft_body",
            max_length=5000,
        )
        assert_borrower_campaign_copy(
            borrower_copy,
            field_name="approved draft text",
            require_cta=True,
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
            raise HTTPException(
                status_code=422, detail="approved SMS drafts must not include a subject"
            )
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
    generation_id = response.generation_id
    audit_id = str(uuid4())
    response_json = _canonical_intent(response.model_dump(mode="json"))
    response_hash = _outreach_draft_response_hash(response)
    if response.response_hash != response_hash:
        raise LakebaseError("generated outreach draft hash is invalid")
    audit_params = build_audit_insert_params(
        actor=actor,
        action="draft_outreach",
        entity_type="outreach_draft",
        entity_id=generation_id,
        payload_json={
            "channel": payload.channel,
            "offer_code": response.offer_code,
            "campaign_id": response.campaign_id,
            "variant_name": response.variant_name,
            "campaign_treatment_fingerprint": response.campaign_treatment_fingerprint,
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
            "campaign_id": response.campaign_id,
            "variant_name": response.variant_name,
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
        or reconstructed.response_hash != response_hash
        or _outreach_draft_response_hash(reconstructed) != response_hash
        or _canonical_intent(reconstructed.model_dump(mode="json")) != response_json
        or reconstructed.campaign_id != payload.campaign_id
        or reconstructed.variant_name != payload.variant_name
    ):
        raise LakebaseError("generated outreach draft proof does not match its response")
    return reconstructed
