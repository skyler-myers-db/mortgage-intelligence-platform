"""Audit-free Lead Queue aggregates: GET /leads/count and GET /leads/facets.

Both resolve the SAME filters as the ranked list (admin gate, assignee
visibility, cohort replay) but return totals only, so neither writes a
VIEW_LEADS row nor even resolves the audit store. Facets drop the counted
dimension's own filter and answer closed-vocabulary buckets.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend.services.lead_cohort_replay as cohort_replay
from backend.api.leads import IDENTITY_PROOF_LIST_ONLY_DETAIL
from backend.main import app
from backend.schemas.lead_facets import LeadFacetCounts
from backend.services.audit_store import get_audit_store
from backend.services.repositories import get_lead_repository
from backend.services.repositories.databricks_lead_facets import (
    PRODUCT_FACET_LABELS,
    DatabricksLeadFacetRepository,
)
from backend.services.repositories.factory import get_lead_facet_repository
from backend.services.sales_state import get_sales_state_store

USER = {"X-Forwarded-Email": "lo@example.com", "X-Forwarded-Groups": ""}
ADMIN = {"X-Forwarded-Email": "lo@example.com", "X-Forwarded-Groups": "mip-admin"}


class _CountRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def count(self, **kwargs: Any) -> int:
        self.calls.append(kwargs)
        return 41

    def list(self, **kwargs: Any) -> list[object]:
        raise AssertionError("an aggregate read must never read rows")


class _FacetRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def facets(self, dimension: str, **kwargs: Any) -> LeadFacetCounts:
        self.calls.append((dimension, kwargs))
        return LeadFacetCounts(total_matching=5, buckets=[("IL", 3), ("TX", 2)])


class _AuditProbe:
    """Counts resolutions of the audit dependency and every write."""

    def __init__(self) -> None:
        self.resolved = 0
        self.writes = 0

    def provide(self) -> _AuditProbe:
        self.resolved += 1
        return self

    def write(self, **_kwargs: Any) -> None:
        self.writes += 1


class _Sales:
    _client = object()

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    def require_visible_assignee(self, *, actor: str, assigned_to_email: str, use_cache: bool = False) -> None:
        _ = (actor, assigned_to_email, use_cache)

    def borrower_ids_for_assignee(self, assigned_to_email: str | None) -> list[str] | None:
        _ = assigned_to_email
        return list(self.ids)


def _provide(value: object) -> Any:
    def provide() -> object:
        return value

    return provide


@pytest.fixture
def wired() -> Iterator[tuple[_CountRepo, _FacetRepo, _AuditProbe]]:
    count_repo, facet_repo, probe = _CountRepo(), _FacetRepo(), _AuditProbe()
    deps = (get_lead_repository, get_lead_facet_repository, get_audit_store)
    prior = {dep: app.dependency_overrides.get(dep) for dep in deps}
    app.dependency_overrides[get_lead_repository] = _provide(count_repo)
    app.dependency_overrides[get_lead_facet_repository] = _provide(facet_repo)
    app.dependency_overrides[get_audit_store] = probe.provide
    try:
        yield count_repo, facet_repo, probe
    finally:
        for dep, value in prior.items():
            if value is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = value


def _with_sales(ids: list[str]) -> Any:
    prior = app.dependency_overrides.get(get_sales_state_store)
    app.dependency_overrides[get_sales_state_store] = _provide(_Sales(ids))
    return prior


def _restore_sales(prior: Any) -> None:
    if prior is None:
        app.dependency_overrides.pop(get_sales_state_store, None)
    else:
        app.dependency_overrides[get_sales_state_store] = prior


# --- GET /leads/count -----------------------------------------------------------


def test_count_answers_the_repository_total_with_no_audit(wired: tuple[_CountRepo, _FacetRepo, _AuditProbe]) -> None:
    count_repo, _, probe = wired
    response = TestClient(app).get(
        "/api/leads/count",
        params={"state": "IL", "segment_codes": "itm,equity", "min_opportunity_score": "70"},
        headers=USER,
    )
    assert response.status_code == 200
    assert response.json() == {"total_matching": 41}
    assert probe.resolved == 0 and probe.writes == 0
    [kwargs] = count_repo.calls
    assert kwargs["state"] == "IL"
    assert kwargs["segment_codes"] == ["itm", "equity"]
    assert kwargs["min_opportunity_score"] == 70
    # The same "Eligible only" contactability default as the ranked list.
    assert kwargs["portfolio_criteria"].marketing_eligibility == "Eligible only"
    assert "limit" not in kwargs


def test_count_applies_the_admin_gate(wired: tuple[_CountRepo, _FacetRepo, _AuditProbe]) -> None:
    count_repo, _, _ = wired
    refused = TestClient(app).get(
        "/api/leads/count", params={"include_suppressed_for_analytics": "true"}, headers=USER
    )
    assert refused.status_code == 403
    assert count_repo.calls == []
    allowed = TestClient(app).get(
        "/api/leads/count", params={"include_suppressed_for_analytics": "true"}, headers=ADMIN
    )
    assert allowed.status_code == 200
    # The admin override clears the Eligible-only gate: no criteria at all.
    assert "portfolio_criteria" not in count_repo.calls[0]


@pytest.mark.parametrize(
    ("path", "extra"),
    [("/api/leads/count", {}), ("/api/leads/facets", {"dimension": "state"})],
)
def test_the_identity_proof_is_list_only(
    wired: tuple[_CountRepo, _FacetRepo, _AuditProbe], path: str, extra: dict[str, str]
) -> None:
    response = TestClient(app).get(path, params={"include_identity_proof": "true", **extra}, headers=ADMIN)
    assert response.status_code == 422
    assert response.json() == {"detail": IDENTITY_PROOF_LIST_ONLY_DETAIL}


def test_count_never_reads_a_growth_agent_handoff(wired: tuple[_CountRepo, _FacetRepo, _AuditProbe]) -> None:
    count_repo, _, _ = wired
    # Two handoff values 422 on the list; the count does not read the param.
    response = TestClient(app).get(
        "/api/leads/count?segment=itm&growth_handoff=a&growth_handoff=b", headers=USER
    )
    assert response.status_code == 200
    assert count_repo.calls[0]["segment"] == "itm"


def test_a_zero_visible_assignee_counts_zero_with_no_read(wired: tuple[_CountRepo, _FacetRepo, _AuditProbe]) -> None:
    count_repo, facet_repo, probe = wired
    prior = _with_sales([])
    try:
        count = TestClient(app).get("/api/leads/count", params={"assigned_to": "lo.two@summit.example"}, headers=USER)
        facets = TestClient(app).get(
            "/api/leads/facets",
            params={"dimension": "state", "assigned_to": "lo.two@summit.example"},
            headers=USER,
        )
    finally:
        _restore_sales(prior)
    assert count.json() == {"total_matching": 0}
    assert facets.json() == {"dimension": "state", "total_matching": 0, "buckets": []}
    assert count_repo.calls == [] and facet_repo.calls == []
    assert probe.resolved == 0


def test_count_replays_a_governed_cohort(
    wired: tuple[_CountRepo, _FacetRepo, _AuditProbe], monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Cohort:
        def fetchone(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object]:
            _ = (sql, params)
            return {"route_filters": json.dumps({"states": ["TX"], "min_opportunity_score": 80})}

    monkeypatch.setattr(cohort_replay, "get_lakebase_client", lambda: _Cohort())
    count_repo, _, _ = wired
    response = TestClient(app).get(
        "/api/leads/count",
        params={"cohort_id": "11111111-1111-1111-1111-111111111111", "state": "WA"},
        headers=USER,
    )
    assert response.status_code == 200
    [kwargs] = count_repo.calls
    assert kwargs["state"] is None
    assert kwargs["state_codes"] == ["TX"]
    assert kwargs["min_opportunity_score"] == 80


# --- GET /leads/facets --------------------------------------------------------------


@pytest.mark.parametrize(
    ("dimension", "dropped"),
    [
        ("state", {"state": None, "state_codes": None}),
        ("segment", {"segment": None, "segment_codes": None, "segment_mode": "any"}),
        ("approval", {"approval_status": None}),
    ],
)
def test_facets_drop_the_dimensions_own_filter_and_keep_every_other(
    wired: tuple[_CountRepo, _FacetRepo, _AuditProbe], dimension: str, dropped: dict[str, object]
) -> None:
    _, facet_repo, probe = wired
    response = TestClient(app).get(
        "/api/leads/facets",
        params={
            "dimension": dimension,
            "state": "IL",
            "states": "IL,TX",
            "segment_codes": "itm,equity",
            "segment_mode": "all",
            "approval_status": "approved",
            "product": "HELOC",
            "zip": "60617",
        },
        headers=USER,
    )
    assert response.status_code == 200
    assert response.json() == {
        "dimension": dimension,
        "total_matching": 5,
        "buckets": [{"value": "IL", "count": 3}, {"value": "TX", "count": 2}],
    }
    [(called_dimension, kwargs)] = facet_repo.calls
    assert called_dimension == dimension
    for key, value in dropped.items():
        assert kwargs[key] == value
    # Every other filter still applies.
    assert kwargs["zip_code"] == "60617"
    assert kwargs["portfolio_criteria"].product == "HELOC"
    if dimension != "approval":
        assert kwargs["approval_status"] == "approved"
    if dimension != "segment":
        assert kwargs["segment_codes"] == ["itm", "equity"]
    assert probe.resolved == 0 and probe.writes == 0


def test_the_product_facet_drops_only_the_product_filter(wired: tuple[_CountRepo, _FacetRepo, _AuditProbe]) -> None:
    _, facet_repo, _ = wired
    TestClient(app).get(
        "/api/leads/facets",
        params={"dimension": "product", "product": "HELOC", "occupancy": "Owner-occupied"},
        headers=USER,
    )
    [(_, kwargs)] = facet_repo.calls
    assert kwargs["portfolio_criteria"].product is None
    assert kwargs["portfolio_criteria"].occupancy == "Owner-occupied"


def test_an_unknown_dimension_is_refused(wired: tuple[_CountRepo, _FacetRepo, _AuditProbe]) -> None:
    _, facet_repo, _ = wired
    for params in ({"dimension": "borrower_id"}, {}):
        response = TestClient(app).get("/api/leads/facets", params=params, headers=USER)
        assert response.status_code == 422
    assert facet_repo.calls == []


# --- DatabricksLeadFacetRepository -------------------------------------------------


class _Client:
    def __init__(self, rows: list[dict[str, object]] | None = None, row: dict[str, object] | None = None) -> None:
        self.rows = rows or []
        self.row = row or {}
        self.statements: list[tuple[str, dict[str, object]]] = []

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        self.statements.append((sql, dict(params or {})))
        return self.rows

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.statements.append((sql, dict(params or {})))
        return self.row


def _repo(client: _Client, ttl: float = 0) -> DatabricksLeadFacetRepository:
    return DatabricksLeadFacetRepository(client, cache_ttl_s=ttl)  # type: ignore[arg-type]


def test_state_buckets_are_usps_only_and_sum_to_the_total() -> None:
    client = _Client(
        rows=[
            {"facet_value": "IL", "facet_count": 3},
            {"facet_value": "tx", "facet_count": 2},
            {"facet_value": "XX", "facet_count": 9},
            {"facet_value": None, "facet_count": 4},
            {"facet_value": "jane@example.com", "facet_count": 1},
        ]
    )
    counts = _repo(client).facets("state", segment=None, portfolio_id=None, state_codes=["IL"])
    assert counts.buckets == [("IL", 3), ("TX", 2)]
    assert counts.total_matching == sum(count for _, count in counts.buckets) == 5
    sql, params = client.statements[0]
    assert "GROUP BY m.state" in sql
    assert "mip.gold.borrower_360" in sql
    assert "silver" not in sql.lower()
    assert params["state_0"] == "IL"


def test_approval_buckets_are_the_reviewed_states_in_order() -> None:
    client = _Client(
        rows=[
            {"facet_value": "hold", "facet_count": 1},
            {"facet_value": "Approved", "facet_count": 4},
            {"facet_value": "pending", "facet_count": 6},
            {"facet_value": "deleted", "facet_count": 7},
        ]
    )
    counts = _repo(client).facets("approval", segment=None, portfolio_id=None)
    assert counts.buckets == [("pending", 6), ("approved", 4), ("hold", 1)]
    assert counts.total_matching == 11
    assert "GROUP BY m.approval_status" in client.statements[0][0]


def test_segment_counts_bind_every_code_and_total_is_count_star() -> None:
    client = _Client(row={"facet_total": 20, "facet_0": 7, "facet_1": 3})
    counts = _repo(client).facets("segment", segment=None, portfolio_id=None, max_rate_spread_bps=100)
    sql, params = client.statements[0]
    assert counts.total_matching == 20
    assert counts.buckets[0] == ("itm", 7)
    assert counts.buckets[1] == ("listed", 3)
    assert all(count == 0 for _, count in counts.buckets[2:])
    assert "array_contains(m.segment_codes, :facet_segment_0)" in sql
    assert params["facet_segment_0"] == "itm"
    # Codes are bound, never interpolated.
    assert "'itm'" not in sql
    assert "b.rate_spread_bps <= :public_max_rate_spread_bps" in sql


def test_product_counts_use_the_reviewed_labels_and_offer_codes() -> None:
    client = _Client(row={"facet_total": 9, "facet_0": 4, "facet_1": 2})
    counts = _repo(client).facets("product", segment=None, portfolio_id=None)
    assert [label for label, _ in counts.buckets] == list(PRODUCT_FACET_LABELS.values())
    assert counts.buckets[0] == ("Refi", 4)
    sql, params = client.statements[0]
    assert "m.recommended_offer_code IN (:facet_product_0_0, :facet_product_0_1)" in sql
    assert params["facet_product_0_0"] == "refi"
    assert params["facet_product_0_1"] == "refi_plus_heloc"


def test_the_facet_cache_key_covers_the_dimension_and_every_filter() -> None:
    client = _Client(rows=[{"facet_value": "IL", "facet_count": 1}], row={"facet_total": 1})
    repo = _repo(client, ttl=60)
    repo.facets("state", segment=None, portfolio_id=None, approval_status="approved")
    repo.facets("state", segment=None, portfolio_id=None, approval_status="approved")
    assert len(client.statements) == 1
    repo.facets("approval", segment=None, portfolio_id=None, approval_status="approved")
    repo.facets("state", segment=None, portfolio_id=None, approval_status="pending")
    repo.facets("state", segment=None, portfolio_id=None, approval_status="approved", max_opportunity_score=80)
    assert len(client.statements) == 4
