"""Public score and rate-spread bounds on GET /leads (audit tables-06, wow-stage-1).

``min/max_opportunity_score`` (0..100) and ``min/max_rate_spread_bps``
(-1000..5000, signed) are validated at the edge, refused beside a governed
cohort or a Growth Agent handoff, applied identically to the ranked rows, the
X-Total-Matching count and the identity proof, cached under keys that include
them, and recorded on the one VIEW_LEADS row the read writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.lead import LeadSummary
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    _assert_allowlisted,
    _assert_public_safe_values,
    get_audit_store,
)
from backend.services.lead_query_resolution import (
    BOUNDS_WITH_COHORT_DETAIL,
    BOUNDS_WITH_HANDOFF_DETAIL,
)
from backend.services.repositories import get_lead_repository
from backend.services.repositories.databricks_lead_cohorts import numeric_ceiling_clause
from backend.services.repositories.databricks_repo import DatabricksLeadRepository
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

HEADERS = {"X-Forwarded-Email": "lo@example.com", "X-Forwarded-Groups": ""}
ADMIN = {"X-Forwarded-Email": "lo@example.com", "X-Forwarded-Groups": "mip-admin"}
ALL_BOUNDS = {
    "min_opportunity_score": "70",
    "max_opportunity_score": "90",
    "min_rate_spread_bps": "-25",
    "max_rate_spread_bps": "150",
}
EXPECTED_KWARGS = {
    "min_opportunity_score": 70,
    "max_opportunity_score": 90,
    "min_rate_spread_bps": -25,
    "max_rate_spread_bps": 150,
}
BOUND_KEYS = tuple(EXPECTED_KWARGS)


def _lead(borrower_id: str = "B-0123456789ABC") -> LeadSummary:
    return LeadSummary(
        borrower_id=borrower_id,
        display_name="Borrower abc",
        city="Chicago",
        state="IL",
        zip="60617",
        segment_codes=["itm"],
        equity_estimate=1,
        rate_spread_bps=80,
        opportunity_score=80,
        confidence=80,
        recommended_offer="Refinance",
        why_now="test",
        evidence_ids=[],
    )


class _RecordingRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list(self, **kwargs: Any) -> list[LeadSummary]:
        self.calls.append(("list", kwargs))
        return []

    def count(self, **kwargs: Any) -> int:
        self.calls.append(("count", kwargs))
        return 3

    def list_with_identity(self, **kwargs: Any) -> tuple[list[LeadSummary], dict[str, str | int]]:
        self.calls.append(("list_with_identity", kwargs))
        return [], {"total": 3, "cohort_digest": "b" * 64, "snapshot_id": "s-1"}


def _provide(value: object) -> Any:
    def provide() -> object:
        return value

    return provide


@pytest.fixture
def repo() -> Iterator[_RecordingRepo]:
    recorder = _RecordingRepo()
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = _provide(recorder)
    try:
        yield recorder
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior


@pytest.fixture
def audit() -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    prior = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = _provide(store)
    try:
        yield store
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = prior


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"min_opportunity_score": "-1"},
        {"max_opportunity_score": "101"},
        {"min_rate_spread_bps": "-1001"},
        {"max_rate_spread_bps": "5001"},
        {"min_opportunity_score": "70.5"},
        {"max_rate_spread_bps": "abc"},
    ],
)
def test_out_of_range_or_non_integer_bounds_are_refused_at_the_edge(
    repo: _RecordingRepo, params: dict[str, str]
) -> None:
    response = TestClient(app).get("/api/leads", params=params, headers=HEADERS)
    assert response.status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        (
            {"min_opportunity_score": "91", "max_opportunity_score": "90"},
            "min_opportunity_score must not exceed max_opportunity_score",
        ),
        (
            {"min_rate_spread_bps": "10", "max_rate_spread_bps": "-10"},
            "min_rate_spread_bps must not exceed max_rate_spread_bps",
        ),
    ],
)
def test_an_inverted_pair_is_refused_with_fixed_copy(
    repo: _RecordingRepo, params: dict[str, str], detail: str
) -> None:
    response = TestClient(app).get("/api/leads", params=params, headers=HEADERS)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    assert repo.calls == []


def test_equal_bounds_are_a_valid_single_value(repo: _RecordingRepo) -> None:
    response = TestClient(app).get(
        "/api/leads",
        params={"min_opportunity_score": "80", "max_opportunity_score": "80"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert repo.calls[0][1]["min_opportunity_score"] == 80
    assert repo.calls[0][1]["max_opportunity_score"] == 80


@pytest.mark.parametrize("bound", BOUND_KEYS)
def test_a_bound_beside_a_genie_cohort_is_refused(repo: _RecordingRepo, bound: str) -> None:
    response = TestClient(app).get(
        "/api/leads",
        params={"cohort_id": "11111111-1111-1111-1111-111111111111", bound: "50"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json() == {"detail": BOUNDS_WITH_COHORT_DETAIL}
    assert repo.calls == []


@pytest.mark.parametrize("bound", BOUND_KEYS)
def test_a_bound_beside_a_growth_agent_handoff_is_refused(repo: _RecordingRepo, bound: str) -> None:
    response = TestClient(app).get(
        "/api/leads",
        params={"segment": "itm", "growth_handoff": "token", bound: "50"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json() == {"detail": BOUNDS_WITH_HANDOFF_DETAIL}
    assert repo.calls == []


# --- the bounds reach every read the same way -------------------------------


def test_bounds_reach_the_list_and_the_count_with_the_same_kwargs(repo: _RecordingRepo) -> None:
    response = TestClient(app).get("/api/leads", params={**ALL_BOUNDS, "state": "IL"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["X-Total-Matching"] == "3"
    methods = [method for method, _ in repo.calls]
    assert methods == ["list", "count"]
    list_kwargs = {k: v for k, v in repo.calls[0][1].items() if k != "limit"}
    count_kwargs = repo.calls[1][1]
    assert list_kwargs == count_kwargs
    for key, value in EXPECTED_KWARGS.items():
        assert count_kwargs[key] == value


def test_bounds_reach_the_identity_proof(repo: _RecordingRepo) -> None:
    response = TestClient(app).get(
        "/api/leads",
        params={**ALL_BOUNDS, "include_identity_proof": "true"},
        headers=ADMIN,
    )
    assert response.status_code == 200
    assert [method for method, _ in repo.calls] == ["list_with_identity"]
    for key, value in EXPECTED_KWARGS.items():
        assert repo.calls[0][1][key] == value


def test_unset_bounds_are_never_passed(repo: _RecordingRepo) -> None:
    """Older repositories and test doubles keep their signatures."""

    response = TestClient(app).get("/api/leads", params={"max_rate_spread_bps": "0"}, headers=HEADERS)
    assert response.status_code == 200
    kwargs = repo.calls[0][1]
    assert kwargs["max_rate_spread_bps"] == 0
    for key in ("min_opportunity_score", "max_opportunity_score", "min_rate_spread_bps"):
        assert key not in kwargs


# --- VIEW_LEADS ---------------------------------------------------------------


def test_view_leads_records_the_effective_floors_and_the_ceilings(
    repo: _RecordingRepo, audit: InMemoryAuditStore
) -> None:
    response = TestClient(app).get("/api/leads", params=ALL_BOUNDS, headers=HEADERS)
    assert response.status_code == 200
    events = audit.list(limit=5)
    assert [event.event_type for event in events] == ["VIEW_LEADS"]
    payload = events[0].payload_json
    for key, value in EXPECTED_KWARGS.items():
        assert payload[key] == value


def test_view_leads_without_bounds_records_none_of_them(
    repo: _RecordingRepo, audit: InMemoryAuditStore
) -> None:
    TestClient(app).get("/api/leads", params={"state": "IL"}, headers=HEADERS)
    payload = audit.list(limit=1)[0].payload_json
    assert not set(BOUND_KEYS) & set(payload)


def test_audit_policy_accepts_the_ceiling_keys_in_range() -> None:
    _assert_allowlisted({"max_opportunity_score": 90, "max_rate_spread_bps": -25})
    _assert_public_safe_values({"max_opportunity_score": 0, "max_rate_spread_bps": 5000})
    _assert_public_safe_values({"max_opportunity_score": 100, "max_rate_spread_bps": -1000})


@pytest.mark.parametrize(
    "payload",
    [
        {"max_opportunity_score": 101},
        {"max_opportunity_score": -1},
        {"max_opportunity_score": True},
        {"max_opportunity_score": "90"},
        {"max_rate_spread_bps": 5001},
        {"max_rate_spread_bps": 12.5},
        {"max_rate_spread_bps": "jane@example.com"},
    ],
)
def test_audit_policy_rejects_a_ceiling_outside_its_floor_twins_range(payload: dict[str, object]) -> None:
    with pytest.raises(AuditMetadataValueViolation) as refused:
        _assert_public_safe_values(payload)
    assert "jane" not in str(refused.value)


# --- SQL ------------------------------------------------------------------------


class _Client:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, object]]] = []

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        self.statements.append((sql, dict(params or {})))
        return []

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self.statements.append((sql, dict(params or {})))
        return {"n": 4}


def test_the_ceiling_clause_is_a_plain_bound_comparison_so_null_spreads_never_match() -> None:
    clause, params = numeric_ceiling_clause(max_opportunity_score=90, max_rate_spread_bps=150)
    assert clause == (
        "AND b.opportunity_score <= :public_max_opportunity_score "
        "AND b.rate_spread_bps <= :public_max_rate_spread_bps"
    )
    assert params == {"public_max_opportunity_score": 90, "public_max_rate_spread_bps": 150}
    # No COALESCE / IS NULL escape: NULL <= x is UNKNOWN, so the row is out.
    assert "COALESCE" not in clause.upper()
    assert "NULL" not in clause.upper()
    assert numeric_ceiling_clause() == ("", {})


def test_no_bound_keeps_the_ranked_lead_population_path() -> None:
    client = _Client()
    DatabricksLeadRepository(client, cache_ttl_s=0).list(segment=None, portfolio_id=None)  # type: ignore[arg-type]
    sql = client.statements[0][0]
    assert "lead_population" in sql
    assert "public_max" not in sql


def test_a_max_only_request_takes_the_borrower_360_path_with_the_ceiling() -> None:
    client = _Client()
    repo = DatabricksLeadRepository(client, cache_ttl_s=0)  # type: ignore[arg-type]
    repo.list(segment=None, portfolio_id=None, max_opportunity_score=60)
    repo.count(segment=None, portfolio_id=None, max_rate_spread_bps=-10)
    list_sql, list_params = client.statements[0]
    count_sql, count_params = client.statements[1]
    assert "gold.borrower_360" in list_sql
    assert "lead_population" not in list_sql
    assert "AND b.opportunity_score <= :public_max_opportunity_score" in list_sql
    assert list_params["public_max_opportunity_score"] == 60
    assert "gold.borrower_360" in count_sql
    assert "AND b.rate_spread_bps <= :public_max_rate_spread_bps" in count_sql
    assert count_params["public_max_rate_spread_bps"] == -10


def test_floors_and_ceilings_share_one_statement_on_every_path() -> None:
    client = _Client()
    repo = DatabricksLeadRepository(client, cache_ttl_s=0)  # type: ignore[arg-type]
    kwargs: dict[str, Any] = {"segment": None, "portfolio_id": None, "state_codes": ["IL"], **EXPECTED_KWARGS}
    repo.list(**kwargs)
    repo.count(**kwargs)
    with pytest.raises(ValueError, match="no metadata"):
        # The fake answers no rows; the statement is what this test reads.
        repo.list_with_identity(**kwargs)
    assert len(client.statements) == 3
    for sql, params in client.statements:
        assert "b.opportunity_score >= :replay_min_opportunity_score" in sql
        assert "b.rate_spread_bps >= :replay_min_rate_spread_bps" in sql
        assert "b.opportunity_score <= :public_max_opportunity_score" in sql
        assert "b.rate_spread_bps <= :public_max_rate_spread_bps" in sql
        assert params["replay_min_opportunity_score"] == 70
        assert params["public_max_rate_spread_bps"] == 150
        # Bound values never reach the statement text (the freshness marker
        # is a timestamp, so match the comparison, not the bare digits).
        for value in EXPECTED_KWARGS.values():
            assert f"<= {value}" not in sql and f">= {value}" not in sql


def test_the_list_cache_key_includes_the_ceilings() -> None:
    client = _Client()
    repo = DatabricksLeadRepository(client, cache_ttl_s=60)  # type: ignore[arg-type]
    repo.list(segment=None, portfolio_id=None, max_opportunity_score=50)
    repo.list(segment=None, portfolio_id=None, max_opportunity_score=60)
    repo.list(segment=None, portfolio_id=None, max_rate_spread_bps=50)
    repo.list(segment=None, portfolio_id=None, max_opportunity_score=50)
    assert len(client.statements) == 3


def test_the_count_cache_key_includes_the_ceilings() -> None:
    client = _Client()
    repo = DatabricksLeadRepository(client, cache_ttl_s=60)  # type: ignore[arg-type]
    repo.count(segment=None, portfolio_id=None, max_opportunity_score=50)
    repo.count(segment=None, portfolio_id=None, max_opportunity_score=60)
    repo.count(segment=None, portfolio_id=None, max_rate_spread_bps=50)
    repo.count(segment=None, portfolio_id=None, max_opportunity_score=50)
    assert len(client.statements) == 3
