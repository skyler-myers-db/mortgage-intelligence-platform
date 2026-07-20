from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.schemas.portfolio import HouseholdDedupConfig, project_public_campaign_json_field
from backend.services.campaign_intelligence import (
    campaign_copy_hash,
    campaign_criteria_fingerprint,
)
from backend.services.campaign_targeting import campaign_treatment_fingerprint
from backend.services.repositories import get_lead_repository, get_outreach_repository

client = TestClient(app)
BORROWER_EVIDENCE_IDS = ["ev-001", "ev-002", "ev-003"]
CAMPAIGN_A = "11111111-1111-4111-8111-111111111111"
CAMPAIGN_B = "22222222-2222-4222-8222-222222222222"
FOREIGN_CAMPAIGN = "33333333-3333-4333-8333-333333333333"
OWNER = "skyler@entrada.ai"


@pytest.fixture(autouse=True)
def _configure_exact_approver_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "approver_identities", OWNER)


def _draft(
    *,
    campaign_id: str | None = None,
    variant_name: str | None = None,
    channel: str = "email",
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"borrower_id": "B-48291", "channel": channel}
    if campaign_id is not None:
        payload["campaign_id"] = campaign_id
    if variant_name is not None:
        payload["variant_name"] = variant_name
    response = client.post(
        "/api/outreach/draft",
        json=payload,
        headers=headers or {"X-Forwarded-Email": OWNER},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approval(draft: dict[str, object], **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "borrower_id": "B-48291",
        "offer_code": draft["offer_code"],
        "channel": "email",
        "evidence_ids": BORROWER_EVIDENCE_IDS,
        "draft_subject": draft["subject"],
        "draft_body": draft["body"],
        "draft_generation_id": draft["generation_id"],
        "draft_response_hash": draft["response_hash"],
        "draft_source_refreshed_at": draft["source_refreshed_at"],
    }
    if draft.get("campaign_id") is not None:
        payload["campaign_id"] = draft["campaign_id"]
    if draft.get("variant_name") is not None:
        payload["variant_name"] = draft["variant_name"]
    payload.update(updates)
    return payload


def _install_campaign_rows(
    monkeypatch,
    lakebase,
    *,
    contract_version: int = 1,
    criteria: dict[str, object] | None = None,
    campaign_state: dict[str, object] | None = None,
    verified_copy: bool = True,
) -> None:
    original_fetchone = lakebase.fetchone
    initial_state = campaign_state or {}
    initial_criteria = project_public_campaign_json_field(
        "criteria",
        initial_state.get("criteria", criteria or {"marketing_eligibility": "Eligible only"}),
    )
    initial_suppression = project_public_campaign_json_field(
        "suppression_policy",
        initial_state.get("suppression_policy", {"default": "eligible_only"}),
    )
    initial_holdout = project_public_campaign_json_field(
        "holdout",
        initial_state.get("holdout"),
    )
    initial_dedup = HouseholdDedupConfig.model_validate(
        initial_state.get("household_dedup") or {}
    ).model_dump(mode="json")
    assert isinstance(initial_criteria, dict)
    assert isinstance(initial_suppression, dict)
    assert initial_holdout is None or isinstance(initial_holdout, dict)
    stored_contract_fingerprint = str(
        initial_state.get("treatment_contract_fingerprint")
        or campaign_treatment_fingerprint(
            json_contract_version=int(initial_state.get("json_contract_version", contract_version)),
            criteria=initial_criteria,
            suppression_policy=initial_suppression,
            holdout=initial_holdout,
            household_dedup=initial_dedup,
        )
    )
    campaign_owners = {
        CAMPAIGN_A: OWNER,
        CAMPAIGN_B: OWNER,
        FOREIGN_CAMPAIGN: "foreign-owner@entrada.ai",
    }
    variants = {
        (CAMPAIGN_A, "Primary", "email"): {
            "subject": "Review your mortgage options",
            "body": "A governed primary campaign message. Reply to review your options.",
        },
        (CAMPAIGN_A, "Alternate", "email"): {
            "subject": "A guided mortgage review",
            "body": "A governed alternate campaign message. Reply to compare your options.",
        },
        (CAMPAIGN_B, "Primary", "email"): {
            "subject": "Review a different campaign",
            "body": "A governed second campaign message. Reply to review your options.",
        },
        (FOREIGN_CAMPAIGN, "Primary", "email"): {
            "subject": "Foreign campaign copy",
            "body": "This governed message belongs to another operator. Reply to review.",
        },
    }

    def _fetchone(sql: str, params: dict[str, Any] | None = None):
        values = params or {}
        if "FROM mip_app.campaign_message_variants" in sql:
            key = (
                str(values.get("campaign_id") or ""),
                str(values.get("variant_name") or ""),
                str(values.get("channel") or ""),
            )
            copy = variants.get(key)
            if copy is None:
                return None
            proof = (
                {
                    "generation_mode": "supervisor",
                    "generator_label": "Databricks Agent Responses",
                    "provenance_key_id": "v1",
                    "provenance_issued_at": "2026-07-15T12:00:00Z",
                    "provenance_expires_at": "2026-07-15T13:00:00Z",
                    "provenance_copy_hash": campaign_copy_hash(
                        copy["subject"],
                        copy["body"],
                        variant_name=key[1],
                        channel=key[2],
                    ),
                    "provenance_criteria_fingerprint": campaign_criteria_fingerprint(
                        initial_criteria
                    ),
                    "provenance_performance_fingerprint": None,
                    "provenance_token_digest": "d" * 64,
                }
                if verified_copy
                else {
                    "generation_mode": "operator",
                    "generator_label": "Operator edited",
                }
            )
            return {
                "campaign_id": key[0],
                "variant_name": key[1],
                "channel": key[2],
                **proof,
                **copy,
            }
        if "FROM mip_app.campaigns" in sql:
            campaign_id = str(values.get("campaign_id") or "")
            owner = campaign_owners.get(campaign_id)
            if owner is None:
                return None
            state = campaign_state or {}
            projected_criteria = state.get(
                "criteria", criteria or {"marketing_eligibility": "Eligible only"}
            )
            projected_suppression = state.get("suppression_policy", {"default": "eligible_only"})
            projected_holdout = state.get("holdout")
            projected_dedup = state.get(
                "household_dedup",
                {
                    "enabled": False,
                    "dedupe_unit": "borrower",
                    "primary_contact_strategy": "highest_opportunity_eligible",
                },
            )
            return {
                "campaign_id": campaign_id,
                "owner_email": owner,
                "status": state.get("status", "active"),
                "json_contract_version": state.get("json_contract_version", contract_version),
                "criteria": projected_criteria,
                "suppression_policy": projected_suppression,
                "holdout": projected_holdout,
                "household_dedup": projected_dedup,
                "treatment_state": state.get("treatment_state", "ready"),
                "treatment_materialization_id": state.get(
                    "treatment_materialization_id",
                    "44444444-4444-4444-8444-444444444444",
                ),
                "treatment_algorithm_version": state.get(
                    "treatment_algorithm_version", "campaign-treatment-v2"
                ),
                "treatment_contract_fingerprint": state.get(
                    "treatment_contract_fingerprint",
                    stored_contract_fingerprint,
                ),
                "treatment_fingerprint": state.get("treatment_fingerprint", "a" * 64),
                "treatment_source_snapshot_id": state.get("treatment_source_snapshot_id", "b" * 64),
                "treatment_delta_version": state.get("treatment_delta_version", 17),
            }
        if "FROM mip_app.generated_outreach_drafts" in sql:
            row = original_fetchone(sql, values)
            if row is None:
                return None
            stored = next(
                item
                for item in lakebase.generated_outreach_drafts
                if item["generation_id"] == values["generation_id"]
            )
            return {
                **row,
                "campaign_id": stored.get("campaign_id"),
                "variant_name": stored.get("variant_name"),
            }
        if "FROM mip_app.approvals" in sql and "request_id" in sql:
            request_id = values.get("request_id")
            return next(
                (row for row in lakebase.approvals if row.get("request_id") == request_id),
                None,
            )
        return original_fetchone(sql, values)

    monkeypatch.setattr(lakebase, "fetchone", _fetchone)


def test_production_approval_requires_persisted_draft_proof(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": draft["offer_code"],
            "channel": "email",
            "evidence_ids": BORROWER_EVIDENCE_IDS,
            "draft_subject": draft["subject"],
            "draft_body": draft["body"],
        },
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Approval requires the audited generated draft proof."


def test_production_approval_accepts_exact_draft_proof(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft),
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 200, response.text
    assert response.json()["draft_generation_id"] == draft["generation_id"]
    assert response.json()["draft_edited"] is False


def test_production_approval_rejects_tampered_draft_proof(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft, draft_response_hash="f" * 64),
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 409
    assert "does not match" in response.json()["detail"]


def test_production_approval_rejects_human_edit_from_generated_draft(monkeypatch) -> None:
    draft = _draft()
    monkeypatch.setattr(settings, "app_env", "production")
    edited = f"Please review this updated option. {draft['body']}"

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft, draft_body=edited),
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Edited outreach copy cannot be approved from an older proof; "
        "regenerate an audited draft before approval."
    )


