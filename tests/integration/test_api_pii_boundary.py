"""Integration check: no API route leaks raw-PII keys into its response.

The ``conftest.py`` session fixture wires every repository to an
in-process stub, so this test exercises the real FastAPI router code
paths + Pydantic serialization, not the Databricks backends. The
assertion is structural: for each route that returns a borrower /
lead / evidence payload, the JSON response keys must be a subset of
the declared Pydantic schema fields. No raw gold columns
(``owner_name_hash``, ``trigger_timeline_json``,
``owner_1_full_name``, etc.) may appear anywhere in the payload.

Note on Cotality identifiers: raw CLIP and Owner Link are licensed
quasi-identifiers. API surfaces may carry display-safe surrogates
(``clip_ref_*``, ``owner_link_ref_*``) or synthetic demo ids
(``clip_demo_*``, ``ol_demo_*``), never the raw share values.

This complements the unit tests in ``tests/unit/test_pii_redaction.py``
by catching any router that forgets to go through the repository
seam (for example, a new route that hand-rolls a ``SELECT *``).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.assets import AssetMetadataResponse
from backend.schemas.analytics import (
    EconomicsAnalyticsResponse,
    ExecutiveAnalyticsResponse,
    GeographyAnalyticsResponse,
    SegmentAnalyticsResponse,
    SignalAnalyticsResponse,
)
from backend.schemas.common import EvidenceEvent
from backend.schemas.lead import Borrower360, LeadSummary, SegmentSummary
from backend.schemas.portfolio import PortfolioPreview
from backend.schemas.proof import BorrowerProof

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
    }
)

_ANALYTICS_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "clip",
        "clip_id",
        "raw_clip",
        "subject_clip",
        "owner_link_id",
        "owner_link_raw",
        "owner_name_hash",
        "owner_1_full_name",
        "owner_full_name_raw",
        "situs_street_address",
        "mailing_street_address",
        "email",
        "actor_email",
        "lender",
        "lender_ref",
        "target_lender_refs",
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


def _walk_pairs(node: Any) -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            pairs.append((str(k), v))
            pairs.extend(_walk_pairs(v))
    elif isinstance(node, list):
        for item in node:
            pairs.extend(_walk_pairs(item))
    return pairs


def _assert_no_forbidden_keys(payload: Any, *, route: str) -> None:
    seen = _walk_keys(payload)
    leaks = _FORBIDDEN_KEYS.intersection(seen)
    assert not leaks, (
        f"{route} leaked forbidden PII keys: {sorted(leaks)} "
        f"(full keyset: {sorted(seen)})"
    )


def _assert_no_analytics_forbidden_keys(payload: Any, *, route: str) -> None:
    seen = _walk_keys(payload)
    leaks = _ANALYTICS_FORBIDDEN_KEYS.intersection(seen)
    assert not leaks, (
        f"{route} leaked analytics-forbidden PII keys: {sorted(leaks)} "
        f"(full keyset: {sorted(seen)})"
    )


def _assert_no_raw_cotality_ids(payload: Any, *, route: str) -> None:
    for key, value in _walk_pairs(payload):
        if value in (None, ""):
            continue
        text = str(value)
        if key in {"clip", "clip_id", "subject_clip"}:
            assert text.startswith(("clip_ref_", "clip_demo_")), (
                f"{route} exposed raw CLIP-like value for {key}: {text!r}"
            )
        if key == "owner_link_id":
            assert text.startswith(("owner_link_ref_", "ol_demo_")), (
                f"{route} exposed raw Owner Link value: {text!r}"
            )


def _assert_schema_subset(payload: Any, *, route: str, schema: type[Any]) -> None:
    allowed = set(schema.model_fields.keys())
    assert set(payload.keys()).issubset(allowed), (
        f"{route} has extra top-level keys: {set(payload.keys()) - allowed}"
    )


# ---------------------------------------------------------------------------
# /api/borrowers/{id}
# ---------------------------------------------------------------------------


def test_borrower_get_has_only_schema_keys() -> None:
    resp = client.get("/api/borrowers/B-48291")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/borrowers/{id}")
    _assert_no_raw_cotality_ids(body, route="/api/borrowers/{id}")
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
    _assert_no_raw_cotality_ids(body, route="/api/borrowers/{id}/evidence")
    allowed = set(EvidenceEvent.model_fields.keys())
    for ev in body:
        assert set(ev.keys()).issubset(allowed), (
            f"EvidenceEvent has extra keys: {set(ev.keys()) - allowed}"
        )


def test_borrower_proof_has_only_schema_keys_and_no_raw_pii() -> None:
    resp = client.get("/api/borrowers/B-48291/proof")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/borrowers/{id}/proof")
    _assert_no_raw_cotality_ids(body, route="/api/borrowers/{id}/proof")
    _assert_schema_subset(body, route="/api/borrowers/{id}/proof", schema=BorrowerProof)
    assert "confidence" not in body
    assert body["signal_strength_note"]
    assert body["evidence_confidence_note"]
    for evidence in body["evidence_rows"]:
        assert "source_table" not in evidence
    for query in body["reproduce"]:
        assert "SELECT *" not in query["sql"].upper()
        assert "owner_name_hash" not in query["sql"].lower()
        assert "source_table" not in query["sql"].lower()
        assert ";" not in query["sql"]


def test_asset_metadata_has_only_schema_keys_and_no_raw_pii() -> None:
    resp = client.get("/api/admin/assets/lead_population/metadata")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/admin/assets/{asset_key}/metadata")
    _assert_no_raw_cotality_ids(body, route="/api/admin/assets/{asset_key}/metadata")
    _assert_schema_subset(
        body,
        route="/api/admin/assets/{asset_key}/metadata",
        schema=AssetMetadataResponse,
    )

    rendered = json.dumps(body).lower()
    assert "owner_name_hash" not in rendered
    assert "source_table" not in rendered
    assert "dbfs:/" not in rendered
    assert "s3://" not in rendered
    assert "mip.silver" not in rendered


# ---------------------------------------------------------------------------
# /api/leads
# ---------------------------------------------------------------------------


def test_leads_list_has_only_schema_keys() -> None:
    resp = client.get("/api/leads?portfolio_id=p1")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/leads")
    _assert_no_raw_cotality_ids(body, route="/api/leads")
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
    _assert_no_raw_cotality_ids(body, route="/api/segments")
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


# ---------------------------------------------------------------------------
# /api/v1/analytics/*
# ---------------------------------------------------------------------------


def test_analytics_executive_has_only_schema_keys_and_no_raw_pii() -> None:
    resp = client.get("/api/v1/analytics/executive")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/v1/analytics/executive")
    _assert_no_analytics_forbidden_keys(body, route="/api/v1/analytics/executive")
    _assert_no_raw_cotality_ids(body, route="/api/v1/analytics/executive")
    _assert_schema_subset(
        body,
        route="/api/v1/analytics/executive",
        schema=ExecutiveAnalyticsResponse,
    )


def test_analytics_geography_has_only_schema_keys_and_no_raw_pii() -> None:
    resp = client.get("/api/v1/analytics/geography")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/v1/analytics/geography")
    _assert_no_analytics_forbidden_keys(body, route="/api/v1/analytics/geography")
    _assert_no_raw_cotality_ids(body, route="/api/v1/analytics/geography")
    _assert_schema_subset(
        body,
        route="/api/v1/analytics/geography",
        schema=GeographyAnalyticsResponse,
    )


def test_analytics_economics_has_only_schema_keys_and_no_raw_pii() -> None:
    resp = client.get("/api/v1/analytics/economics")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/v1/analytics/economics")
    _assert_no_analytics_forbidden_keys(body, route="/api/v1/analytics/economics")
    _assert_no_raw_cotality_ids(body, route="/api/v1/analytics/economics")
    _assert_schema_subset(
        body,
        route="/api/v1/analytics/economics",
        schema=EconomicsAnalyticsResponse,
    )


def test_analytics_segments_has_only_schema_keys_and_no_raw_pii() -> None:
    resp = client.get("/api/v1/analytics/segments")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/v1/analytics/segments")
    _assert_no_analytics_forbidden_keys(body, route="/api/v1/analytics/segments")
    _assert_no_raw_cotality_ids(body, route="/api/v1/analytics/segments")
    _assert_schema_subset(
        body,
        route="/api/v1/analytics/segments",
        schema=SegmentAnalyticsResponse,
    )


def test_analytics_signals_has_only_schema_keys_and_no_raw_pii() -> None:
    resp = client.get("/api/v1/analytics/signals")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_forbidden_keys(body, route="/api/v1/analytics/signals")
    _assert_no_analytics_forbidden_keys(body, route="/api/v1/analytics/signals")
    _assert_no_raw_cotality_ids(body, route="/api/v1/analytics/signals")
    _assert_schema_subset(
        body,
        route="/api/v1/analytics/signals",
        schema=SignalAnalyticsResponse,
    )
