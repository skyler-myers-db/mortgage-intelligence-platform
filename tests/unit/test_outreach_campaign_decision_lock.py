"""Transactional campaign proof checks for outreach decisions."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Literal

import pytest
from fastapi import HTTPException

from backend.api import outreach
from backend.schemas.portfolio import (
    HouseholdDedupConfig,
    project_public_campaign_json_field,
)
from backend.services.campaign_targeting import campaign_treatment_fingerprint

CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"


def _campaign_row(*, status: str = "approved", **updates: object) -> dict[str, Any]:
    criteria: dict[str, object] = {"marketing_eligibility": "Eligible only"}
    suppression: dict[str, object] = {"default": "eligible_only"}
    household_dedup = HouseholdDedupConfig().model_dump(mode="json")
    row: dict[str, Any] = {
        "campaign_id": CAMPAIGN_ID,
        "owner_email": "owner@example.com",
        "status": status,
        "json_contract_version": 1,
        "criteria": criteria,
        "suppression_policy": suppression,
        "holdout": None,
        "household_dedup": household_dedup,
        "treatment_state": "ready",
        "treatment_materialization_id": "22222222-2222-4222-8222-222222222222",
        "treatment_algorithm_version": "campaign-treatment-v2",
        "treatment_contract_fingerprint": campaign_treatment_fingerprint(
            json_contract_version=1,
            criteria=criteria,
            suppression_policy=suppression,
            holdout=None,
            household_dedup=household_dedup,
        ),
        "treatment_fingerprint": "a" * 64,
        "treatment_source_snapshot_id": "b" * 64,
        "treatment_delta_version": 17,
    }
    row.update(updates)
    return row


def _refresh_contract_fingerprint(row: dict[str, Any]) -> None:
    criteria = project_public_campaign_json_field("criteria", row["criteria"])
    suppression = project_public_campaign_json_field(
        "suppression_policy",
        row["suppression_policy"],
    )
    holdout = project_public_campaign_json_field("holdout", row["holdout"])
    household_dedup = HouseholdDedupConfig.model_validate(
        row["household_dedup"]
    ).model_dump(mode="json")
    assert isinstance(criteria, dict)
    assert isinstance(suppression, dict)
    assert holdout is None or isinstance(holdout, dict)
    row["treatment_contract_fingerprint"] = campaign_treatment_fingerprint(
        json_contract_version=1,
        criteria=criteria,
        suppression_policy=suppression,
        holdout=holdout,
        household_dedup=household_dedup,
    )


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _Connection:
    def __init__(
        self,
        campaign: dict[str, Any] | None,
        *,
        existing: dict[str, Any] | None = None,
    ) -> None:
        self.campaign = campaign
        self.existing = existing
        self.executed: list[str] = []

    def execute(self, sql: str, _params: dict[str, Any] | None = None) -> _Result:
        self.executed.append(sql)
        if "pg_advisory_xact_lock" in sql:
            return _Result(None)
        if sql == outreach._APPROVAL_LOOKUP_BY_REQUEST_ID:
            return _Result(self.existing)
        if sql == outreach._CAMPAIGN_DECISION_LOCK_LOOKUP:
            return _Result(self.campaign)
        raise AssertionError(f"decision advanced beyond campaign proof lock: {sql}")


class _Transaction(AbstractContextManager[_Connection]):
    def __init__(self, owner: _Lakebase) -> None:
        self.owner = owner

    def __enter__(self) -> _Connection:
        return self.owner.connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: object,
    ) -> Literal[False]:
        self.owner.rolled_back = exc_type is not None
        return False


class _Lakebase:
    _supports_atomic_transactions = True

    def __init__(
        self,
        campaign: dict[str, Any] | None,
        *,
        existing: dict[str, Any] | None = None,
    ) -> None:
        self.connection = _Connection(campaign, existing=existing)
        self.rolled_back = False

    def transaction(self) -> _Transaction:
        return _Transaction(self)


def _commit(
    lakebase: _Lakebase,
    *,
    expected_proof: str,
    action: str = "approve",
) -> tuple[dict[str, Any], bool]:
    return outreach._commit_outreach_decision_atomic(
        lakebase,  # type: ignore[arg-type]
        approval_id="33333333-3333-4333-8333-333333333333",
        actor="owner@example.com",
        action=action,
        borrower_id="B-48291",
        campaign_id=CAMPAIGN_ID,
        variant_name="Primary",
        channel="email",
        offer_code="heloc",
        rationale="Governed decision.",
        request_id="44444444-4444-4444-8444-444444444444",
        audit_payload={},
        evidence_ids=["ev-001", "ev-002", "ev-003"],
        event_action=f"outreach.{action}",
        event_type="APPROVE" if action == "approve" else "OUTREACH_REJECT",
        audit_request_id="44444444-4444-4444-8444-444444444444",
        decision_intent="{}",
        campaign_proof_fingerprint=expected_proof,
        response_payload={},
    )


@pytest.mark.parametrize("status", ["rejected", "archived"])
def test_terminal_campaign_rolls_back_before_decision_insert(status: str) -> None:
    campaign = _campaign_row(status=status)
    lakebase = _Lakebase(campaign)
    expected = outreach._campaign_decision_proof_fingerprint(campaign)

    with pytest.raises(HTTPException) as exc_info:
        _commit(lakebase, expected_proof=expected)

    assert exc_info.value.status_code == 409
    assert lakebase.rolled_back is True
    assert lakebase.connection.executed == [
        outreach.BORROWER_DECISION_LOCK,
        outreach._APPROVAL_LOOKUP_BY_REQUEST_ID,
        outreach._CAMPAIGN_DECISION_LOCK_LOOKUP,
    ]
    assert "FOR SHARE" in lakebase.connection.executed[-1]
    assert "FOR KEY SHARE" not in lakebase.connection.executed[-1]


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("owner_email", "new-owner@example.com"),
        ("treatment_materialization_id", "55555555-5555-4555-8555-555555555555"),
        ("treatment_fingerprint", "c" * 64),
        ("treatment_source_snapshot_id", "d" * 64),
        ("treatment_delta_version", 18),
    ],
)
def test_campaign_proof_drift_rolls_back_before_decision_insert(
    field: str,
    changed: object,
) -> None:
    validated = _campaign_row()
    expected = outreach._campaign_decision_proof_fingerprint(validated)
    changed_campaign = _campaign_row()
    changed_campaign[field] = changed
    lakebase = _Lakebase(changed_campaign)

    with pytest.raises(HTTPException) as exc_info:
        _commit(lakebase, expected_proof=expected)

    assert exc_info.value.status_code == 409
    assert "targeting proof changed" in str(exc_info.value.detail)
    assert lakebase.rolled_back is True
    assert lakebase.connection.executed == [
        outreach.BORROWER_DECISION_LOCK,
        outreach._APPROVAL_LOOKUP_BY_REQUEST_ID,
        outreach._CAMPAIGN_DECISION_LOCK_LOOKUP,
    ]


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        (
            "criteria",
            {
                "marketing_eligibility": "Eligible only",
                "states": ["CA"],
            },
        ),
        (
            "suppression_policy",
            {"marketing_eligibility": "Eligible only"},
        ),
        ("holdout", {"method": "hash_modulo", "size_pct": 10}),
        (
            "household_dedup",
            {
                "enabled": True,
                "dedupe_unit": "household",
                "primary_contact_strategy": "highest_opportunity_eligible",
            },
        ),
    ],
)
def test_internally_valid_targeting_contract_drift_still_rolls_back(
    field: str,
    changed: object,
) -> None:
    validated = _campaign_row()
    expected = outreach._campaign_decision_proof_fingerprint(validated)
    changed_campaign = _campaign_row()
    changed_campaign[field] = changed
    _refresh_contract_fingerprint(changed_campaign)
    lakebase = _Lakebase(changed_campaign)

    with pytest.raises(HTTPException) as exc_info:
        _commit(lakebase, expected_proof=expected)

    assert exc_info.value.status_code == 409
    assert "targeting proof changed" in str(exc_info.value.detail)
    assert lakebase.rolled_back is True
    assert lakebase.connection.executed == [
        outreach.BORROWER_DECISION_LOCK,
        outreach._APPROVAL_LOOKUP_BY_REQUEST_ID,
        outreach._CAMPAIGN_DECISION_LOCK_LOOKUP,
    ]


def test_committed_retry_replays_before_archived_campaign_lock() -> None:
    response = {
        "approved": True,
        "approval_id": "33333333-3333-4333-8333-333333333333",
        "audit_event_id": "55555555-5555-4555-8555-555555555555",
    }
    existing = {
        "approval_id": response["approval_id"],
        "audit_event_id": response["audit_event_id"],
        "actor_email": "owner@example.com",
        "borrower_id": "B-48291",
        "action": "approve",
        "decision_intent": "{}",
        "decision_payload_hash": outreach._intent_hash("{}"),
        "decision_response": response,
    }
    lakebase = _Lakebase(_campaign_row(status="archived"), existing=existing)

    replay, created = _commit(lakebase, expected_proof="a" * 64)

    assert replay == response
    assert created is False
    assert lakebase.rolled_back is False
    assert lakebase.connection.executed == [
        outreach.BORROWER_DECISION_LOCK,
        outreach._APPROVAL_LOOKUP_BY_REQUEST_ID,
    ]


@pytest.mark.parametrize(
    ("action", "status"),
    [
        ("approve", "approved"),
        ("approve", "live"),
        ("approve", "active"),
        ("reject", "draft"),
        ("reject", "pending_review"),
        ("reject", "approved"),
        ("reject", "live"),
        ("reject", "active"),
    ],
)
def test_action_appropriate_status_accepts_exact_locked_proof(
    action: str,
    status: str,
) -> None:
    campaign = _campaign_row(status=status)
    connection = _Connection(campaign)

    outreach._lock_and_revalidate_campaign_decision(
        connection,
        campaign_id=CAMPAIGN_ID,
        action=action,
        expected_proof_fingerprint=outreach._campaign_decision_proof_fingerprint(campaign),
    )

    assert connection.executed == [outreach._CAMPAIGN_DECISION_LOCK_LOOKUP]