def test_campaign_bound_draft_uses_exact_governed_variant(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)

    draft = _draft(
        campaign_id=CAMPAIGN_A,
        variant_name="Primary",
        headers={"X-Forwarded-Email": OWNER},
    )

    assert draft["campaign_id"] == CAMPAIGN_A
    assert draft["variant_name"] == "Primary"
    assert len(str(draft["campaign_treatment_fingerprint"])) == 64
    assert "governed primary campaign message" in str(draft["body"]).lower()
    assert "alternate campaign message" not in str(draft["body"]).lower()


@pytest.mark.parametrize(
    "status",
    ["rejected", "archived"],
)
def test_campaign_bound_draft_rejects_terminal_lifecycle_state(
    monkeypatch,
    fake_lakebase_client,
    status: str,
) -> None:
    _install_campaign_rows(
        monkeypatch,
        fake_lakebase_client,
        campaign_state={"status": status},
    )

    response = client.post(
        "/api/outreach/draft",
        json={
            "borrower_id": "B-48291",
            "channel": "email",
            "campaign_id": CAMPAIGN_A,
            "variant_name": "Primary",
        },
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Campaign lifecycle state does not allow outreach review."
    assert fake_lakebase_client.generated_outreach_drafts == []


@pytest.mark.parametrize("status", ["draft", "pending_review", "approved", "live", "active"])
def test_campaign_bound_draft_allows_governed_review_lifecycle_states(
    monkeypatch,
    fake_lakebase_client,
    status: str,
) -> None:
    _install_campaign_rows(
        monkeypatch,
        fake_lakebase_client,
        campaign_state={"status": status},
    )

    draft = _draft(
        campaign_id=CAMPAIGN_A,
        variant_name="Primary",
        headers={"X-Forwarded-Email": OWNER},
    )

    assert draft["campaign_id"] == CAMPAIGN_A


def test_campaign_bound_approval_rejects_operator_copy_without_durable_proof(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(
        monkeypatch,
        fake_lakebase_client,
        campaign_state={"status": "active"},
        verified_copy=False,
    )
    draft = _draft(
        campaign_id=CAMPAIGN_A,
        variant_name="Primary",
        headers={"X-Forwarded-Email": OWNER},
    )
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft),
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 409
    assert "not bound to durable server proof" in response.json()["detail"]
    assert fake_lakebase_client.approvals == []


def test_campaign_bound_draft_quarantines_legacy_contract_before_writing(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client, contract_version=0)

    response = client.post(
        "/api/outreach/draft",
        json={
            "borrower_id": "B-48291",
            "channel": "email",
            "campaign_id": CAMPAIGN_A,
            "variant_name": "Primary",
        },
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Campaign must be rebuilt before it can be used for outreach."
    )
    assert fake_lakebase_client.generated_outreach_drafts == []


def test_campaign_bound_draft_rejects_borrower_outside_saved_cohort(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)
    previous = app.dependency_overrides[get_lead_repository]
    outside_repo = MagicMock()
    outside_repo.is_campaign_treatment_member.return_value = False
    app.dependency_overrides[get_lead_repository] = lambda: outside_repo
    try:
        response = client.post(
            "/api/outreach/draft",
            json={
                "borrower_id": "B-48291",
                "channel": "email",
                "campaign_id": CAMPAIGN_A,
                "variant_name": "Primary",
            },
            headers={"X-Forwarded-Email": OWNER},
        )
    finally:
        app.dependency_overrides[get_lead_repository] = previous

    assert response.status_code == 409
    assert response.json()["detail"] == "Borrower is not in the saved campaign cohort."
    assert fake_lakebase_client.generated_outreach_drafts == []


def test_campaign_approval_rechecks_membership_after_draft(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)
    draft = _draft(
        campaign_id=CAMPAIGN_A,
        variant_name="Primary",
        headers={"X-Forwarded-Email": OWNER},
    )
    previous = app.dependency_overrides[get_lead_repository]
    outside_repo = MagicMock()
    outside_repo.is_campaign_treatment_member.return_value = False
    app.dependency_overrides[get_lead_repository] = lambda: outside_repo
    monkeypatch.setattr(settings, "app_env", "production")
    try:
        response = client.post(
            "/api/outreach/approve",
            json=_approval(draft),
            headers={"X-Forwarded-Email": OWNER},
        )
    finally:
        app.dependency_overrides[get_lead_repository] = previous

    assert response.status_code == 409
    assert response.json()["detail"] == "Borrower is not in the saved campaign cohort."
    assert not any(
        "INSERT INTO mip_app.approvals" in sql for sql, _params in fake_lakebase_client.executes
    )


def test_campaign_approval_rejects_treatment_contract_changed_after_draft(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    campaign_state: dict[str, object] = {"holdout": {"method": "hash_modulo", "size_pct": 10}}
    _install_campaign_rows(
        monkeypatch,
        fake_lakebase_client,
        campaign_state=campaign_state,
    )
    draft = _draft(
        campaign_id=CAMPAIGN_A,
        variant_name="Primary",
        headers={"X-Forwarded-Email": OWNER},
    )
    campaign_state["holdout"] = {"method": "hash_modulo", "size_pct": 20}
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft),
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Campaign targeting contract is invalid; rebuild the campaign."
    )
    assert not any(
        "INSERT INTO mip_app.approvals" in sql for sql, _params in fake_lakebase_client.executes
    )


