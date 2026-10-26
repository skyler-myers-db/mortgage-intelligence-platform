"""An audit payload that names a segment label also carries its code.

critic-11 / D-platform-process-g step 11 (examiner reconstruction). Display
labels change ("Prime Refi Candidates" became "In the Money" in 2026-10), and
historical audit rows are never rewritten, so an examiner reads a stored row
by its CODE. This suite runs the real routes against the in-process fakes and
reads the metadata the audit writer actually stored:

* every reviewed GROWTH_AGENT_RUN workflow and ``custom_segment_watch`` for
  each reviewed code: whenever the stored payload names a segment label, the
  payload's ``result_filters`` names the matching code (``segment`` or
  ``segment_codes``);
* every RECOMMEND_OFFER payload for the fixture book: same rule.

Assertion-first (joint check C7): it found no payload that names a label
without its code, so no route changed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.growth_agent_workflows import SEGMENT_LABELS, WORKFLOWS
from backend.services.segment_labels import LEGACY_SEGMENT_NAMES, SEGMENT_DISPLAY_LABELS
from tests.fixtures import mock_population
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.unit.test_growth_agent_api import (
    _clear_overrides,
    _client,
    _FakeLakebaseClient,
    _FakeSqlClient,
)

_HEADERS = {"X-Forwarded-Email": "operator@summit.example"}
_LABEL_TO_CODE: dict[str, str] = {
    **{label: code for code, label in SEGMENT_DISPLAY_LABELS.items()},
    **LEGACY_SEGMENT_NAMES,
}


def _named_codes(payload: dict[str, Any]) -> set[str]:
    """Codes whose display label (current or legacy) appears anywhere in the payload."""

    text = json.dumps(payload)
    return {code for label, code in _LABEL_TO_CODE.items() if label in text}


def _carried_codes(payload: dict[str, Any]) -> set[str]:
    filters = payload.get("result_filters") or {}
    codes: set[str] = set()
    single = filters.get("segment")
    if isinstance(single, str) and single:
        codes.add(single)
    many = filters.get("segment_codes")
    if isinstance(many, str):
        codes.update(code for code in many.split(",") if code)
    elif isinstance(many, list):
        codes.update(str(code) for code in many)
    return codes


def _growth_run_metadata(path: str, body: dict[str, Any]) -> dict[str, Any]:
    sql = _FakeSqlClient()
    lakebase = _FakeLakebaseClient()
    client = _client(sql, lakebase)
    try:
        response = client.post(path, json=body, headers=_HEADERS)
    finally:
        _clear_overrides()
    assert response.status_code == 200, response.text
    assert len(lakebase.audit_events) == 1
    event = lakebase.audit_events[0]
    assert event["event_type"] == "GROWTH_AGENT_RUN"
    metadata = json.loads(event["metadata"])
    assert isinstance(metadata, dict)
    return metadata


_GROWTH_CASES: list[tuple[str, str, dict[str, Any]]] = [
    *[
        (f"workflow:{workflow_id}", f"/api/growth-agent/workflows/{workflow_id}/run", {"states": ["IL", "TX"]})
        for workflow_id in WORKFLOWS
    ],
    *[
        (
            f"custom:{code}",
            "/api/growth-agent/custom/run",
            {"states": ["IL"], "segment_codes": [code], "segment_mode": "any"},
        )
        for code in SEGMENT_LABELS
    ],
    (
        "custom:itm+permit:all",
        "/api/growth-agent/custom/run",
        {"states": ["IL"], "segment_codes": ["itm", "permit"], "segment_mode": "all"},
    ),
]


@pytest.mark.parametrize(("case_id", "path", "body"), _GROWTH_CASES, ids=[case[0] for case in _GROWTH_CASES])
def test_growth_agent_run_payload_names_no_label_without_its_code(
    case_id: str, path: str, body: dict[str, Any]
) -> None:
    metadata = _growth_run_metadata(path, body)
    named = _named_codes(metadata)
    assert named <= _carried_codes(metadata), (case_id, named, _carried_codes(metadata))


def test_the_label_scan_is_not_vacuous() -> None:
    """The custom workflow for each code really names its label, and carries the code."""

    for code, label in SEGMENT_LABELS.items():
        metadata = _growth_run_metadata(
            "/api/growth-agent/custom/run",
            {"states": ["IL"], "segment_codes": [code], "segment_mode": "any"},
        )
        assert label in json.dumps(metadata), code
        assert code in _named_codes(metadata)
        assert code in _carried_codes(metadata)
    assert _named_codes({"note": "Prime Refi Candidates"}) == {"itm"}


def test_recommend_offer_payload_names_no_label_without_its_code() -> None:
    audit = InMemoryAuditStore()
    previous = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    client = TestClient(app)
    recommended = 0
    try:
        for borrower_id in mock_population.BORROWER_OFFER_INPUTS:
            response = client.post("/api/offers/recommend", json={"borrower_id": borrower_id})
            # A few fixture rows are deliberately incomplete and fail closed (500, no audit).
            recommended += response.status_code == 200
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous
    events = audit.list(limit=500, event_type="RECOMMEND_OFFER")
    assert len(events) == recommended >= 10
    for event in events:
        payload = dict(event.payload_json)
        assert _named_codes(payload) <= _carried_codes(payload), event.entity_id
