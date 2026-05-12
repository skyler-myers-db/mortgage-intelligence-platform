from __future__ import annotations

import json

from fastapi.testclient import TestClient

import backend.api.leads as leads_api
from backend.main import app
from backend.services.repositories import get_lead_repository


class _CaptureLeadRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> list[object]:
        self.calls.append(kwargs)
        return []


class _CohortLakebase:
    def fetchone(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object] | None:
        _ = (sql, params)
        return {
            "route_filters": json.dumps(
                {
                    "zips": ["60617", "60628"],
                    "segment_codes": ["itm"],
                    "segment_mode": "any",
                    "target_lender_ref": "Competitor B",
                    "portfolio_criteria": {
                        "occupancy": "Owner-occupied",
                        "min_equity_pct_label": "≥ 25%",
                    },
                }
            )
        }


class _CohortLakebaseNoTarget:
    def fetchone(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object] | None:
        _ = (sql, params)
        return {
            "route_filters": json.dumps(
                {
                    "zips": ["60617"],
                    "segment_codes": ["itm"],
                    "segment_mode": "any",
                }
            )
        }


class _MalformedCohortLakebase:
    def __init__(self, route_filters: object) -> None:
        self.route_filters = route_filters

    def fetchone(self, sql: str, params: dict[str, object] | None = None) -> dict[str, object] | None:
        _ = (sql, params)
        return {"route_filters": self.route_filters}


def test_lead_queue_replays_lakebase_cohort_filters_and_ignores_widening_url(
    monkeypatch,
) -> None:
    repo = _CaptureLeadRepo()
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: repo
    monkeypatch.setattr(leads_api, "get_lakebase_client", lambda: _CohortLakebase())
    try:
        response = TestClient(app).get(
            "/api/leads?cohort_id=11111111-1111-1111-1111-111111111111"
            "&zip=99999&state=WA&segment=equity&zips=99999&segment_codes=equity"
            "&target_lender_ref=Competitor%20A"
            "&geography=California&occupancy=Investor&lien_status=Open%20HELOC"
            "&lender_relationship=Competitor%20customer&product=HELOC&min_equity_pct_label=%E2%89%A5%2040%25",
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    assert response.status_code == 200
    assert repo.calls
    call = repo.calls[-1]
    assert call["zip_code"] is None
    assert call["state"] is None
    assert call["segment"] is None
    assert call["zip_codes"] == ["60617", "60628"]
    assert call["segment_codes"] == ["itm"]
    assert call["segment_mode"] == "any"
    assert call["target_lender_ref"] == "Competitor B"
    assert call["portfolio_criteria"].occupancy == "Owner-occupied"
    assert call["portfolio_criteria"].min_equity_pct_label == "≥ 25%"


def test_lead_queue_cohort_without_target_ignores_url_target_lender(
    monkeypatch,
) -> None:
    repo = _CaptureLeadRepo()
    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: repo
    monkeypatch.setattr(leads_api, "get_lakebase_client", lambda: _CohortLakebaseNoTarget())
    try:
        response = TestClient(app).get(
            "/api/leads?cohort_id=22222222-2222-2222-2222-222222222222"
            "&target_lender_ref=Competitor%20B"
            "&geography=Texas&occupancy=Investor&lien_status=Open%20HELOC",
            headers={"X-Forwarded-Email": "lo@example.com"},
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior

    assert response.status_code == 200
    assert repo.calls
    call = repo.calls[-1]
    assert call["zip_codes"] == ["60617"]
    assert call["segment_codes"] == ["itm"]
    assert call["target_lender_ref"] is None
    assert call["portfolio_criteria"].marketing_eligibility == "Eligible only"


def test_lead_queue_cohort_rejects_invalid_lakebase_route_filters(
    monkeypatch,
) -> None:
    bad_filters = [
        "{not-json",
        json.dumps({"zips": "60617"}),
        json.dumps({"zips": ["abcde"]}),
        json.dumps({"borrower_ids": ["B-11111", "raw-clip-123"]}),
        json.dumps({"segment_codes": ["itm", "equity"], "segment_mode": "bogus"}),
        json.dumps({"segment_codes": ["itm", "equity"], "segment_mode": 1}),
        json.dumps({"portfolio_criteria": "Owner-occupied"}),
        json.dumps({"portfolio_criteria": {}}),
        json.dumps({"portfolio_criteria": {"geography": "", "occupancy": ""}}),
        json.dumps(
            {
                "portfolio_criteria": {
                    "occupancy": "All",
                    "lien_status": "Any",
                    "lender_relationship": "All",
                    "product": "All products",
                    "target_lender_ref": "All",
                    "min_equity_pct_label": "Any",
                    "owner_link": "All",
                    "purchase_intent": "All",
                }
            }
        ),
        json.dumps({"portfolio_criteria": {"occupancy": "Owner-occupied", "owner_name": "Alice"}}),
        json.dumps({"portfolio_criteria": {"occupancy": "Owner-occupied", "raw_lender_name": "Acme"}}),
        json.dumps({"source": "genie"}),
    ]

    for route_filters in bad_filters:
        repo = _CaptureLeadRepo()
        prior = app.dependency_overrides.get(get_lead_repository)
        app.dependency_overrides[get_lead_repository] = lambda repo=repo: repo
        monkeypatch.setattr(
            leads_api,
            "get_lakebase_client",
            lambda route_filters=route_filters: _MalformedCohortLakebase(route_filters),
        )
        try:
            response = TestClient(app).get(
                "/api/leads?cohort_id=33333333-3333-3333-3333-333333333333",
                headers={"X-Forwarded-Email": "lo@example.com"},
            )
        finally:
            if prior is None:
                app.dependency_overrides.pop(get_lead_repository, None)
            else:
                app.dependency_overrides[get_lead_repository] = prior

        assert response.status_code == 422, route_filters
        assert repo.calls == []