def test_campaign_rejection_rechecks_saved_cohort_membership(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)
    previous = app.dependency_overrides[get_lead_repository]
    outside_repo = MagicMock()
    outside_repo.is_campaign_treatment_member.return_value = False
    app.dependency_overrides[get_lead_repository] = lambda: outside_repo
    try:
        response = client.post(
            "/api/outreach/reject",
            json={
                "borrower_id": "B-48291",
                "campaign_id": CAMPAIGN_A,
                "variant_name": "Primary",
                "channel": "email",
                "rationale_code": "low_intent",
            },
            headers={"X-Forwarded-Email": OWNER},
        )
    finally:
        app.dependency_overrides[get_lead_repository] = previous

    assert response.status_code == 409
    assert response.json()["detail"] == "Borrower is not in the saved campaign cohort."
    assert not any(
        "INSERT INTO mip_app.approvals" in sql for sql, _params in fake_lakebase_client.executes
    )


@pytest.mark.parametrize(
    ("updates", "expected_status"),
    [
        ({"campaign_id": CAMPAIGN_B}, 409),
        ({"variant_name": "Alternate"}, 409),
        ({"campaign_id": None, "variant_name": None}, 409),
    ],
)
def test_approval_rejects_altered_or_missing_campaign_binding(
    monkeypatch,
    fake_lakebase_client,
    updates: dict[str, object],
    expected_status: int,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)
    draft = _draft(
        campaign_id=CAMPAIGN_A,
        variant_name="Primary",
        headers={"X-Forwarded-Email": OWNER},
    )
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.post(
        "/api/outreach/approve",
        json=_approval(draft, **updates),
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == expected_status
    assert "proof" in response.json()["detail"].lower()
    assert not any(
        "INSERT INTO mip_app.approvals" in sql for sql, _params in fake_lakebase_client.executes
    )


def test_draft_hides_foreign_campaign_from_non_admin(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)
    non_admin_client = TestClient(app)
    non_admin_client.headers.pop("X-Forwarded-Groups", None)

    response = non_admin_client.post(
        "/api/outreach/draft",
        json={
            "borrower_id": "B-48291",
            "channel": "email",
            "campaign_id": FOREIGN_CAMPAIGN,
            "variant_name": "Primary",
        },
        headers={
            "X-Forwarded-Email": "analyst@summit.example",
            "X-Forwarded-Groups": "workspace-users",
        },
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "campaign not found"


def test_draft_rejects_nonexistent_or_wrong_channel_variant(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)

    nonexistent = client.post(
        "/api/outreach/draft",
        json={
            "borrower_id": "B-48291",
            "channel": "email",
            "campaign_id": CAMPAIGN_A,
            "variant_name": "Missing",
        },
        headers={"X-Forwarded-Email": OWNER},
    )
    wrong_channel = client.post(
        "/api/outreach/draft",
        json={
            "borrower_id": "B-48291",
            "channel": "sms",
            "campaign_id": CAMPAIGN_A,
            "variant_name": "Primary",
        },
        headers={"X-Forwarded-Email": OWNER},
    )

    assert nonexistent.status_code == 404
    assert nonexistent.json()["detail"] == "campaign variant not found"
    assert wrong_channel.status_code == 404
    assert wrong_channel.json()["detail"] == "campaign variant not found"


def test_draft_rejects_malformed_campaign_uuid(fake_lakebase_client) -> None:
    response = client.post(
        "/api/outreach/draft",
        json={
            "borrower_id": "B-48291",
            "channel": "email",
            "campaign_id": "campaign-not-a-uuid",
            "variant_name": "Primary",
        },
    )

    assert response.status_code == 422
    assert "valid UUID" in response.text
    assert not any(
        "FROM mip_app.campaigns" in sql for sql, _params in fake_lakebase_client.fetchones
    )


def test_approval_rejects_non_uuid_draft_generation_id_before_lakebase_query(
    fake_lakebase_client,
) -> None:
    response = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "draft_generation_id": "genie-11111111-1111-4111-8111-111111111111",
        },
        headers={"X-Forwarded-Email": OWNER},
    )

    assert response.status_code == 422
    assert fake_lakebase_client.fetchones == []
    assert fake_lakebase_client.executes == []


