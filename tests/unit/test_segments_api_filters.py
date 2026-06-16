from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.lead import SegmentSummary
from backend.services.repositories import get_segment_repository


class _CaptureSegmentRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> list[SegmentSummary]:
        self.calls.append(kwargs)
        return [
            SegmentSummary(
                code="itm",
                name="Prime Refi Candidates",
                count=1,
                delta="+0%",
                avg_score=80,
                description="test",
                color="#22d3ee",
            )
        ]


def test_segments_api_default_uses_unfiltered_gold_rollup() -> None:
    repo = _CaptureSegmentRepo()
    prior = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: repo
    try:
        response = TestClient(app).get("/api/segments")
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_repository, None)
        else:
            app.dependency_overrides[get_segment_repository] = prior

    assert response.status_code == 200
    assert repo.calls
    call = repo.calls[-1]
    assert call["segment_codes"] is None
    assert call["segment_mode"] == "any"
    assert call["portfolio_criteria"] is None


def test_segments_api_passes_segment_and_portfolio_filters_to_repository() -> None:
    repo = _CaptureSegmentRepo()
    prior = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: repo
    try:
        response = TestClient(app).get(
            "/api/segments?segment_codes=itm,equity&segment_mode=all"
            "&occupancy=Owner-occupied&lien_status=Open%20HELOC"
            "&min_equity_pct_label=%E2%89%A5%2025%25",
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_repository, None)
        else:
            app.dependency_overrides[get_segment_repository] = prior

    assert response.status_code == 200
    assert repo.calls
    call = repo.calls[-1]
    assert call["segment_codes"] == ["itm", "equity"]
    assert call["segment_mode"] == "all"
    criteria = call["portfolio_criteria"]
    assert criteria.occupancy == "Owner-occupied"
    assert criteria.lien_status == "Open HELOC"
    assert criteria.min_equity_pct_label == "≥ 25%"
    assert criteria.marketing_eligibility == "Any"


def test_segments_api_preserves_explicit_marketing_eligibility_filter() -> None:
    repo = _CaptureSegmentRepo()
    prior = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: repo
    try:
        response = TestClient(app).get(
            "/api/segments?marketing_eligibility=Eligible%20only",
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_repository, None)
        else:
            app.dependency_overrides[get_segment_repository] = prior

    assert response.status_code == 200
    assert repo.calls
    criteria = repo.calls[-1]["portfolio_criteria"]
    assert criteria.marketing_eligibility == "Eligible only"
