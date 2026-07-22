"""Live campaign creation and audit-discovery contract.

Skipped unless explicitly enabled against a dev/sandbox deployment. This test
proves the real Lakebase transaction, idempotent replay, Admin audit filters,
and circuit-breaker behavior that in-memory repositories cannot establish.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

import pytest

from tests.integration.live_campaign_cleanup import CampaignFixtureTracker
from tools.cleanup_live_campaign_fixtures import run_scoped_campaign_name

APP_URL = (os.environ.get("MIP_APP_URL") or "").rstrip("/")
TOKEN = os.environ.get("MIP_BEARER_TOKEN") or os.environ.get("DATABRICKS_TOKEN") or ""
ADMIN_TOKEN = os.environ.get("MIP_ADMIN_BEARER_TOKEN") or TOKEN
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
    token: str = TOKEN,
    idempotency_key: str | None = None,
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


def _campaign_conflict_probe(*args: object, **kwargs: object) -> tuple[int, object]:
    raise RuntimeError("live campaign conflict probe is not initialized")


@pytest.fixture(autouse=True)
def _archive_created_campaigns(monkeypatch: pytest.MonkeyPatch) -> object:
    original_request = _request
    tracker = CampaignFixtureTracker(default_token=TOKEN)

    def tracked_request(*args: object, **kwargs: object) -> tuple[int, object]:
        return tracker.request(original_request, *args, **kwargs)

    def conflict_probe(*args: object, **kwargs: object) -> tuple[int, object]:
        return tracker.conflict_probe(original_request, *args, **kwargs)

    monkeypatch.setattr(__name__ + "._request", tracked_request)
    monkeypatch.setattr(__name__ + "._campaign_conflict_probe", conflict_probe)
    yield
    tracker.cleanup(original_request, admin_token=ADMIN_TOKEN)


def _assert_dev_mutation_target() -> None:
    status, health = _request("GET", "/api/admin/health", token=ADMIN_TOKEN)
    assert status == 200
    assert isinstance(health, dict)
    assert health.get("app_env") in {"dev", "sandbox"}, (
        "Live campaign audit test creates a draft campaign; refusing "
        f"app_env={health.get('app_env')!r}"
    )


def _assert_lakebase_breaker_closed() -> None:
    status, health = _request("GET", "/api/health")
    assert status == 200
    assert isinstance(health, dict)
    breakers = health.get("circuit_breakers")
    assert isinstance(breakers, dict)
    assert breakers.get("lakebase") == "closed"


def _tiny_reviewed_campaign_criteria() -> dict[str, object]:
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
            "&recency=Untouched%2030d&limit=1",
        )
        if status == 200 and isinstance(leads, list) and leads:
            return criteria
    pytest.fail("No lead exists for the bounded live campaign audit fixture")


def test_live_campaign_replay_and_admin_audit_filters_agree() -> None:
    _assert_dev_mutation_target()
    idempotency_key = f"live-campaign-{uuid4()}"
    criteria = _tiny_reviewed_campaign_criteria()
    status, recommendation = _request(
        "POST",
        "/api/portfolio/campaign-recommendation",
        {"criteria": criteria},
    )
    assert status == 200
    assert isinstance(recommendation, dict)
    recommended_variants = recommendation.get("variants")
    assert isinstance(recommended_variants, list) and len(recommended_variants) == 2
    generation_mode = recommendation.get("generation_mode")
    generator_label = recommendation.get("generator_label")
    payload: dict[str, object] = {
        "name": run_scoped_campaign_name("Live campaign audit contract"),
        "criteria": criteria,
        "suppression_policy": {"marketing_eligibility": "Eligible only"},
        "message_variants": [
            {
                "variant_name": str(variant["variant_name"]),
                "channel": "email",
                "subject": variant["subject"],
                "body": variant["body"],
                "weight_pct": 50,
                "generation_mode": generation_mode,
                "generator_label": generator_label,
                "provenance_token": variant["provenance_token"],
            }
            for variant in recommended_variants
        ],
    }

    status, created = _request(
        "POST",
        "/api/portfolio/create",
        payload,
        idempotency_key=idempotency_key,
    )
    assert status == 200
    assert isinstance(created, dict)
    campaign_id = created.get("campaign_id")
    audit_event_id = created.get("audit_event_id")
    assert isinstance(campaign_id, str) and campaign_id
    assert isinstance(audit_event_id, str) and audit_event_id

    status, replay = _request(
        "POST",
        "/api/portfolio/create",
        payload,
        idempotency_key=idempotency_key,
    )
    assert status == 200
    assert isinstance(replay, dict)
    assert replay.get("campaign_id") == campaign_id
    assert replay.get("audit_event_id") == audit_event_id

    conflicting_payload = dict(payload)
    conflicting_payload["name"] = run_scoped_campaign_name(
        "Conflicting live campaign payload"
    )
    status, conflict = _campaign_conflict_probe(
        "POST",
        "/api/portfolio/create",
        conflicting_payload,
        idempotency_key=idempotency_key,
    )
    assert status == 409
    assert "different campaign payload" in str(conflict)
    _assert_lakebase_breaker_closed()

    query = urllib.parse.urlencode(
        {
            "limit": 25,
            "action": "portfolio.create",
            "event_type": "PORTFOLIO_CREATE",
            "entity_id": campaign_id,
        }
    )
    status, events = _request(
        "GET",
        f"/api/audit/events?{query}",
        token=ADMIN_TOKEN,
    )
    assert status == 200
    assert isinstance(events, list)
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, dict)
    assert event.get("event_id") == audit_event_id
    assert event.get("entity_type") == "campaign"
    assert event.get("entity_id") == campaign_id
    assert event.get("action") == "portfolio.create"
    assert event.get("event_type") == "PORTFOLIO_CREATE"
    assert event.get("request_id") == idempotency_key
    event_payload = event.get("payload_json") or {}
    assert "variant_provenance" in event_payload
    variant_provenance = event_payload["variant_provenance"]
    assert isinstance(variant_provenance, list) and len(variant_provenance) == 2
    for proof in variant_provenance:
        assert proof["provenance_key_id"]
        assert len(proof["provenance_copy_hash"]) == 64
        assert len(proof["provenance_criteria_fingerprint"]) == 64
