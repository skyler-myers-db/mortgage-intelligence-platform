"""Live concurrent replay/conflict checks against deployed Lakebase state."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from backend.schemas.portfolio import (
    CampaignRecommendationResponse,
    PortfolioCreateRequest,
)
from tests.fixtures.live_campaign_lifecycle import approve_campaign_for_outreach
from tests.integration.live_campaign_cleanup import CampaignFixtureTracker
from tools.cleanup_live_campaign_fixtures import run_scoped_campaign_name

APP_URL = (os.environ.get("MIP_APP_URL") or "").rstrip("/")
TOKEN = os.environ.get("MIP_BEARER_TOKEN") or ""
ADMIN_TOKEN = os.environ.get("MIP_ADMIN_BEARER_TOKEN") or ""
LIVE_MUTATION_OK = os.environ.get("MIP_LIVE_MUTATION_OK") == "1"
_WORKERS = 8

pytestmark = pytest.mark.skipif(
    os.environ.get("LAKEBASE_INTEGRATION") != "1"
    or not APP_URL
    or not TOKEN
    or not ADMIN_TOKEN
    or not LIVE_MUTATION_OK,
    reason=(
        "Set LAKEBASE_INTEGRATION=1, MIP_APP_URL, MIP_BEARER_TOKEN, "
        "MIP_ADMIN_BEARER_TOKEN, and MIP_LIVE_MUTATION_OK=1"
    ),
)


def _request(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    token: str = TOKEN,
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id
    request = urllib.request.Request(
        f"{APP_URL}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
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


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    assert isinstance(value, str) and value, f"missing {field}: {payload!r}"
    return value


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


def _bounded_criteria_and_candidates() -> tuple[dict[str, object], list[str]]:
    for state in ("IL", "CA", "FL", "WA"):
        criteria: dict[str, object] = {
            "states": [state],
            "min_equity_pct": 99.9,
            "occupancy": "Owner-occupied",
            "recency": "Untouched 30d",
        }
        status, preview = _request(
            "POST",
            "/api/portfolio/preview",
            {"criteria": criteria, "campaign_build_config": {}},
        )
        if (
            status != 200
            or not isinstance(preview, dict)
            or preview.get("campaign_build_eligible") is not True
            or not isinstance(preview.get("campaign_build_contact_count"), int)
            or int(preview["campaign_build_contact_count"]) <= 0
        ):
            continue
        query = urllib.parse.urlencode(
            {
                "states": state,
                "min_equity_pct": "99.9",
                "occupancy": "Owner-occupied",
                "recency": "Untouched 30d",
                "limit": "100",
            }
        )
        status, leads = _request("GET", f"/api/leads?{query}")
        if status != 200 or not isinstance(leads, list):
            continue
        borrower_ids = [
            str(lead["borrower_id"])
            for lead in leads
            if isinstance(lead, dict) and isinstance(lead.get("borrower_id"), str)
        ]
        if borrower_ids:
            return criteria, borrower_ids
    pytest.fail("No nonempty campaign cohort fits the governed synchronous build limit")


def _create_campaign() -> tuple[str, str, str, list[str]]:
    criteria, borrower_ids = _bounded_criteria_and_candidates()
    recommendation_status, raw_recommendation = _request(
        "POST",
        "/api/portfolio/campaign-recommendation",
        {"criteria": criteria},
    )
    assert recommendation_status == 200, raw_recommendation
    assert isinstance(raw_recommendation, dict), raw_recommendation
    payload, recommendation = _reviewed_campaign_create_payload(
        name=run_scoped_campaign_name("Live Lakebase concurrency contract"),
        criteria=criteria,
        raw_recommendation=raw_recommendation,
    )
    status, created = _request(
        "POST",
        "/api/portfolio/create",
        payload,
        idempotency_key=f"live-concurrency-campaign-{uuid4()}",
    )
    assert status == 200, created
    assert isinstance(created, dict)
    campaign_id = _required_string(created, "campaign_id")

    status, campaign = _request("GET", f"/api/campaigns/{campaign_id}")
    assert status == 200, campaign
    assert isinstance(campaign, dict), campaign
    persisted_variants = campaign.get("message_variants")
    assert isinstance(persisted_variants, list), campaign
    expected = recommendation.variants[0]
    persisted = next(
        (
            variant
            for variant in persisted_variants
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
    return campaign_id, expected.variant_name, "email", borrower_ids


def _approve_campaign_for_outreach(campaign_id: str) -> None:
    """Advance an approval fixture through the public governed lifecycle."""

    approve_campaign_for_outreach(
        campaign_id,
        request=_request,
        approver_token=ADMIN_TOKEN,
    )


def _campaign_draft() -> tuple[str, dict[str, object]]:
    campaign_id, variant_name, channel, borrower_ids = _create_campaign()
    _approve_campaign_for_outreach(campaign_id)
    rejected: list[object] = []
    for borrower_id in borrower_ids:
        status, draft = _request(
            "POST",
            "/api/outreach/draft",
            {
                "borrower_id": borrower_id,
                "campaign_id": campaign_id,
                "variant_name": variant_name,
                "channel": channel,
            },
        )
        if status == 200 and isinstance(draft, dict):
            return campaign_id, draft
        rejected.append(draft)
    pytest.fail(f"No bounded-cohort borrower passed the exact T0 gate: {rejected[:3]!r}")


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


def _race_requests(
    requests: list[tuple[str, str, dict[str, object], str | None]],
    *,
    token: str = TOKEN,
) -> list[tuple[int, object]]:
    barrier = threading.Barrier(len(requests))

    def submit(item: tuple[str, str, dict[str, object], str | None]) -> tuple[int, object]:
        method, path, payload, correlation_id = item
        barrier.wait(timeout=30)
        return _request(
            method,
            path,
            payload,
            token=token,
            correlation_id=correlation_id,
        )

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        return list(executor.map(submit, requests))


def _assert_dev_target() -> None:
    status, health = _request("GET", "/api/admin/health", token=ADMIN_TOKEN)
    assert status == 200, health
    assert isinstance(health, dict)
    assert health.get("app_env") in {"dev", "sandbox"}


def test_concurrent_identical_outreach_approval_is_one_durable_decision() -> None:
    _assert_dev_target()
    _campaign_id, draft = _campaign_draft()
    payload = _approval_payload(draft, request_id=str(uuid4()))
    results = _race_requests([("POST", "/api/outreach/approve", payload, None)] * _WORKERS)

    assert {status for status, _body in results} == {200}, results
    bodies = [body for _status, body in results]
    assert all(isinstance(body, dict) for body in bodies)
    assert all(body == bodies[0] for body in bodies[1:])
    first = bodies[0]
    assert isinstance(first, dict)
    approval_id = _required_string(first, "approval_id")
    audit_id = _required_string(first, "audit_event_id")
    status, audits = _request(
        "GET",
        "/api/audit/events?" + urllib.parse.urlencode({"entity_id": approval_id}),
        token=ADMIN_TOKEN,
    )
    assert status == 200, audits
    assert isinstance(audits, list)
    assert [event.get("event_id") for event in audits if isinstance(event, dict)] == [audit_id]


def test_concurrent_payload_conflict_has_one_winner_and_no_lost_update() -> None:
    _assert_dev_target()
    _campaign_id, draft = _campaign_draft()
    request_id = str(uuid4())
    first = _approval_payload(draft, request_id=request_id)
    second = {**first, "rationale": "Alternative governed rationale."}
    results = _race_requests(
        [
            (
                "POST",
                "/api/outreach/approve",
                first if index < _WORKERS // 2 else second,
                None,
            )
            for index in range(_WORKERS)
        ]
    )

    statuses = [status for status, _body in results]
    assert statuses.count(200) == _WORKERS // 2, results
    assert statuses.count(409) == _WORKERS // 2, results
    winning_bodies = [body for status, body in results if status == 200]
    assert all(body == winning_bodies[0] for body in winning_bodies[1:])
    for status, body in results:
        if status == 409:
            assert isinstance(body, dict)
            assert body.get("detail") == (
                "request_id already belongs to a different outreach decision"
            )


def test_concurrent_identical_campaign_transition_is_one_audited_replay() -> None:
    _assert_dev_target()
    campaign_id, _variant, _channel, _borrowers = _create_campaign()
    correlation_id = f"campaign-concurrency-{uuid4()}"
    payload: dict[str, object] = {"status": "archived", "rationale": "Live replay proof."}
    results = _race_requests(
        [("PATCH", f"/api/campaigns/{campaign_id}", payload, correlation_id)] * _WORKERS
    )

    assert {status for status, _body in results} == {200}, results
    bodies = [body for _status, body in results]
    assert all(body == bodies[0] for body in bodies[1:])
    status, audits = _request(
        "GET",
        "/api/audit/events?"
        + urllib.parse.urlencode(
            {"entity_id": campaign_id, "correlation_id": correlation_id, "limit": 10}
        ),
        token=ADMIN_TOKEN,
    )
    assert status == 200, audits
    assert isinstance(audits, list)
    assert len(audits) == 1, audits


def test_same_correlation_competing_campaign_payload_has_one_idempotency_winner() -> None:
    _assert_dev_target()
    campaign_id, _variant, _channel, _borrowers = _create_campaign()
    correlation_id = f"campaign-conflict-{uuid4()}"
    pending: dict[str, object] = {
        "status": "pending_review",
        "expected_status": "draft",
        "rationale": "Live review queue proof.",
    }
    archived: dict[str, object] = {
        "status": "archived",
        "expected_status": "draft",
        "rationale": "Live archive conflict proof.",
    }
    results = _race_requests(
        [
            (
                "PATCH",
                f"/api/campaigns/{campaign_id}",
                pending if index < _WORKERS // 2 else archived,
                correlation_id,
            )
            for index in range(_WORKERS)
        ]
    )

    statuses = [status for status, _body in results]
    assert statuses.count(200) == _WORKERS // 2, results
    assert statuses.count(409) == _WORKERS // 2, results
    status, audits = _request(
        "GET",
        "/api/audit/events?"
        + urllib.parse.urlencode(
            {"entity_id": campaign_id, "correlation_id": correlation_id, "limit": 10}
        ),
        token=ADMIN_TOKEN,
    )
    assert status == 200, audits
    assert isinstance(audits, list)
    assert len(audits) == 1, audits


def test_independent_campaign_transitions_use_cas_without_lost_update() -> None:
    _assert_dev_target()
    campaign_id, _variant, _channel, _borrowers = _create_campaign()

    status, pending = _request(
        "PATCH",
        f"/api/campaigns/{campaign_id}",
        {
            "status": "pending_review",
            "expected_status": "draft",
            "rationale": "Prepare the live CAS proof.",
        },
        correlation_id=f"campaign-cas-prepare-{uuid4()}",
    )
    assert status == 200, pending
    status, approved = _request(
        "PATCH",
        f"/api/campaigns/{campaign_id}",
        {
            "status": "approved",
            "expected_status": "pending_review",
            "rationale": "Approve the isolated live CAS proof.",
        },
        token=ADMIN_TOKEN,
        correlation_id=f"campaign-cas-approve-{uuid4()}",
    )
    assert status == 200, approved

    race_ids = [f"campaign-independent-cas-{uuid4()}" for _index in range(_WORKERS)]
    live: dict[str, object] = {
        "status": "live",
        "expected_status": "approved",
        "rationale": "Independent live transition proof.",
    }
    rejected: dict[str, object] = {
        "status": "rejected",
        "expected_status": "approved",
        "rationale": "Independent rejection transition proof.",
    }
    results = _race_requests(
        [
            (
                "PATCH",
                f"/api/campaigns/{campaign_id}",
                live if index < _WORKERS // 2 else rejected,
                race_ids[index],
            )
            for index in range(_WORKERS)
        ],
        token=ADMIN_TOKEN,
    )

    statuses = [status for status, _body in results]
    assert statuses.count(200) == 1, results
    assert statuses.count(409) == _WORKERS - 1, results
    winning_body = next(body for status, body in results if status == 200)
    assert isinstance(winning_body, dict)
    winning_status = winning_body.get("status")
    assert winning_status in {"live", "rejected"}

    status, final_campaign = _request(
        "GET",
        f"/api/campaigns/{campaign_id}",
        token=ADMIN_TOKEN,
    )
    assert status == 200, final_campaign
    assert isinstance(final_campaign, dict)
    assert final_campaign.get("status") == winning_status

    status, audits = _request(
        "GET",
        "/api/audit/events?" + urllib.parse.urlencode({"entity_id": campaign_id, "limit": 50}),
        token=ADMIN_TOKEN,
    )
    assert status == 200, audits
    assert isinstance(audits, list)
    race_audits = [
        event
        for event in audits
        if isinstance(event, dict) and event.get("correlation_id") in set(race_ids)
    ]
    assert len(race_audits) == 1, race_audits
