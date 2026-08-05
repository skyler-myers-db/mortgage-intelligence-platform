"""Validate immutable campaign treatment proof before activation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from backend.schemas.portfolio import (
    CAMPAIGN_TREATMENT_ALGORITHM_VERSION,
    HouseholdDedupConfig,
    project_public_campaign_json_field,
)
from backend.services.campaign_targeting import campaign_treatment_fingerprint

CAMPAIGN_ACTIVATION_ERROR = (
    "campaign must be active with a valid saved treatment proof at activation time"
)

CAMPAIGN_PROOF_COLUMNS = """
SELECT c.status, c.json_contract_version, c.criteria, c.suppression_policy,
       c.holdout, c.household_dedup, c.treatment_state,
       c.treatment_materialization_id::text, c.treatment_algorithm_version,
       c.treatment_contract_fingerprint, c.treatment_fingerprint,
       c.treatment_source_snapshot_id, c.treatment_delta_version,
       c.treatment_assignment_digest, c.treatment_candidate_count,
       c.treatment_selected_primary_count, c.treatment_count,
       c.treatment_holdout_count, c.treatment_materialized_at,
       a.approval_id::text, a.borrower_id, a.action, a.actor_email,
       a.offer_code, a.campaign_id::text, a.variant_name, a.channel,
       a.decision_intent, a.decision_payload_hash
FROM mip_app.approvals AS a
JOIN mip_app.campaigns AS c ON c.campaign_id = a.campaign_id
"""

CAMPAIGN_PROOF_FOR_APPROVAL = (
    CAMPAIGN_PROOF_COLUMNS
    + """
WHERE a.approval_id = %(approval_id)s::uuid
  AND a.borrower_id = %(borrower_id)s
  AND a.action = 'approve'
  AND c.campaign_id = %(campaign_id)s::uuid
LIMIT 1
"""
)

ACTIVE_CAMPAIGN_APPROVAL_LOCK = (
    CAMPAIGN_PROOF_COLUMNS
    + """
WHERE a.approval_id = %(approval_id)s::uuid
  AND a.borrower_id = %(borrower_id)s
  AND a.action = 'approve'
  AND c.campaign_id = %(campaign_id)s::uuid
  AND c.status = 'active'
