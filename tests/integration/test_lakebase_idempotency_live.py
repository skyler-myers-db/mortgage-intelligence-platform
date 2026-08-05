"""Live closed-loop idempotency checks against the deployed app.

Skipped unless ``LAKEBASE_INTEGRATION=1`` plus ``MIP_APP_URL`` and a bearer
token are present and ``MIP_LIVE_MUTATION_OK=1`` explicitly permits writes.
This intentionally exercises the app API backed by real Lakebase constraints;
the in-memory fake remains useful for fast unit tests but cannot prove
production ``ON CONFLICT`` / partial-index behavior.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from uuid import uuid4

import pytest

from backend.schemas.portfolio import (
    CampaignRecommendationResponse,
    PortfolioCreateRequest,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from tests.fixtures.live_campaign_lifecycle import approve_campaign_for_outreach
from tests.integration.live_campaign_cleanup import CampaignFixtureTracker
from tools.cleanup_live_campaign_fixtures import run_scoped_campaign_name

APP_URL = (os.environ.get("MIP_APP_URL") or "").rstrip("/")
TOKEN = os.environ.get("MIP_BEARER_TOKEN") or os.environ.get("DATABRICKS_TOKEN") or ""
ADMIN_TOKEN = os.environ.get("MIP_ADMIN_BEARER_TOKEN") or ""
LIVE_MUTATION_OK = os.environ.get("MIP_LIVE_MUTATION_OK") == "1"

pytestmark = pytest.mark.skipif(
    os.environ.get("LAKEBASE_INTEGRATION") != "1"
    or not APP_URL
    or not TOKEN
    or not ADMIN_TOKEN
    or not LIVE_MUTATION_OK,
    reason=(
        "Set LAKEBASE_INTEGRATION=1, MIP_APP_URL, MIP_BEARER_TOKEN/DATABRICKS_TOKEN, "
        "MIP_ADMIN_BEARER_TOKEN, and MIP_LIVE_MUTATION_OK=1 for the dev app"
    ),
)


def _request(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    idempotency_key: str | None = None,
    token: str = TOKEN,
) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(
        f"{APP_URL}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed: object = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed


@pytest.fixture(autouse=True)
def _archive_created_campaigns(monkeypatch: pytest.MonkeyPatch) -> object:
    original_request = _request
    tracker = CampaignFixtureTracker(default_token=TOKEN)

    def tracked_request(*args: object, **kwargs: object) -> tuple[int, object]:
        return tracker.request(original_request, *args, **kwargs)

    monkeypatch.setattr(__name__ + "._request", tracked_request)
    yield
    tracker.cleanup(original_request, admin_token=ADMIN_TOKEN)


def _first_borrower_id() -> str:
    status, body = _request("GET", "/api/leads?limit=1")
    assert status == 200
    assert isinstance(body, list) and body
    borrower_id = body[0].get("borrower_id")
    assert isinstance(borrower_id, str) and borrower_id.startswith("B-")
    return borrower_id


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    assert isinstance(value, str) and value, f"missing {field}: {payload!r}"
    return value


def _live_warehouse_client() -> DatabricksSqlClient:
    """Use the deployment identity only to inspect immutable UC proof rows."""

    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    assert host and token and warehouse_id, (
        "The live treatment-boundary proof requires DATABRICKS_HOST, "
        "DATABRICKS_TOKEN, and DATABRICKS_WAREHOUSE_ID."
    )
    return DatabricksSqlClient(host, token, warehouse_id, timeout_s=50)


def _campaign_assignment_examples(
    *,
    campaign_id: str,
    criteria: dict[str, object],
) -> tuple[str, str, str]:
    """Return one treatment, holdout, and same-household suppressed borrower."""

    state_values = criteria.get("states")
    assert isinstance(state_values, list) and len(state_values) == 1
    state = str(state_values[0])
    min_equity_pct = float(criteria.get("min_equity_pct") or 0)
    assert criteria.get("occupancy") == "Owner-occupied"
    assert criteria.get("recency") == "Untouched 30d"

    client = _live_warehouse_client()
    treatment_table = qualify("audit", "campaign_treatment_snapshot")
    household_table = qualify("gold", "household_rollup")
    borrower_table = qualify("gold", "borrower_360")
    assignments = client.execute(
        f"""
SELECT borrower_id, assignment
FROM {treatment_table}
WHERE campaign_id = :campaign_id
  AND row_kind = 'member'
  AND assignment IN ('treatment', 'holdout')
