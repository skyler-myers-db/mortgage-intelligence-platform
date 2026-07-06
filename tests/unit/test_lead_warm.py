"""Cache-key parity between the lead warmer and the real route (audit P1-6).

The whole value of ``backend.services.lead_warm`` is that the startup /
refresh-ahead warm populates the EXACT cache entries ``GET /api/leads``
reads for a default request. If a route default drifts (limit, segment
mode, a new filter param), the warm would silently heat a dead key and the
booth would eat the 3.6-6.6s cold query again. These tests pin parity by
warming through a counting fake SQL client and then driving the real
route: the route must add ZERO SQL statements.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.leads import DEFAULT_LEAD_LIMIT
from backend.main import app
from backend.services import lead_warm
from backend.services.repositories import get_lead_repository
from backend.services.repositories.databricks_repo import DatabricksLeadRepository


class _CountingClient:
    """Returns empty result sets; records every executed statement."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
        self.statements.append(sql)
        if "COUNT(*)" in sql:
            return [{"n": 0}]
        return []

    def execute_one(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object] | None:
        self.statements.append(sql)
        if "COUNT(*)" in sql:
            return {"n": 0}
        return None


def test_warm_limit_matches_route_default_limit() -> None:
    """Layering keeps lead_warm from importing backend.api — pin equality."""
    assert lead_warm.DEFAULT_LEAD_PAGE_LIMIT == DEFAULT_LEAD_LIMIT


def test_route_default_request_hits_warmed_cache() -> None:
    client_fake = _CountingClient()
    repo = DatabricksLeadRepository(client_fake, cache_ttl_s=300.0)  # type: ignore[arg-type]

    warmed = lead_warm.warm_default_lead_page(repo)
    statements_after_warm = len(client_fake.statements)
    assert warmed == {"leads": 0, "total": 0}
    assert statements_after_warm == 2  # one list + one count

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: repo
    try:
        http = TestClient(app)
        # The hero routes omit `limit` (server default) — and an explicit
        # limit=500 must land on the same key. Both must be pure cache hits.
        assert http.get("/api/leads").status_code == 200
        assert http.get("/api/leads?limit=500").status_code == 200
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    extra = client_fake.statements[statements_after_warm:]
    assert extra == [], (
        "route default request missed the warmed cache — warm kwargs have "
        f"drifted from the route defaults; extra statements: {extra}"
    )


def test_rewarm_interval_defaults_to_disabled_for_idle_cost_control() -> None:
    """Refresh-ahead must be opt-in so idle Apps can auto-stop."""
    from backend.config.settings import Settings

    fresh = Settings(_env_file=None)
    assert fresh.mip_leads_warm_interval_s == 0


def test_positive_rewarm_interval_stays_below_cache_ttl() -> None:
    """When enabled, refresh-ahead only works if warm cadence beats expiry."""
    from backend.config.settings import Settings

    fresh = Settings(_env_file=None, mip_leads_warm_interval_s=120)
    assert 0 < fresh.mip_leads_warm_interval_s < fresh.mip_cache_ttl_s
