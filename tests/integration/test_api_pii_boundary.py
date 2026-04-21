"""Integration check: no API route leaks raw-PII keys into its response.

The ``conftest.py`` session fixture wires every repository to an
in-process stub, so this test exercises the real FastAPI router code
paths + Pydantic serialization, not the Databricks backends. The
assertion is structural: for each route that returns a borrower /
lead / evidence payload, the JSON response keys must be a subset of
the declared Pydantic schema fields. No raw gold columns
(``owner_name_hash``, ``trigger_timeline_json``, ``clip``,
``owner_1_full_name``, etc.) may appear anywhere in the payload.

This complements the unit tests in ``tests/unit/test_pii_redaction.py``
by catching any router that forgets to go through the repository
seam (for example, a new route that hand-rolls a ``SELECT *``).
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import PortfolioPreview

client = TestClient(app)


# Forbidden raw-column keys. If any of these appears in a JSON response
# the redaction contract has been broken. Match ``pii_redaction
# ._FORBIDDEN_OUTPUT_KEYS``.
_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "owner_1_full_name",
        "owner_full_name_raw",
        "owner_name_hash",           # internal hash; UI never sees it
        "owner_name_hash_raw",
        "situs_street_address",
        "situs_street_address_raw",
        "mailing_street_address",
        "mailing_street_raw",
        "mailing_city",
        "mailing_state",
        "trigger_timeline_json",     # raw JSON string; UI gets parsed struct
        "buyer_1_full_name",
        "buyer_full_name_raw",
        "clip",                      # raw CLIP; UI sees clip_id only
    }
)


def _walk_keys(node: Any) -> set[str]:
    """Collect every dict key anywhere in a (possibly nested) payload."""
    keys: set[str] = set()
    if isinstance(node, dict):
        keys.update(node.keys())
        for v in node.values():
            keys.update(_walk_keys(v))
    elif isinstance(node, list):
        for item in node:
            keys.update(_walk_keys(item))
    return keys


def _assert_no_forbidden_keys(payload: Any, *, route: str) -> None:
    seen = _walk_keys(payload)
    leaks = _FORBIDDEN_KEYS.intersection(seen)
    assert not leaks, (
        f"{route} leaked forbidden PII keys: {sorted(leaks)} "
        f"(full keyset: {sorted(seen)})"
    )


# ---------------------------------------------------------------------------
# /api/borrowers/{id}
# ---------------------------------------------------------------------------


def test_borrower_get_has_only_schema_keys() -> None:
    resp = client.get("/api/borrowers/B-48291")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/borrowers/{id}")
    # Keys at the top level MUST be a subset of Borrower360 fields.
    allowed_top = set(Borrower360.model_fields.keys())
    assert set(body.keys()).issubset(allowed_top), (
        f"Borrower360 response has extra keys: {set(body.keys()) - allowed_top}"
    )
    # Nested evidence events must be EvidenceEvent-shaped too.
    for ev in body["evidence_events"]:
        assert set(ev.keys()).issubset(set(EvidenceEvent.model_fields.keys()))


def test_borrower_evidence_has_only_schema_keys() -> None:
    resp = client.get("/api/borrowers/B-48291/evidence")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/borrowers/{id}/evidence")
    allowed = set(EvidenceEvent.model_fields.keys())
    for ev in body:
        assert set(ev.keys()).issubset(allowed), (
            f"EvidenceEvent has extra keys: {set(ev.keys()) - allowed}"
        )


# ---------------------------------------------------------------------------
# /api/leads
# ---------------------------------------------------------------------------


def test_leads_list_has_only_schema_keys() -> None:
    resp = client.get("/api/leads?portfolio_id=p1")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/leads")
    allowed = set(LeadSummary.model_fields.keys())
    for row in body:
        assert set(row.keys()).issubset(allowed), (
            f"LeadSummary row has extra keys: {set(row.keys()) - allowed}"
        )


# ---------------------------------------------------------------------------
# /api/segments
# ---------------------------------------------------------------------------


def test_segments_list_has_only_schema_keys() -> None:
    resp = client.get("/api/segments?portfolio_id=p1")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/segments")
    allowed = set(SegmentSummary.model_fields.keys())
    for row in body:
        assert set(row.keys()).issubset(allowed)


# ---------------------------------------------------------------------------
# /api/portfolio/preview
# ---------------------------------------------------------------------------


def test_portfolio_preview_has_only_schema_keys() -> None:
    resp = client.post("/api/portfolio/preview", json={"criteria": {}})
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/portfolio/preview")
    allowed = set(PortfolioPreview.model_fields.keys())
    assert set(body.keys()).issubset(allowed)
