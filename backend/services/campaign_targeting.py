"""Replay one reviewed saved-campaign treatment cohort without widening it."""

from __future__ import annotations

import hashlib
import json

from backend.schemas.portfolio import (
    CAMPAIGN_TREATMENT_ALGORITHM_VERSION,
)
from backend.services.repositories import LeadRepository


def campaign_treatment_fingerprint(
    *,
    json_contract_version: int,
    criteria: dict[str, object],
    suppression_policy: dict[str, object],
    holdout: dict[str, object] | None,
    household_dedup: dict[str, object],
) -> str:
    """Hash the complete saved treatment contract used for execution.

    The algorithm version makes later cohort-semantics changes explicit rather
    than silently reinterpreting an already generated outreach proof.
    """

    contract = {
        "treatment_algorithm_version": CAMPAIGN_TREATMENT_ALGORITHM_VERSION,
        "json_contract_version": json_contract_version,
        "criteria": criteria,
        "suppression_policy": suppression_policy,
        "holdout": holdout,
        "household_dedup": household_dedup,
    }
    canonical = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def campaign_contains_borrower(
    repo: LeadRepository,
    *,
    borrower_id: str,
    campaign_id: str,
    materialization_id: str,
    delta_version: int,
    treatment_fingerprint: str,
    suppression_policy: dict[str, object] | None = None,
) -> bool:
    """Authorize one immutable T0 treatment member under today's safety gates."""

    frequency_cap_days = 30
    if suppression_policy is not None and "frequency_cap_days" in suppression_policy:
        raw_frequency_cap_days = suppression_policy["frequency_cap_days"]
        if isinstance(raw_frequency_cap_days, bool) or not isinstance(
            raw_frequency_cap_days,
            int,
        ):
            raise ValueError("campaign suppression contract is invalid")
        frequency_cap_days = raw_frequency_cap_days
    if not 30 <= frequency_cap_days <= 365:
        raise ValueError("campaign suppression contract is invalid")

    return repo.is_campaign_treatment_member(
        borrower_id=borrower_id,
        campaign_id=campaign_id,
        materialization_id=materialization_id,
        delta_version=delta_version,
        treatment_fingerprint=treatment_fingerprint,
        frequency_cap_days=frequency_cap_days,
    )
