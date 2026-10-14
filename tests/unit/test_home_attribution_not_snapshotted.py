"""A measure the funnel snapshot does not attribute per state answers ``snapshotted=False``.

Audit 2026-09-21 wow-ai-3 (the W5b deferral). ``competitor_lien`` is a "since
your last login" number whose per-state column is not in
``gold.funnel_snapshot_daily`` yet. The route answers it with
``snapshotted: false`` and runs no SQL, no cache and no audit; the drawer's
note is driven by that field, never by the measure name. The moment
``MEASURE_COLUMNS`` gains the measure (w5-gold-slot-cache), the route stops
short-circuiting and the service answers, with no route or UI edit. The
not-snapshotted cases run with the entry removed, so they stay green after
that lane merges.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.home_attribution import HomeAttributionRate, HomeSummaryAttributionResponse
from backend.services import audit_store as audit_store_module
from backend.services import home_attribution
from backend.services.audit_store import get_audit_store
from backend.services.home_attribution import get_home_attribution_service
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date]] = []

    def attribution(self, measure: str, baseline: date) -> HomeSummaryAttributionResponse:
        self.calls.append((measure, baseline))
        return HomeSummaryAttributionResponse(
            measure=measure,  # type: ignore[arg-type]
            label="competitor liens",
            requested_baseline_date=baseline,
            rate=HomeAttributionRate(series_id="MORTGAGE30US"),
        )


@pytest.fixture()
def audit(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryAuditStore]:
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    monkeypatch.setattr(audit_store_module, "_AUDIT_STORE", store)
    yield store


@pytest.fixture()
def service() -> _RecordingService:
    recording = _RecordingService()
    app.dependency_overrides[get_home_attribution_service] = lambda: recording
    return recording


def _baseline() -> str:
    return (datetime.now(UTC).date() - timedelta(days=30)).isoformat()


def _get(measure: str) -> Any:
    return TestClient(app).get(f"/api/v1/home/summary/attribution?measure={measure}&baseline={_baseline()}")


def test_competitor_lien_answers_not_snapshotted_with_no_read(
    monkeypatch: pytest.MonkeyPatch,
    audit: InMemoryAuditStore,
    service: _RecordingService,
) -> None:
    monkeypatch.delitem(home_attribution.MEASURE_COLUMNS, "competitor_lien", raising=False)

    response = _get("competitor_lien")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["snapshotted"] is False
    assert body["measure"] == "competitor_lien"
    assert body["label"] == "competitor liens"
    assert body["requested_baseline_date"] == _baseline()
    assert (body["baseline_total"], body["current_total"], body["total_change"]) == (None, None, None)
    assert body["states"] == []
    assert body["rate"]["series_id"] == "MORTGAGE30US"
    assert service.calls == []
    assert audit.list(limit=100) == []


def test_the_baseline_window_still_applies_first(
    monkeypatch: pytest.MonkeyPatch,
    service: _RecordingService,
) -> None:
    monkeypatch.delitem(home_attribution.MEASURE_COLUMNS, "competitor_lien", raising=False)
    future = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()

    response = TestClient(app).get(f"/api/v1/home/summary/attribution?measure=competitor_lien&baseline={future}")

    assert response.status_code == 422


def test_once_the_snapshot_carries_the_column_the_service_answers(
    monkeypatch: pytest.MonkeyPatch,
    service: _RecordingService,
) -> None:
    monkeypatch.setitem(
        home_attribution.MEASURE_COLUMNS,
        "competitor_lien",
        ("_ALL", "competitor_lien_borrowers", "competitor liens"),
    )

    response = _get("competitor_lien")

    assert response.status_code == 200
    assert response.json()["snapshotted"] is True
    assert [measure for measure, _ in service.calls] == ["competitor_lien"]


@pytest.mark.parametrize("measure", ["refi_economics_screen", "high_opportunity", "offers_recommended", "listed_for_sale"])
def test_a_snapshotted_measure_reaches_the_service(measure: str, service: _RecordingService) -> None:
    response = _get(measure)

    assert response.status_code == 200
    assert response.json()["snapshotted"] is True
    assert [called for called, _ in service.calls] == [measure]