def test_campaign_approval_persists_proof_binding_and_replays_before_borrower_fetch(
    monkeypatch,
    fake_lakebase_client,
) -> None:
    _install_campaign_rows(monkeypatch, fake_lakebase_client)
    draft = _draft(
        campaign_id=CAMPAIGN_A,
        variant_name="Primary",
        headers={"X-Forwarded-Email": OWNER},
    )
    monkeypatch.setattr(settings, "app_env", "production")
    request_payload = _approval(
        draft,
        request_id="44444444-4444-4444-8444-444444444444",
    )
    headers = {"X-Forwarded-Email": OWNER}

    first = client.post("/api/outreach/approve", json=request_payload, headers=headers)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["approval_id"]
    assert first_body["audit_event_id"]
    assert first_body["draft_generation_id"] == draft["generation_id"]
    approval_insert = next(
        params
        for sql, params in fake_lakebase_client.executes
        if "INSERT INTO mip_app.approvals" in sql
    )
    assert approval_insert["campaign_id"] == CAMPAIGN_A
    assert approval_insert["variant_name"] == "Primary"
    assert approval_insert["channel"] == "email"
    decision_intent = json.loads(str(approval_insert["decision_intent"]))
    assert (
        decision_intent["campaign_treatment_fingerprint"] == draft["campaign_treatment_fingerprint"]
    )

    repo = app.dependency_overrides[get_outreach_repository]()
    borrower_fetch = MagicMock(side_effect=AssertionError("replay fetched borrower from UC"))
    monkeypatch.setattr(repo, "find_borrower", borrower_fetch)
    replay = client.post("/api/outreach/approve", json=request_payload, headers=headers)

    assert replay.status_code == 200, replay.text
    assert replay.json() == first_body
    assert replay.json()["approval_id"] == first_body["approval_id"]
    assert replay.json()["audit_event_id"] == first_body["audit_event_id"]
    assert replay.json()["draft_generation_id"] == draft["generation_id"]

    mismatches = (
        {"draft_generation_id": "55555555-5555-4555-8555-555555555555"},
        {"campaign_id": CAMPAIGN_B},
        {"variant_name": "Alternate"},
        {"channel": "direct_mail"},
    )
    for updates in mismatches:
        conflict = client.post(
            "/api/outreach/approve",
            json={**request_payload, **updates},
            headers=headers,
        )
        assert conflict.status_code == 409, (updates, conflict.text)
        assert conflict.json()["detail"] == (
            "request_id already belongs to a different outreach decision"
        )

    approval_inserts = [
        params
        for sql, params in fake_lakebase_client.executes
        if "INSERT INTO mip_app.approvals" in sql
    ]
    assert len(approval_inserts) == 1
    borrower_fetch.assert_not_called()