ORDER BY assignment, borrower_id
""",
        {"campaign_id": campaign_id},
    )
    treatment_id = next(
        (str(row["borrower_id"]) for row in assignments if row.get("assignment") == "treatment"),
        "",
    )
    holdout_id = next(
        (str(row["borrower_id"]) for row in assignments if row.get("assignment") == "holdout"),
        "",
    )
    assert treatment_id, "saved live campaign materialized no treatment borrower"
    assert holdout_id, "saved live campaign materialized no recommended holdout borrower"

    co_owner = client.execute_one(
        f"""
WITH selected_primaries AS (
  SELECT borrower_id
  FROM {treatment_table}
  WHERE campaign_id = :campaign_id
    AND row_kind = 'member'
),
suppressed_candidates AS (
  SELECT DISTINCT candidate.borrower_id
  FROM selected_primaries AS selected
  INNER JOIN {household_table} AS selected_household
    ON selected_household.borrower_id = selected.borrower_id
  INNER JOIN {household_table} AS candidate
    ON candidate.household_id = selected_household.household_id
   AND candidate.borrower_id <> selected.borrower_id
  INNER JOIN {borrower_table} AS borrower
    ON borrower.borrower_id = candidate.borrower_id
  LEFT ANTI JOIN selected_primaries AS already_selected
    ON already_selected.borrower_id = candidate.borrower_id
  WHERE borrower.state = :state
    AND borrower.equity_pct >= :min_equity_pct
    AND borrower.is_owner_occupied = TRUE
    AND {eligible_sql_predicate('borrower')}
    AND COALESCE(borrower.has_unresolved_owner, FALSE) = FALSE
    AND (
      borrower.last_touch_at IS NULL
      OR borrower.last_touch_at < CURRENT_TIMESTAMP() - INTERVAL '30' DAYS
    )
)
SELECT borrower_id
FROM suppressed_candidates
ORDER BY borrower_id
LIMIT 1
""",
        {
            "campaign_id": campaign_id,
            "state": state,
            "min_equity_pct": min_equity_pct,
        },
    )
    co_owner_id = str((co_owner or {}).get("borrower_id") or "")
    assert co_owner_id, "saved live campaign materialized no provable household-dedup exclusion"
    assert len({treatment_id, holdout_id, co_owner_id}) == 3
    return treatment_id, holdout_id, co_owner_id


def _reviewed_campaign_create_payload(
    *,
    name: str,
    criteria: dict[str, object],
    raw_recommendation: dict[str, object],
) -> tuple[dict[str, object], CampaignRecommendationResponse]:
    """Validate and project the server-issued recommendation into create input."""

    recommendation = CampaignRecommendationResponse.model_validate(raw_recommendation)
    message_variants: list[dict[str, object]] = []
    treatment_weight_pct = 100 - recommendation.holdout_pct
    base_weight_pct = treatment_weight_pct / len(recommendation.variants)
    for index, variant in enumerate(recommendation.variants):
        assert variant.provenance_token is not None, (
            "campaign recommendation omitted the server-issued provenance token for "
            f"{variant.variant_name!r}"
        )
        message_variants.append(
            {
                "variant_name": variant.variant_name,
                "channel": "email",
                "subject": variant.subject,
                "body": variant.body,
                "weight_pct": (
                    base_weight_pct
                    if index < len(recommendation.variants) - 1
                    else treatment_weight_pct
                    - base_weight_pct * (len(recommendation.variants) - 1)
                ),
                "generation_mode": recommendation.generation_mode,
                "generator_label": recommendation.generator_label,
                "provenance_token": variant.provenance_token,
            }
        )
    payload: dict[str, object] = {
        "name": name,
        "criteria": criteria,
        "suppression_policy": {"marketing_eligibility": "Eligible only"},
        "message_variants": message_variants,
        "holdout": {"method": "hash_modulo", "size_pct": recommendation.holdout_pct},
        "household_dedup": {
            "enabled": True,
            "dedupe_unit": "household",
            "primary_contact_strategy": "highest_opportunity_eligible",
        },
    }
    PortfolioCreateRequest.model_validate(payload)
    return payload, recommendation


def _tiny_reviewed_campaign_criteria() -> tuple[dict[str, object], list[str]]:
    for state in ("IL", "CA", "FL", "WA"):
        criteria: dict[str, object] = {
            "states": [state],
            "min_equity_pct": 99.9,
            "occupancy": "Owner-occupied",
            "recency": "Untouched 30d",
        }
        preview_status, preview = _request(
            "POST",
            "/api/portfolio/preview",
            {"criteria": criteria, "campaign_build_config": {}},
        )
        if (
            preview_status != 200
            or not isinstance(preview, dict)
            or preview.get("campaign_build_eligible") is not True
            or not isinstance(preview.get("campaign_build_contact_count"), int)
            or int(preview["campaign_build_contact_count"]) <= 0
        ):
            continue
        status, leads = _request(
            "GET",
            f"/api/leads?states={state}&min_equity_pct=99.9&occupancy=Owner-occupied"
            "&recency=Untouched%2030d&limit=100",
        )
        if status == 200 and isinstance(leads, list) and leads:
            borrower_ids = [
                str(lead["borrower_id"])
                for lead in leads
                if isinstance(lead, dict)
                and isinstance(lead.get("borrower_id"), str)
                and str(lead["borrower_id"]).startswith("B-")
            ]
            if borrower_ids:
                return criteria, borrower_ids
    pytest.fail("No lead exists for the bounded live campaign fixture")


def _create_email_campaign_variant() -> tuple[str, str, str, list[str]]:
    criteria, candidate_borrower_ids = _tiny_reviewed_campaign_criteria()
    recommendation_status, raw_recommendation = _request(
        "POST",
        "/api/portfolio/campaign-recommendation",
        {"criteria": criteria},
    )
    assert recommendation_status == 200, raw_recommendation
    assert isinstance(raw_recommendation, dict), raw_recommendation
    payload, recommendation = _reviewed_campaign_create_payload(
        name=run_scoped_campaign_name("Live Lakebase approval contract"),
        criteria=criteria,
        raw_recommendation=raw_recommendation,
    )
    status, created = _request(
        "POST",
        "/api/portfolio/create",
        payload,
        idempotency_key=f"live-approval-campaign-{uuid4()}",
    )
    assert status == 200, created
    assert isinstance(created, dict)
    campaign_id = _required_string(created, "campaign_id")

    status, campaign = _request("GET", f"/api/campaigns/{campaign_id}")
    assert status == 200, campaign
    assert isinstance(campaign, dict)
    variants = campaign.get("message_variants")
    assert isinstance(variants, list)
    expected = recommendation.variants[0]
    persisted = next(
        (
            variant
            for variant in variants
            if isinstance(variant, dict) and variant.get("variant_name") == expected.variant_name
        ),
        None,
    )
    assert isinstance(persisted, dict), campaign
    assert persisted.get("channel") == "email"
    assert persisted.get("subject") == expected.subject
    assert persisted.get("body") == expected.body
    assert persisted.get("generation_mode") == recommendation.generation_mode
    assert persisted.get("generator_label") == recommendation.generator_label
    assert persisted.get("copy_verified_at_creation") is True
    _approve_campaign_for_outreach(campaign_id)
    return campaign_id, expected.variant_name, "email", candidate_borrower_ids


def _approve_campaign_for_outreach(campaign_id: str) -> None:
    """Advance an approval fixture through the public governed lifecycle."""

    approve_campaign_for_outreach(
        campaign_id,
        request=_request,
        approver_token=ADMIN_TOKEN,
    )


def _campaign_treatment_member_draft(
    *,
    campaign_id: str,
    variant_name: str,
    channel: str,
    candidate_borrower_ids: list[str],
) -> tuple[str, dict[str, object]]:
    """Select through the public authorization gate, never a broad member export."""

    rejected: list[object] = []
    for borrower_id in candidate_borrower_ids:
        draft_status, draft = _request(
            "POST",
            "/api/outreach/draft",
            {
                "borrower_id": borrower_id,
                "campaign_id": campaign_id,
                "variant_name": variant_name,
                "channel": channel,
            },
        )
        if draft_status == 200 and isinstance(draft, dict):
            return borrower_id, draft
        rejected.append(draft)
    pytest.fail(
        "No lead passed the campaign's exact T0 treatment and current-eligibility gate; "
        f"sample responses={rejected[:3]!r}"
    )


def _approval_payload(draft: dict[str, object], *, request_id: str) -> dict[str, object]:
    return {
        "borrower_id": _required_string(draft, "borrower_id"),
        "offer_code": _required_string(draft, "offer_code"),
        "campaign_id": _required_string(draft, "campaign_id"),
        "variant_name": _required_string(draft, "variant_name"),
        "channel": _required_string(draft, "channel"),
        "draft_subject": _required_string(draft, "subject"),
        "draft_body": _required_string(draft, "body"),
        "draft_generation_id": _required_string(draft, "generation_id"),
        "draft_response_hash": _required_string(draft, "response_hash"),
        "draft_source_refreshed_at": _required_string(draft, "source_refreshed_at"),
        "request_id": request_id,
    }


def _approve_and_assign(borrower_id: str) -> None:
    draft_status, draft = _request(
        "POST",
        "/api/outreach/draft",
        {"borrower_id": borrower_id, "channel": "email"},
    )
    assert draft_status == 200
    assert isinstance(draft, dict)
    generation_id = _required_string(draft, "generation_id")
    approve_status, _approve = _request(
        "POST",
        "/api/outreach/approve",
        {
            "borrower_id": borrower_id,
            "offer_code": _required_string(draft, "offer_code"),
            "channel": _required_string(draft, "channel"),
            "draft_subject": _required_string(draft, "subject"),
            "draft_body": _required_string(draft, "body"),
            "draft_generation_id": generation_id,
            "draft_response_hash": _required_string(draft, "response_hash"),
            "draft_source_refreshed_at": _required_string(draft, "source_refreshed_at"),
            "request_id": str(uuid4()),
        },
    )
    assert approve_status == 200, _approve
    assert isinstance(_approve, dict)
    assert _approve.get("draft_generation_id") == generation_id
    assign_status, _assignment = _request(
        "POST",
        f"/api/leads/{borrower_id}/assign",
        {
            "assigned_to_email": "lo01@summit.example",
            "strategy": "manual",
            "request_id": str(uuid4()),
        },
    )
    assert assign_status in {200, 409}


def _assert_lakebase_healthy() -> None:
    status, health = _request("GET", "/api/health")
    assert status == 200
    assert isinstance(health, dict)
    dependencies = health.get("dependencies")
    assert isinstance(dependencies, dict)
    assert dependencies.get("lakebase") == "up"
    breakers = health.get("circuit_breakers")
    assert isinstance(breakers, dict)
    assert breakers.get("lakebase") == "closed"


def _assert_dev_mutation_target() -> None:
    status, health = _request("GET", "/api/admin/health", token=ADMIN_TOKEN)
    assert status == 200
    assert isinstance(health, dict)
    app_env = health.get("app_env")
    assert app_env in {"dev", "sandbox"}, (
        "Live Lakebase idempotency test mutates campaigns, drafts, approvals, assignments, outcomes, "
        f"and dispositions; refusing non-dev/sandbox app_env={app_env!r}"
    )


def test_live_generated_draft_approval_binding_and_replay_without_breaker_trip() -> None:
    _assert_dev_mutation_target()
    _assert_lakebase_healthy()
    campaign_id, variant_name, channel, candidate_borrower_ids = _create_email_campaign_variant()
    borrower_id, draft = _campaign_treatment_member_draft(
        campaign_id=campaign_id,
        variant_name=variant_name,
        channel=channel,
        candidate_borrower_ids=candidate_borrower_ids,
    )
    assert draft.get("borrower_id") == borrower_id
    assert draft.get("campaign_id") == campaign_id
    assert draft.get("variant_name") == variant_name
    assert draft.get("channel") == channel
    _required_string(draft, "body")
    generation_id = _required_string(draft, "generation_id")
    assert len(_required_string(draft, "response_hash")) == 64

    approval_payload = _approval_payload(draft, request_id=str(uuid4()))
    status, first = _request("POST", "/api/outreach/approve", approval_payload)
    assert status == 200, first
    assert isinstance(first, dict)
    assert first.get("approved") is True
    approval_id = _required_string(first, "approval_id")
    audit_event_id = _required_string(first, "audit_event_id")
    assert first.get("draft_generation_id") == generation_id
    _assert_lakebase_healthy()

    status, replay = _request("POST", "/api/outreach/approve", approval_payload)
    assert status == 200, replay
    assert isinstance(replay, dict)
    assert replay == first
    assert replay.get("approval_id") == approval_id
    assert replay.get("audit_event_id") == audit_event_id
    assert replay.get("draft_generation_id") == generation_id
    _assert_lakebase_healthy()

    mismatches = (
        {"draft_generation_id": str(uuid4())},
        {"campaign_id": str(uuid4())},
        {"variant_name": "Mismatched proof"},
        {"channel": "direct_mail"},
    )
    for updates in mismatches:
        status, mismatch = _request(
            "POST",
            "/api/outreach/approve",
            {**approval_payload, **updates},
        )
        assert status == 409, (updates, mismatch)
        assert isinstance(mismatch, dict)
        assert mismatch.get("detail") == (
            "request_id already belongs to a different outreach decision"
        )
        _assert_lakebase_healthy()


def test_live_saved_holdout_and_household_dedup_exclude_non_treatment_borrowers() -> None:
    """Prove the saved T0 boundary through UC evidence and the public draft gate."""

    _assert_dev_mutation_target()
    _assert_lakebase_healthy()
    campaign_id, variant_name, channel, _candidate_borrower_ids = (
        _create_email_campaign_variant()
    )
    status, campaign = _request("GET", f"/api/campaigns/{campaign_id}")
    assert status == 200, campaign
    assert isinstance(campaign, dict)
    criteria = campaign.get("criteria")
    holdout = campaign.get("holdout")
    household_dedup = campaign.get("household_dedup")
    household_summary = campaign.get("household_summary")
    assert isinstance(criteria, dict)
    assert isinstance(holdout, dict)
    assert holdout.get("method") == "hash_modulo"
    assert 5 <= float(holdout.get("size_pct") or 0) <= 30
    assert isinstance(household_dedup, dict)
    assert household_dedup == {
        "enabled": True,
        "dedupe_unit": "household",
        "primary_contact_strategy": "highest_opportunity_eligible",
    }
    assert isinstance(household_summary, dict)
    assert int(household_summary.get("suppressed_co_owner_count") or 0) > 0

    treatment_id, holdout_id, co_owner_id = _campaign_assignment_examples(
        campaign_id=campaign_id,
        criteria=criteria,
    )

    def draft_for(borrower_id: str) -> tuple[int, object]:
        return _request(
            "POST",
            "/api/outreach/draft",
            {
                "borrower_id": borrower_id,
                "campaign_id": campaign_id,
                "variant_name": variant_name,
                "channel": channel,
            },
        )

    treatment_status, treatment_draft = draft_for(treatment_id)
    assert treatment_status == 200, treatment_draft
    assert isinstance(treatment_draft, dict)
    assert treatment_draft.get("borrower_id") == treatment_id

    for excluded_kind, borrower_id in (
        ("holdout", holdout_id),
        ("household co-owner", co_owner_id),
    ):
        excluded_status, excluded = draft_for(borrower_id)
        assert excluded_status == 409, (excluded_kind, excluded)
        assert isinstance(excluded, dict)
        assert excluded.get("detail") == "Borrower is not in the saved campaign cohort."
    _assert_lakebase_healthy()


def test_live_duplicate_outcome_and_disposition_replay_without_breaker_trip() -> None:
    _assert_dev_mutation_target()
    borrower_id = _first_borrower_id()
    _approve_and_assign(borrower_id)

    outcome_request_id = str(uuid4())
    source_record_ref = f"live-idem-{uuid4().hex[:12]}"
    outcome_payload = {
        "outcome_type": "closed_funded",
        "source_system": "manual_import",
        "source_record_ref": source_record_ref,
        "assigned_to_email": "lo01@summit.example",
        "loan_amount": 425000,
        "request_id": outcome_request_id,
    }
    status, first = _request("POST", f"/api/leads/{borrower_id}/outcome", outcome_payload)
    assert status == 200
    status, replay = _request("POST", f"/api/leads/{borrower_id}/outcome", outcome_payload)
    assert status == 200
    assert isinstance(first, dict) and isinstance(replay, dict)
    assert first["outcome"]["outcome_id"] == replay["outcome"]["outcome_id"]
    _assert_lakebase_healthy()

    mismatched_outcome = dict(outcome_payload)
    mismatched_outcome["loan_amount"] = 426000
    status, mismatch = _request("POST", f"/api/leads/{borrower_id}/outcome", mismatched_outcome)
    assert status == 409
    assert "request_id already belongs to a different lead outcome" in str(mismatch)
    _assert_lakebase_healthy()

    disposition_request_id = str(uuid4())
    disposition_payload = {
        "lo_email": "lo01@summit.example",
        "outcome": "connected",
        "request_id": disposition_request_id,
    }
    status, first_disposition = _request(
        "POST",
        f"/api/leads/{borrower_id}/disposition",
        disposition_payload,
    )
    assert status == 200
    status, replay_disposition = _request(
        "POST",
        f"/api/leads/{borrower_id}/disposition",
        disposition_payload,
    )
    assert status == 200
    assert isinstance(first_disposition, dict) and isinstance(replay_disposition, dict)
    assert (
        first_disposition["disposition"]["disposition_id"]
        == replay_disposition["disposition"]["disposition_id"]
    )
    _assert_lakebase_healthy()

    mismatched_disposition = dict(disposition_payload)
    mismatched_disposition["outcome"] = "called_left_voicemail"
    status, mismatch_disposition = _request(
        "POST",
        f"/api/leads/{borrower_id}/disposition",
        mismatched_disposition,
    )
    assert status == 409
    assert "request_id already belongs to a different call disposition" in str(mismatch_disposition)
    _assert_lakebase_healthy()
