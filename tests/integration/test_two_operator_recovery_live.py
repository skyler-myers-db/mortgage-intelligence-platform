"""Live per-operator recovery isolation with two non-admin M2M principals.

This is deliberately an on-demand mutation test. Each operator prepares a
two-item bulk approval, commits only the first item (the interruption point),
and then reloads the PII-minimized recovery feed. The operators must see their
own committed approval only, while the separate admin identity can inspect
both exact audit rows.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

import pytest

APP_URL = (os.environ.get("MIP_APP_URL") or "").rstrip("/")
OPERATOR_A_TOKEN = (
    os.environ.get("MIP_NON_ADMIN_BEARER_TOKEN")
    or os.environ.get("MIP_BEARER_TOKEN")
    or ""
)
OPERATOR_B_TOKEN = os.environ.get("MIP_OPERATOR2_BEARER_TOKEN") or ""
ADMIN_TOKEN = os.environ.get("MIP_ADMIN_BEARER_TOKEN") or ""
LIVE_MUTATION_OK = os.environ.get("MIP_LIVE_MUTATION_OK") == "1"

pytestmark = pytest.mark.skipif(
    os.environ.get("LAKEBASE_INTEGRATION") != "1"
    or not APP_URL
    or not OPERATOR_A_TOKEN
    or not OPERATOR_B_TOKEN
    or not ADMIN_TOKEN
    or not LIVE_MUTATION_OK,
    reason=(
        "Set LAKEBASE_INTEGRATION=1, MIP_APP_URL, both non-admin bearer tokens, "
        "MIP_ADMIN_BEARER_TOKEN, and MIP_LIVE_MUTATION_OK=1"
    ),
)


def _request(
    method: str,
    path: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{APP_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed: object = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed


def _required_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    assert isinstance(value, str) and value, f"missing {field}: {payload!r}"
    return value


def _drafts_for_operator(
    token: str,
    *,
    excluded: set[str],
) -> list[dict[str, object]]:
    status, leads = _request("GET", "/api/leads?limit=50", token=token)
    assert status == 200, leads
    assert isinstance(leads, list)
    drafts: list[dict[str, object]] = []
    for lead in leads:
        if not isinstance(lead, dict):
            continue
        borrower_id = lead.get("borrower_id")
        if not isinstance(borrower_id, str) or borrower_id in excluded:
            continue
        status, draft = _request(
            "POST",
            "/api/outreach/draft",
            token=token,
            payload={"borrower_id": borrower_id, "channel": "email"},
        )
        if status == 200 and isinstance(draft, dict):
            drafts.append(draft)
            excluded.add(borrower_id)
            if len(drafts) == 2:
                return drafts
    pytest.fail("Could not prepare two governed outreach drafts for a live operator")


def _approve_first_bulk_item(
    token: str,
    drafts: list[dict[str, object]],
    *,
    label: str,
) -> dict[str, object]:
    first = drafts[0]
    payload: dict[str, object] = {
        "borrower_id": _required_string(first, "borrower_id"),
        "offer_code": _required_string(first, "offer_code"),
        "channel": _required_string(first, "channel"),
        "draft_subject": _required_string(first, "subject"),
        "draft_body": _required_string(first, "body"),
        "draft_generation_id": _required_string(first, "generation_id"),
        "draft_response_hash": _required_string(first, "response_hash"),
        "draft_source_refreshed_at": _required_string(first, "source_refreshed_at"),
        "bulk_id": f"{label}-bulk-{uuid4().hex[:12]}",
        "bulk_rationale": "Live interrupted-bulk recovery proof.",
        "request_id": str(uuid4()),
    }
    status, approved = _request(
        "POST",
        "/api/outreach/approve",
        token=token,
        payload=payload,
    )
    assert status == 200, approved
    assert isinstance(approved, dict)
    assert approved.get("approved") is True
    return approved


def _my_activity(token: str) -> set[tuple[str, str | None, str]]:
    status, page = _request("GET", "/api/audit/my-events?limit=50", token=token)
    assert status == 200, page
    assert isinstance(page, dict)
    items = page.get("items")
    assert isinstance(items, list)
    return {
        (
            str(item.get("event_type") or ""),
            str(item["subject_id"]) if isinstance(item.get("subject_id"), str) else None,
            str(item.get("created_at") or ""),
        )
        for item in items
        if isinstance(item, dict)
    }


def _admin_exact_audit_event(approval: dict[str, object]) -> dict[str, object]:
    approval_id = _required_string(approval, "approval_id")
    audit_event_id = _required_string(approval, "audit_event_id")
    path = "/api/audit/events?" + urllib.parse.urlencode(
        {"entity_id": approval_id, "limit": 10}
    )
    status, events = _request("GET", path, token=ADMIN_TOKEN)
    assert status == 200, events
    assert isinstance(events, list)
    exact = [
        event
        for event in events
        if isinstance(event, dict) and event.get("event_id") == audit_event_id
    ]
    assert len(exact) == 1, events
    return exact[0]


def test_interrupted_bulk_recovery_is_isolated_between_two_real_operators() -> None:
    status, health = _request("GET", "/api/admin/health", token=ADMIN_TOKEN)
    assert status == 200, health
    assert isinstance(health, dict)
    assert health.get("app_env") in {"dev", "sandbox"}

    excluded: set[str] = set()
    operator_a_drafts = _drafts_for_operator(OPERATOR_A_TOKEN, excluded=excluded)
    operator_b_drafts = _drafts_for_operator(OPERATOR_B_TOKEN, excluded=excluded)
    a_before = _my_activity(OPERATOR_A_TOKEN)
    b_before = _my_activity(OPERATOR_B_TOKEN)

    operator_a_approval = _approve_first_bulk_item(
        OPERATOR_A_TOKEN,
        operator_a_drafts,
        label="operator-a",
    )
    operator_b_approval = _approve_first_bulk_item(
        OPERATOR_B_TOKEN,
        operator_b_drafts,
        label="operator-b",
    )

    a_committed = _required_string(operator_a_drafts[0], "borrower_id")
    a_interrupted = _required_string(operator_a_drafts[1], "borrower_id")
    b_committed = _required_string(operator_b_drafts[0], "borrower_id")
    b_interrupted = _required_string(operator_b_drafts[1], "borrower_id")
    a_new = _my_activity(OPERATOR_A_TOKEN) - a_before
    b_new = _my_activity(OPERATOR_B_TOKEN) - b_before
    a_subjects = {subject for event_type, subject, _created in a_new if event_type == "APPROVE"}
    b_subjects = {subject for event_type, subject, _created in b_new if event_type == "APPROVE"}

    assert a_committed in a_subjects
    assert a_interrupted not in a_subjects
    assert b_committed not in a_subjects
    assert b_committed in b_subjects
    assert b_interrupted not in b_subjects
    assert a_committed not in b_subjects

    for token in (OPERATOR_A_TOKEN, OPERATOR_B_TOKEN):
        status, body = _request(
            "GET",
            "/api/audit/my-events?actor=another-operator&limit=50",
            token=token,
        )
        assert status == 422, body

    admin_a = _admin_exact_audit_event(operator_a_approval)
    admin_b = _admin_exact_audit_event(operator_b_approval)
    assert admin_a.get("actor")
    assert admin_b.get("actor")
    assert admin_a["actor"] != admin_b["actor"]
