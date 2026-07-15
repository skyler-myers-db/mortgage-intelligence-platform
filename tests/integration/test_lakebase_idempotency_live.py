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

APP_URL = (os.environ.get("MIP_APP_URL") or "").rstrip("/")
TOKEN = os.environ.get("MIP_BEARER_TOKEN") or os.environ.get("DATABRICKS_TOKEN") or ""
LIVE_MUTATION_OK = os.environ.get("MIP_LIVE_MUTATION_OK") == "1"

pytestmark = pytest.mark.skipif(
    os.environ.get("LAKEBASE_INTEGRATION") != "1"
    or not APP_URL
    or not TOKEN
    or not LIVE_MUTATION_OK,
    reason=(
        "Set LAKEBASE_INTEGRATION=1, MIP_APP_URL, MIP_BEARER_TOKEN/DATABRICKS_TOKEN, "
        "and MIP_LIVE_MUTATION_OK=1 for the dev app"
    ),
)


def _request(
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    idempotency_key: str | None = None,
) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
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


def _create_email_campaign_variant() -> tuple[str, str, str]:
    variant_name = "Approval proof"
    channel = "email"
    subject = "Review your mortgage options"
    body = "Reply to review your mortgage options with our team."
    status, created = _request(
        "POST",
        "/api/portfolio/create",
        {
            "name": "Live Lakebase approval contract",
            "criteria": {},
            "message_variants": [
                {
                    "variant_name": variant_name,
                    "channel": channel,
                    "subject": subject,
                    "body": body,
                    "weight_pct": 100,
                    "generation_mode": "operator",
                }
            ],
        },
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
    persisted = next(
        (
            variant
            for variant in variants
            if isinstance(variant, dict) and variant.get("variant_name") == variant_name
        ),
        None,
    )
    assert isinstance(persisted, dict), campaign
    assert persisted.get("channel") == channel
    assert persisted.get("subject") == subject
    assert persisted.get("body") == body
    return campaign_id, variant_name, channel


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
    status, health = _request("GET", "/api/admin/health")
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
    borrower_id = _first_borrower_id()
    campaign_id, variant_name, channel = _create_email_campaign_variant()

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
    assert status == 200, draft
    assert isinstance(draft, dict)
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
