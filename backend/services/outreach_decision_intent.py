"""Outreach decision intent: canonical intent payloads, their hashes, the
campaign decision proof, and replay matching against a stored intent."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from backend.schemas.offer import (
    OutreachApproveRequest,
    OutreachRejectRequest,
)
from backend.schemas.portfolio import HouseholdDedupConfig, project_public_campaign_json_field
from backend.services.campaign_targeting import campaign_treatment_fingerprint


def _canonical_intent(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _intent_hash(intent: str) -> str:
    return hashlib.sha256(intent.encode("utf-8")).hexdigest()


def _normalized_campaign_decision_proof(campaign: dict[str, Any]) -> dict[str, Any]:
    """Return the exact campaign proof that must survive until decision commit."""

    contract_raw = campaign.get("json_contract_version")
    try:
        contract_version = int(contract_raw) if contract_raw is not None else 0
    except (TypeError, ValueError) as exc:
        raise ValueError("campaign JSON contract version is invalid") from exc
    if contract_version != 1:
        raise ValueError("campaign JSON contract version is invalid")

    projected_criteria = project_public_campaign_json_field(
        "criteria",
        campaign.get("criteria"),
    )
    projected_suppression = project_public_campaign_json_field(
        "suppression_policy",
        campaign.get("suppression_policy"),
    )
    projected_holdout = project_public_campaign_json_field(
        "holdout",
        campaign.get("holdout"),
    )
    household_dedup = HouseholdDedupConfig.model_validate(
        campaign.get("household_dedup") or {}
    ).model_dump(mode="json")
    if not isinstance(projected_criteria, dict):
        raise ValueError("campaign criteria are invalid")
    if not isinstance(projected_suppression, dict):
        raise ValueError("campaign suppression policy is invalid")
    if projected_holdout is not None and not isinstance(projected_holdout, dict):
        raise ValueError("campaign holdout is invalid")

    contract_fingerprint = campaign_treatment_fingerprint(
        json_contract_version=contract_version,
        criteria=projected_criteria,
        suppression_policy=projected_suppression,
        holdout=projected_holdout,
        household_dedup=household_dedup,
    )
    if contract_fingerprint != str(campaign.get("treatment_contract_fingerprint") or ""):
        raise ValueError("campaign treatment contract fingerprint is invalid")

    treatment_fingerprint = str(campaign.get("treatment_fingerprint") or "")
    materialization_id = str(campaign.get("treatment_materialization_id") or "")
    delta_version_raw = campaign.get("treatment_delta_version")
    if isinstance(delta_version_raw, bool) or not isinstance(delta_version_raw, int | str):
        raise ValueError("campaign treatment Delta version is invalid")
    delta_version = int(delta_version_raw)
    if (
        re.fullmatch(r"[0-9a-f]{64}", treatment_fingerprint) is None
        or not materialization_id
        or delta_version < 0
    ):
        raise ValueError("campaign treatment manifest is invalid")
    if (
        str(campaign.get("treatment_state") or "") != "ready"
        or str(campaign.get("treatment_algorithm_version") or "") != "campaign-treatment-v2"
    ):
        raise ValueError("campaign treatment state is invalid")

    return {
        "campaign_id": str(campaign.get("campaign_id") or ""),
        "owner_email": str(campaign.get("owner_email") or "").strip().lower(),
        "json_contract_version": contract_version,
        "criteria": projected_criteria,
        "suppression_policy": projected_suppression,
        "holdout": projected_holdout,
        "household_dedup": household_dedup,
        "treatment_state": "ready",
        "treatment_materialization_id": materialization_id,
        "treatment_algorithm_version": "campaign-treatment-v2",
        "treatment_contract_fingerprint": contract_fingerprint,
        "treatment_fingerprint": treatment_fingerprint,
        "treatment_source_snapshot_id": str(
            campaign.get("treatment_source_snapshot_id") or ""
        ),
        "treatment_delta_version": delta_version,
    }


def _campaign_decision_proof_fingerprint(campaign: dict[str, Any]) -> str:
    return _intent_hash(_canonical_intent(_normalized_campaign_decision_proof(campaign)))


def _approval_decision_intent(
    payload: OutreachApproveRequest,
    *,
    actor: str,
    offer_code: str,
    evidence_ids: list[str],
    safe_rationale: str | None,
    safe_bulk_rationale: str | None,
    campaign_owner_email: str | None,
    campaign_treatment_fingerprint: str | None,
) -> str:
    return _canonical_intent(
        {
            "action": "approve",
            "actor": actor,
            "borrower_id": payload.borrower_id,
            "offer_code": offer_code,
            "offer_code_supplied": payload.offer_code is not None,
            "channel": payload.channel,
            "campaign_id": payload.campaign_id,
            "variant_name": payload.variant_name,
            "campaign_owner_email": campaign_owner_email,
            "campaign_treatment_fingerprint": campaign_treatment_fingerprint,
            "evidence_ids": evidence_ids,
            "evidence_ids_supplied": bool(_normalized_payload_evidence_ids(payload.evidence_ids)),
            "rationale": safe_rationale,
            "bulk_id": payload.bulk_id,
            "bulk_rationale": safe_bulk_rationale,
            "draft_body": (payload.draft_body or "").strip(),
            "draft_subject": (payload.draft_subject or "").strip() or None,
            "draft_generation_id": payload.draft_generation_id,
            "draft_response_hash": payload.draft_response_hash,
            "draft_source_refreshed_at": payload.draft_source_refreshed_at,
            "assigned_to_email": payload.assigned_to_email,
            "follow_up_in_days": payload.follow_up_in_days,
        }
    )


def _normalized_payload_evidence_ids(evidence_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in evidence_ids if str(value).strip()))


def _reject_decision_intent(
    payload: OutreachRejectRequest,
    *,
    actor: str,
    offer_code: str,
    evidence_ids: list[str],
    safe_rationale: str,
    campaign_id: str | None,
    variant_name: str | None,
    campaign_owner_email: str | None,
    campaign_treatment_fingerprint: str | None,
) -> str:
    return _canonical_intent(
        {
            "action": "reject",
            "actor": actor,
            "borrower_id": payload.borrower_id,
            "offer_code": offer_code,
            "offer_code_supplied": payload.offer_code is not None,
            "channel": payload.channel,
            "campaign_id": campaign_id,
            "variant_name": variant_name,
            "campaign_owner_email": campaign_owner_email,
            "campaign_treatment_fingerprint": campaign_treatment_fingerprint,
            "evidence_ids": evidence_ids,
            "evidence_ids_supplied": bool(_normalized_payload_evidence_ids(payload.evidence_ids)),
            "rationale_code": payload.rationale_code,
            "rationale": safe_rationale,
        }
    )


def _decision_intent_without_owner_claim(decision_intent: str) -> str:
    """Project the pre-owner-claim intent used by durable fallback keys."""

    try:
        parsed = json.loads(decision_intent)
    except json.JSONDecodeError:
        return decision_intent
    if not isinstance(parsed, dict):
        return decision_intent
    parsed.pop("campaign_owner_email", None)
    return _canonical_intent(parsed)


def _source_marker_matches(
    intent: dict[str, Any],
    *,
    marker: str,
    supplied: bool,
) -> bool:
    if marker not in intent:
        # Legacy intents did not record whether a borrower-derived value was
        # omitted by the caller. Only an explicit retry value can prove a
        # match without consulting mutable UC state.
        return supplied
    value = intent.get(marker)
    return isinstance(value, bool) and value is supplied


def _intent_evidence_matches(intent: dict[str, Any], payload_ids: list[str]) -> bool:
    normalized = _normalized_payload_evidence_ids(payload_ids)
    supplied = bool(normalized)
    if not _source_marker_matches(
        intent,
        marker="evidence_ids_supplied",
        supplied=supplied,
    ):
        return False
    if not supplied:
        return True
    stored = intent.get("evidence_ids")
    if not isinstance(stored, list):
        return False
    stored_ids = _normalized_payload_evidence_ids([str(value) for value in stored])
    return len(stored_ids) == len(normalized) and set(stored_ids) == set(normalized)


def _intent_offer_matches(
    intent: dict[str, Any],
    payload_offer_code: str | None,
) -> bool:
    supplied = payload_offer_code is not None
    if not _source_marker_matches(
        intent,
        marker="offer_code_supplied",
        supplied=supplied,
    ):
        return False
    return not supplied or intent.get("offer_code") == payload_offer_code


def _approve_intent_matches_payload(
    intent: dict[str, Any],
    *,
    payload: OutreachApproveRequest,
    actor: str,
    safe_rationale: str | None,
    safe_bulk_rationale: str | None,
) -> bool:
    expected = {
        "action": "approve",
        "actor": actor,
        "borrower_id": payload.borrower_id,
        "channel": payload.channel,
        "campaign_id": payload.campaign_id,
        "variant_name": payload.variant_name,
        "rationale": safe_rationale,
        "bulk_id": payload.bulk_id,
        "bulk_rationale": safe_bulk_rationale,
        "draft_body": (payload.draft_body or "").strip(),
        "draft_subject": (payload.draft_subject or "").strip() or None,
        "draft_generation_id": payload.draft_generation_id,
        "draft_response_hash": payload.draft_response_hash,
        "draft_source_refreshed_at": payload.draft_source_refreshed_at,
        "assigned_to_email": payload.assigned_to_email,
        "follow_up_in_days": payload.follow_up_in_days,
    }
    treatment_fingerprint = intent.get("campaign_treatment_fingerprint")
    campaign_owner_email = intent.get("campaign_owner_email")
    fingerprint_matches = (
        treatment_fingerprint is None
        if payload.campaign_id is None
        else isinstance(treatment_fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", treatment_fingerprint) is not None
    )
    owner_matches = (
        campaign_owner_email is None
        if payload.campaign_id is None
        else campaign_owner_email is None
        or (
            isinstance(campaign_owner_email, str)
            and bool(campaign_owner_email.strip())
        )
    )
    return (
        all(intent.get(key) == value for key, value in expected.items())
        and fingerprint_matches
        and owner_matches
        and _intent_offer_matches(intent, payload.offer_code)
        and _intent_evidence_matches(intent, payload.evidence_ids)
    )


def _reject_intent_matches_payload(
    intent: dict[str, Any],
    *,
    payload: OutreachRejectRequest,
    actor: str,
    safe_rationale: str,
) -> bool:
    expected = {
        "action": "reject",
        "actor": actor,
        "borrower_id": payload.borrower_id,
        "channel": payload.channel,
        "campaign_id": payload.campaign_id,
        "variant_name": payload.variant_name,
        "rationale_code": payload.rationale_code,
        "rationale": safe_rationale,
    }
    treatment_fingerprint = intent.get("campaign_treatment_fingerprint")
    campaign_owner_email = intent.get("campaign_owner_email")
    fingerprint_matches = (
        treatment_fingerprint is None
        if payload.campaign_id is None
        else isinstance(treatment_fingerprint, str)
        and re.fullmatch(r"[0-9a-f]{64}", treatment_fingerprint) is not None
    )
    owner_matches = (
        campaign_owner_email is None
        if payload.campaign_id is None
        else campaign_owner_email is None
        or (
            isinstance(campaign_owner_email, str)
            and bool(campaign_owner_email.strip())
        )
    )
    return (
        all(intent.get(key) == value for key, value in expected.items())
        and fingerprint_matches
        and owner_matches
        and _intent_offer_matches(intent, payload.offer_code)
        and _intent_evidence_matches(intent, payload.evidence_ids)
    )


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
