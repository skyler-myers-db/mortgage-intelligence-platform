"""Route contract for ``GET /api/v1/analytics/rate-window`` (dataviz-08).

The repository is replaced through ``app.dependency_overrides`` so nothing
here touches a warehouse. Pins:

* the response shape and the evidence manifest (provenance) citing BOTH
  source tables plus the gold table the endpoint reads;
* the honest degraded state: a cold warehouse surfaces as the resilience
  layer's 503 ``warming_up`` body, never as an empty series;
* the deprecated unversioned alias still answers.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.analytics_rate_window import (
    RateWindowProvenance,
    RateWindowResponse,
    RateWindowThresholds,
    RateWindowWeek,
)
from backend.services.repositories import get_rate_window_repository
from backend.services.resilience import DependencyDownError

RATE_WINDOW_PATH = "/api/v1/analytics/rate-window"


def _response() -> RateWindowResponse:
    return RateWindowResponse(
        series_id="MORTGAGE30US",
        weeks=[
            RateWindowWeek(
                week="2026-04-06",
                market_rate_pct=6.37,
                book_median_pct=7.1,
                book_p25_pct=6.55,
                book_p75_pct=7.62,
                itm_count=1180,
            ),
            RateWindowWeek(
                week="2026-04-13",
                market_rate_pct=6.30,
                book_median_pct=7.1,
                book_p25_pct=6.55,
                book_p75_pct=7.62,
                itm_count=1204,
                is_latest=True,
            ),
        ],
        book_lien_count=48210,
        book_as_of="2026-04-16 06:00:00",
        thresholds=RateWindowThresholds(min_spread_bps=75, min_equity_pct=15),
        provenance=RateWindowProvenance(
            market_rate_source="mip.silver.market_rates_weekly",
            book_source="mip.gold.borrower_360 + mip.silver.lien_current",
            gold_source="mip.gold.rate_window_weekly",
            rule_source="mip.gold.fn_rate_spread + mip.gold.fn_in_the_money",
            book_as_of="2026-04-16 06:00:00",
            refreshed_at="2026-04-16 06:00:00",
            note="Today's book against the historical rate.",
        ),
    )


class _StubRateWindowRepo:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def rate_window(self) -> RateWindowResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _response()


@pytest.fixture
def stub_repo() -> Iterator[_StubRateWindowRepo]:
    repo = _StubRateWindowRepo()
    prior = app.dependency_overrides.get(get_rate_window_repository)
    app.dependency_overrides[get_rate_window_repository] = lambda: repo
    try:
        yield repo
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_rate_window_repository, None)
        else:
            app.dependency_overrides[get_rate_window_repository] = prior


def test_rate_window_returns_the_series_and_its_evidence_manifest(stub_repo: _StubRateWindowRepo) -> None:
    response = TestClient(app).get(RATE_WINDOW_PATH)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "series_id",
        "weeks",
        "book_lien_count",
        "book_as_of",
        "thresholds",
        "provenance",
    }
    assert body["series_id"] == "MORTGAGE30US"
    assert [week["week"] for week in body["weeks"]] == ["2026-04-06", "2026-04-13"]
    assert set(body["weeks"][0]) == {
        "week",
        "market_rate_pct",
        "book_median_pct",
        "book_p25_pct",
        "book_p75_pct",
        "itm_count",
        "is_latest",
    }
    assert body["weeks"][-1]["is_latest"] is True
    assert body["thresholds"] == {"min_spread_bps": 75, "min_equity_pct": 15}
    # The evidence manifest names both source tables and the gold table.
    provenance = body["provenance"]
    assert provenance["market_rate_source"] == "mip.silver.market_rates_weekly"
    assert provenance["gold_source"] == "mip.gold.rate_window_weekly"
    assert "mip.gold.borrower_360" in provenance["book_source"]
    assert "fn_in_the_money" in provenance["rule_source"]
    assert provenance["book_as_of"] == body["book_as_of"]
    assert stub_repo.calls == 1


def test_rate_window_cold_warehouse_is_an_honest_warming_up_503() -> None:
    repo = _StubRateWindowRepo(
        error=DependencyDownError(
            "warehouse",
            reason="statement timed out while the warehouse started",
            kind=DependencyDownError.KIND_WARMING_UP,
        ),
    )
    prior = app.dependency_overrides.get(get_rate_window_repository)
    app.dependency_overrides[get_rate_window_repository] = lambda: repo
    try:
        response = TestClient(app).get(RATE_WINDOW_PATH)
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_rate_window_repository, None)
        else:
            app.dependency_overrides[get_rate_window_repository] = prior

    assert response.status_code == 503
    body = response.json()
    assert body["retryable"] is True
    assert body["dependency"] == "warehouse"
    assert body["reason"] == "warming_up"
    # No fabricated series rides along with the degraded body.
    assert "weeks" not in body
    # R5-02: the upstream error text never reaches the wire.
    assert "timed out" not in body["detail"]


def test_rate_window_unversioned_alias_still_answers(stub_repo: _StubRateWindowRepo) -> None:
    response = TestClient(app).get("/api/analytics/rate-window")

    assert response.status_code == 200
    assert response.json()["series_id"] == "MORTGAGE30US"
