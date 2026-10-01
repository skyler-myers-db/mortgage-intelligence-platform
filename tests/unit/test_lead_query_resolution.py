"""The Lead Queue filter contract: one parameter dataclass, one resolver.

``LeadQueryParams`` is the single place every Lead Queue read (the ranked
list, the audit-free count and facets) takes its filters from, and the W5c
paging lane hashes its request fingerprint from it. Its field order is the
OpenAPI parameter order FastAPI flattens the dependency into, so a reorder
would silently reshuffle the published contract.
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.main import app
from backend.schemas.lead import LeadSummary
from backend.schemas.lead_query import LeadQueryParams, lead_query_params
from backend.services.lead_query_resolution import (
    ResolvedLeadQuery,
    resolve_lead_query,
    view_leads_audit_payload,
)

#: GET /leads before W5a, minus ``limit`` (tests/fixtures/openapi_baseline.json
#: at 0a30fca2). Wire names: ``zip_code`` is published as ``zip``.
PRE_W5A_WIRE_ORDER: tuple[str, ...] = (
    "segment", "segment_codes", "segment_mode", "portfolio_id", "state", "zip", "county",
    "states", "zips", "counties", "cities", "borrower_ids", "target_lender_ref", "geography",
    "occupancy", "lien_status", "lender_relationship", "product", "loan_product",
    "origination_channel", "min_equity_pct_label", "min_equity_pct", "owner_link",
    "purchase_intent", "marketing_eligibility", "consent_status", "recency",
    "include_suppressed_for_analytics", "include_identity_proof", "approval_status",
    "outreach_status", "assigned_to", "aged_days", "cohort_id", "funnel_stage",
)

_WIRE_NAME = {"zip_code": "zip"}


def _field_wire_names() -> list[str]:
    return [_WIRE_NAME.get(f.name, f.name) for f in dataclasses.fields(LeadQueryParams)]


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/api/leads", "headers": raw, "query_string": b""})


def test_the_dataclass_keeps_the_pre_w5a_wire_order_as_its_prefix() -> None:
    assert tuple(_field_wire_names()[: len(PRE_W5A_WIRE_ORDER)]) == PRE_W5A_WIRE_ORDER


@pytest.mark.parametrize("path", ["/api/leads", "/api/v1/leads"])
def test_the_published_parameter_order_is_the_dataclass_order_then_limit(path: str) -> None:
    published = [param["name"] for param in app.openapi()["paths"][path]["get"]["parameters"]]
    assert published == [*_field_wire_names(), "limit"]


def test_the_dependency_returns_every_declared_default() -> None:
    params = lead_query_params()
    assert params.segment_mode == "any"
    assert params.marketing_eligibility == "Eligible only"
    assert params.approval_status == "any"
    assert params.outreach_status == "any"
    assert params.include_suppressed_for_analytics is False
    assert params.include_identity_proof is False
    assert all(
        getattr(params, f.name) is None
        for f in dataclasses.fields(LeadQueryParams)
        if f.name
        not in {
            "segment_mode",
            "marketing_eligibility",
            "approval_status",
            "outreach_status",
            "include_suppressed_for_analytics",
            "include_identity_proof",
        }
    )


def test_the_params_are_frozen() -> None:
    params = lead_query_params()
    with pytest.raises(dataclasses.FrozenInstanceError):
        params.segment = "itm"  # type: ignore[misc]


def test_an_aggregate_read_ignores_a_handoff_it_was_never_given() -> None:
    """``growth_handoff=None`` is how count and facets resolve: no proof read."""

    resolved = resolve_lead_query(
        _request({"X-Forwarded-Email": "lo@example.com"}),
        sales_state=None,  # type: ignore[arg-type]
        params=lead_query_params(segment="itm"),
        growth_handoff=None,
    )
    assert resolved.handoff_proof is None
    assert resolved.repository_args["segment"] == "itm"


def test_two_handoff_values_are_refused_before_any_read() -> None:
    with pytest.raises(HTTPException) as refused:
        resolve_lead_query(
            _request(),
            sales_state=None,  # type: ignore[arg-type]
            params=lead_query_params(),
            growth_handoff=["a", "b"],
        )
    assert refused.value.status_code == 422
    assert refused.value.detail == "Growth Agent handoff proof is invalid"


def test_the_segment_check_precedes_the_handoff_shape_check() -> None:
    """Same precedence as the inline router: an unknown segment names itself."""

    with pytest.raises(HTTPException) as refused:
        resolve_lead_query(
            _request(),
            sales_state=None,  # type: ignore[arg-type]
            params=lead_query_params(segment="bogus"),
            growth_handoff=["a", "b"],
        )
    assert refused.value.detail == "segment contains an unknown segment"


def test_the_view_leads_payload_names_the_rendered_rows_and_the_filters() -> None:
    lead = LeadSummary(
        borrower_id="B-0123456789ABC",
        display_name="Borrower abc",
        city="Chicago",
        state="IL",
        zip="60617",
        segment_codes=["itm"],
        equity_estimate=1,
        rate_spread_bps=1,
        opportunity_score=80,
        confidence=80,
        recommended_offer="Refinance",
        why_now="test",
        evidence_ids=[],
    )
    resolved = ResolvedLeadQuery(
        actor="lo@example.com",
        segment_codes=["itm", "equity"],
        segment_mode="all",
        state="il",
        approval_status="approved",
        aged_days=7,
    )
    assert view_leads_audit_payload(resolved, [lead], limit=50) == {
        "rendered_borrower_ids": ["B-0123456789ABC"],
        "portfolio_id": None,
        "segment": None,
        "limit": 50,
        "segment_codes": ["itm", "equity"],
        "segment_mode": "all",
        "state": "IL",
        "approval_status": "approved",
        "aged_days": 7,
    }