FOR SHARE OF c
"""
)


@dataclass(frozen=True)
class CampaignActivationProof:
    """Exact immutable campaign proof authorized for one approved borrower."""

    campaign_id: str
    channel: str
    offer_code: str | None
    materialization_id: str
    delta_version: int
    treatment_fingerprint: str
    suppression_policy: dict[str, object]
    decision_intent: str
    decision_payload_hash: str


def _coerce_json_object(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("campaign proof JSON is invalid")


def _proof_integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} is invalid")
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{field_name} is invalid")
    return parsed


def _canonical_decision_intent(value: Any) -> tuple[dict[str, object], str, str]:
    intent_text = str(value or "")
    if not intent_text:
        raise ValueError("approval decision intent is missing")
    parsed = json.loads(intent_text)
    if not isinstance(parsed, dict):
        raise ValueError("approval decision intent is invalid")
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if canonical != intent_text:
        raise ValueError("approval decision intent is not canonical")
    digest = hashlib.sha256(intent_text.encode("utf-8")).hexdigest()
    return dict(parsed), intent_text, digest


def campaign_activation_proof_from_row(
    row: dict[str, Any],
    *,
    approval_id: str,
    borrower_id: str,
    campaign_id: str,
) -> CampaignActivationProof:
    """Validate the full Lakebase manifest and immutable approval binding."""

    if (
        str(row.get("status") or "").strip().lower() != "active"
        or str(row.get("treatment_state") or "") != "ready"
        or str(row.get("treatment_algorithm_version") or "")
        != CAMPAIGN_TREATMENT_ALGORITHM_VERSION
    ):
        raise PermissionError(CAMPAIGN_ACTIVATION_ERROR)
    if (
        str(row.get("approval_id") or "") != approval_id
        or str(row.get("borrower_id") or "") != borrower_id
        or str(row.get("campaign_id") or "") != campaign_id
        or row.get("action") != "approve"
    ):
        raise PermissionError(CAMPAIGN_ACTIVATION_ERROR)

    try:
        contract_version = _proof_integer(
            row.get("json_contract_version"),
            field_name="campaign JSON contract version",
        )
        if contract_version != 1:
            raise ValueError("campaign JSON contract version is invalid")
        criteria = project_public_campaign_json_field(
            "criteria",
            _coerce_json_object(row.get("criteria")),
        )
        suppression = project_public_campaign_json_field(
            "suppression_policy",
            _coerce_json_object(row.get("suppression_policy")),
        )
        holdout_raw = row.get("holdout")
        holdout = project_public_campaign_json_field(
            "holdout",
            None if holdout_raw is None else _coerce_json_object(holdout_raw),
        )
        household_dedup = HouseholdDedupConfig.model_validate(
            _coerce_json_object(row.get("household_dedup"))
        )
        if not isinstance(criteria, dict) or not isinstance(suppression, dict):
            raise ValueError("campaign treatment contract is invalid")
        if holdout is not None and not isinstance(holdout, dict):
            raise ValueError("campaign treatment contract is invalid")
        contract_fingerprint = campaign_treatment_fingerprint(
            json_contract_version=contract_version,
            criteria=criteria,
            suppression_policy=suppression,
            holdout=holdout,
            household_dedup=household_dedup.model_dump(mode="json"),
        )
        if contract_fingerprint != str(row.get("treatment_contract_fingerprint") or ""):
            raise ValueError("campaign treatment contract fingerprint is invalid")

        materialization_id = str(UUID(str(row.get("treatment_materialization_id") or "")))
        delta_version = _proof_integer(
            row.get("treatment_delta_version"),
            field_name="campaign treatment Delta version",
        )
        treatment_fingerprint = str(row.get("treatment_fingerprint") or "")
        source_snapshot_id = str(row.get("treatment_source_snapshot_id") or "")
        assignment_digest = str(row.get("treatment_assignment_digest") or "")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (
                treatment_fingerprint,
                source_snapshot_id,
                assignment_digest,
            )
        ):
            raise ValueError("campaign treatment manifest digest is invalid")
        candidate_count = _proof_integer(
            row.get("treatment_candidate_count"),
            field_name="campaign treatment candidate count",
        )
        selected_primary_count = _proof_integer(
            row.get("treatment_selected_primary_count"),
            field_name="campaign treatment selected-primary count",
        )
        treatment_count = _proof_integer(
            row.get("treatment_count"),
            field_name="campaign treatment count",
        )
        holdout_count = _proof_integer(
            row.get("treatment_holdout_count"),
            field_name="campaign holdout count",
        )
        if (
            not row.get("treatment_materialized_at")
            or selected_primary_count > candidate_count
            or selected_primary_count != treatment_count + holdout_count
        ):
            raise ValueError("campaign treatment manifest counts are invalid")
        fingerprint_material = (
            f"{CAMPAIGN_TREATMENT_ALGORITHM_VERSION}:{campaign_id}:{contract_fingerprint}:"
            f"{source_snapshot_id}:{assignment_digest}:{candidate_count}:"
            f"{selected_primary_count}:{treatment_count}:{holdout_count}"
        )
        expected_treatment_fingerprint = hashlib.sha256(
            fingerprint_material.encode("utf-8")
        ).hexdigest()
        if treatment_fingerprint != expected_treatment_fingerprint:
            raise ValueError("campaign treatment fingerprint is inconsistent with its manifest")

        intent, intent_text, intent_digest = _canonical_decision_intent(
            row.get("decision_intent")
        )
        if str(row.get("decision_payload_hash") or "") != intent_digest:
            raise ValueError("approval decision proof digest is invalid")
        expected_intent = {
            "action": "approve",
            "actor": str(row.get("actor_email") or ""),
            "borrower_id": borrower_id,
            "campaign_id": campaign_id,
            "variant_name": row.get("variant_name"),
            "channel": row.get("channel"),
            "offer_code": row.get("offer_code"),
            "campaign_treatment_fingerprint": treatment_fingerprint,
        }
        if any(intent.get(key) != value for key, value in expected_intent.items()):
            raise ValueError("approval decision proof does not match campaign treatment")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PermissionError(CAMPAIGN_ACTIVATION_ERROR) from exc

    return CampaignActivationProof(
        campaign_id=campaign_id,
        channel=str(row.get("channel") or ""),
        offer_code=(
            str(row.get("offer_code")) if row.get("offer_code") is not None else None
        ),
        materialization_id=materialization_id,
        delta_version=delta_version,
        treatment_fingerprint=treatment_fingerprint,
        suppression_policy=suppression,
        decision_intent=intent_text,
        decision_payload_hash=intent_digest,
    )
