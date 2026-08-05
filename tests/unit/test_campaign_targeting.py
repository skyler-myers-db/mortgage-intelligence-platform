from __future__ import annotations

from typing import Any

import pytest

from backend.services.campaign_targeting import (
    campaign_contains_borrower,
    campaign_treatment_fingerprint,
)


class _CaptureLeadRepository:
    def __init__(self, *, result: bool = True) -> None:
        self.result = result
        self.treatment_kwargs: dict[str, Any] = {}

    def is_campaign_treatment_member(self, **kwargs: Any) -> bool:
        self.treatment_kwargs = kwargs
        return self.result


def _fingerprint(**updates: object) -> str:
    contract: dict[str, object] = {
        "json_contract_version": 1,
        "criteria": {"states": ["IL"], "marketing_eligibility": "Eligible only"},
        "suppression_policy": {"default": "eligible_only", "frequency_cap_days": 30},
        "holdout": {"method": "hash_modulo", "size_pct": 10},
        "household_dedup": {
            "enabled": False,
            "dedupe_unit": "borrower",
            "primary_contact_strategy": "highest_opportunity_eligible",
        },
        **updates,
    }
    return campaign_treatment_fingerprint(**contract)  # type: ignore[arg-type]


def test_treatment_contract_fingerprint_is_canonical_and_binds_execution_fields() -> None:
    expected = _fingerprint()
    assert expected == campaign_treatment_fingerprint(
        json_contract_version=1,
        criteria={"marketing_eligibility": "Eligible only", "states": ["IL"]},
        suppression_policy={"frequency_cap_days": 30, "default": "eligible_only"},
        holdout={"size_pct": 10, "method": "hash_modulo"},
        household_dedup={
            "primary_contact_strategy": "highest_opportunity_eligible",
            "dedupe_unit": "borrower",
            "enabled": False,
        },
    )
    assert expected != _fingerprint(criteria={"states": ["NY"]})
    assert expected != _fingerprint(suppression_policy={"frequency_cap_days": 60})
    assert expected != _fingerprint(holdout={"method": "hash_modulo", "size_pct": 20})
    assert expected != _fingerprint(
        household_dedup={
            "enabled": True,
            "dedupe_unit": "household",
            "primary_contact_strategy": "highest_opportunity_eligible",
        }
    )


def test_bound_campaign_uses_only_immutable_t0_proof_and_live_frequency_cap() -> None:
    repo = _CaptureLeadRepository()

    assert campaign_contains_borrower(
        repo,  # type: ignore[arg-type]
        borrower_id="B-0000000000001",
        campaign_id="11111111-1111-4111-8111-111111111111",
        materialization_id="22222222-2222-4222-8222-222222222222",
        delta_version=17,
        treatment_fingerprint="a" * 64,
        suppression_policy={"default": "eligible_only", "frequency_cap_days": 60},
    )

    assert repo.treatment_kwargs == {
        "borrower_id": "B-0000000000001",
        "campaign_id": "11111111-1111-4111-8111-111111111111",
        "materialization_id": "22222222-2222-4222-8222-222222222222",
        "delta_version": 17,
        "treatment_fingerprint": "a" * 64,
        "frequency_cap_days": 60,
    }


def test_t0_membership_result_fails_closed() -> None:
    repo = _CaptureLeadRepository(result=False)

    assert not campaign_contains_borrower(
        repo,  # type: ignore[arg-type]
        borrower_id="B-0000000000001",
        campaign_id="11111111-1111-4111-8111-111111111111",
        materialization_id="22222222-2222-4222-8222-222222222222",
        delta_version=17,
        treatment_fingerprint="a" * 64,
    )


@pytest.mark.parametrize("frequency_cap", [True, 29, 366, "30"])
def test_bound_campaign_rejects_invalid_live_frequency_contract(frequency_cap: object) -> None:
    repo = _CaptureLeadRepository()

    with pytest.raises(ValueError, match="campaign suppression contract"):
        campaign_contains_borrower(
            repo,  # type: ignore[arg-type]
            borrower_id="B-0000000000001",
            campaign_id="11111111-1111-4111-8111-111111111111",
            materialization_id="22222222-2222-4222-8222-222222222222",
            delta_version=17,
            treatment_fingerprint="a" * 64,
            suppression_policy={"frequency_cap_days": frequency_cap},
        )

    assert repo.treatment_kwargs == {}
