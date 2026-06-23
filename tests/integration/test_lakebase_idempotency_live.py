"""Live closed-loop idempotency checks against the deployed app.

Skipped unless ``LAKEBASE_INTEGRATION=1`` plus ``MIP_APP_URL`` and a bearer
token are present. This intentionally exercises the app API backed by real
Lakebase constraints; the in-memory fake remains useful for fast unit tests
but cannot prove production ``ON CONFLICT`` / partial-index behavior.
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


def _request(method: str, path: str, payload: dict[str, object] | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{APP_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310
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


def _approve_and_assign(borrower_id: str) -> None:
    draft_status, draft = _request(
        "POST",
        "/api/outreach/draft",
        {"borrower_id": borrower_id, "channel": "email"},
    )
    assert draft_status == 200
    assert isinstance(draft, dict)
    body = str(draft.get("body") or "")
    approve_status, _approve = _request(
        "POST",
        "/api/outreach/approve",
        {
            "borrower_id": borrower_id,
            "offer_code": "refi_plus_heloc",
            "channel": "email",
            "draft_body": body,
            "request_id": str(uuid4()),
        },
    )
    assert approve_status in {200, 409}
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


def _assert_lakebase_breaker_closed() -> None:
    status, health = _request("GET", "/api/health")
    assert status == 200
    assert isinstance(health, dict)
    breakers = health.get("circuit_breakers")
    assert isinstance(breakers, dict)
    assert breakers.get("lakebase") == "closed"


def _assert_dev_mutation_target() -> None:
    status, health = _request("GET", "/api/admin/health")
    assert status == 200
    assert isinstance(health, dict)
    app_env = health.get("app_env")
    assert app_env in {"dev", "sandbox"}, (
        "Live Lakebase idempotency test mutates approvals, assignments, outcomes, "
        f"and dispositions; refusing non-dev/sandbox app_env={app_env!r}"
    )


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
    _assert_lakebase_breaker_closed()

    mismatched_outcome = dict(outcome_payload)
    mismatched_outcome["loan_amount"] = 426000
    status, mismatch = _request("POST", f"/api/leads/{borrower_id}/outcome", mismatched_outcome)
    assert status == 409
    assert "request_id already belongs to a different lead outcome" in str(mismatch)
    _assert_lakebase_breaker_closed()

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
    _assert_lakebase_breaker_closed()

    mismatched_disposition = dict(disposition_payload)
    mismatched_disposition["outcome"] = "called_left_voicemail"
    status, mismatch_disposition = _request(
        "POST",
        f"/api/leads/{borrower_id}/disposition",
        mismatched_disposition,
    )
    assert status == 409
    assert "request_id already belongs to a different call disposition" in str(mismatch_disposition)
    _assert_lakebase_breaker_closed()
