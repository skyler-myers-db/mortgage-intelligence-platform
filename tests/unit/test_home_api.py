"""Route contract for ``GET /api/home/summary`` (S4).

The session conftest wires the endpoint to a real ``HomeSummaryService``
over the shared fake Lakebase (no visit rows) and a zero-row headline SQL
stub, so the default response is the honest first-visit welcome. Tests
that need the delta shape layer their own dependency override, exactly
like the other routers.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.kpi_deltas import HeadlineKpis, KpiDeltaResult, KpiDeltas
from backend.services.home_summary import HomeSummaryService, get_home_summary_service
from backend.services.lakebase import LakebaseError

client = TestClient(app)

_CURRENT = HeadlineKpis(
    marketable_population=5_240_100,
    refi_economics_screen=261_400,
    high_opportunity=88_210,
    offers_available=402_330,
    offers_recommended=310_450,
    avg_opportunity_score=61.5,
)


class _StubDeltas:
    def __init__(self, result: KpiDeltaResult | Exception) -> None:
        self.result = result
        self.actors: list[str] = []

    def deltas_for_actor(self, actor_email: str) -> KpiDeltaResult:
        self.actors.append(actor_email)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _install(stub: _StubDeltas) -> None:
    # The autouse ``_isolate_fastapi_dependency_state`` fixture restores the
    # session baseline overrides after every test, so this per-test layer
    # needs no explicit teardown.
    service = HomeSummaryService(delta_service=stub, spawn=lambda work: None)
    app.dependency_overrides[get_home_summary_service] = lambda: service


def test_summary_defaults_to_first_visit_welcome() -> None:
    res = client.get("/api/home/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "first_visit"
    assert body["previous_visit_at"] is None
    assert body["deltas"] is None
    assert body["phrasing_source"] == "deterministic"
    assert body["headline"].startswith("Welcome")
    assert body["current_source"] == "mip.semantics.portfolio_headline_metric_view"
    assert body["baseline_source"] == "mip_app.kpi_snapshots"


def test_summary_delta_shape_and_actor_resolution() -> None:
    previous = datetime(2026, 7, 9, 14, 30, 0, tzinfo=UTC)
    stub = _StubDeltas(
        KpiDeltaResult(
            actor_email="growth@summit.example",
            previous_visit_at=previous,
            baseline_snapshot_at=datetime(2026, 7, 9, 6, 0, 0, tzinfo=UTC),
            current=_CURRENT,
            baseline=_CURRENT.model_copy(
                update={"high_opportunity": 86_900, "refi_economics_screen": 259_150}
            ),
            deltas=KpiDeltas(
                marketable_population=0,
                refi_economics_screen=2_250,
                high_opportunity=1_310,
                offers_available=0,
                offers_recommended=0,
                avg_opportunity_score=None,
            ),
        )
    )
    _install(stub)
    res = client.get(
        "/api/home/summary",
        headers={"X-Forwarded-Email": "growth@summit.example"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "delta"
    assert [h["display"] for h in body["highlights"]] == ["+1.5%", "+2,250", "0"]
    assert body["headline"] == (
        "Since your last login: +1.5% high-opportunity, "
        "+2,250 refi candidates, 0 offers available."
    )
    # The resolved forwarded identity is what anchors the delta lookup.
    assert stub.actors == ["growth@summit.example"]
    # The actor's email is not reflected back in the payload.
    assert "growth@summit.example" not in res.text


def test_summary_maps_lakebase_outage_to_sanitized_503() -> None:
    _install(_StubDeltas(LakebaseError("connection refused: 10.0.0.7")))
    res = client.get("/api/home/summary")
    assert res.status_code == 503
    detail = res.json()["detail"]
    assert "lakebase" in detail.lower()
    assert "10.0.0.7" not in detail
