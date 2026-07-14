import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_decision_inputs import (
    DECISION_INPUT_KEYS,
    decision_inputs_from_offer_inputs,
)
from backend.services.audit_store import get_audit_store
from backend.services.lakebase import LakebaseError
from backend.services.repositories import get_offer_repository
from backend.services.repositories.databricks_offers import DatabricksOfferRepository
from tests.fixtures import mock_population
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

client = TestClient(app)


def _valid_databricks_offer_row() -> dict[str, object]:
    return {
        "clip": "clip-1",
        "borrower_id": "B-48291",
        "confidence": 80,
        "evidence_ids": ["E-1"],
        "refreshed_at": "2026-04-20T06:12:00Z",
        "rate_spread_bps": 100,
        "equity_pct": 40,
        "has_permit": False,
        "has_heloc_propensity_trigger": True,
        "heloc_propensity_score": 700,
        "has_refi_propensity_trigger": True,
        "refi_propensity_score": 700,
        "listed_for_sale": False,
        "is_investor": False,
        "is_current_customer": False,
        "is_competitor_lien": True,
        "recommended_offer_code": "refi_plus_heloc",
        "min_spread_bps_applied": 75,
        "min_equity_pct_applied": 15,
        "heloc_equity_min_applied": 35,
        "cashout_equity_min_applied": 25,
        "retention_min_spread_applied": 50,
    }


class _OneRowClient:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def execute_one(self, statement: str, params: dict[str, object]) -> dict[str, object]:
        del statement, params
        return self.row


def test_databricks_offer_repository_rejects_unknown_offer_code() -> None:
    row = {**_valid_databricks_offer_row(), "recommended_offer_code": "mystery"}
    repo = DatabricksOfferRepository(_OneRowClient(row))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="recommended_offer_code"):
        repo.get_offer_inputs("B-48291")


def test_databricks_offer_repository_rejects_missing_applied_threshold() -> None:
    row = {**_valid_databricks_offer_row(), "min_spread_bps_applied": None}
    repo = DatabricksOfferRepository(_OneRowClient(row))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="min_spread_bps_applied"):
        repo.get_offer_inputs("B-48291")


def test_offer_recommendation_fails_closed_when_audit_is_unavailable() -> None:
    class FailingAuditStore:
        def write(self, **kwargs: object) -> None:
            raise LakebaseError("SQL failed for subject_clip=1234567890 with host=db.internal")

    previous = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = FailingAuditStore
    try:
        response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous

    assert response.status_code == 503
    assert response.json()["detail"] == "lakebase is temporarily unavailable"
    assert "subject_clip" not in response.text
    assert "db.internal" not in response.text


def test_offers_router_recommends_governed_offer() -> None:
    response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})
    assert response.status_code == 200
    body = response.json()
    assert body["borrower_id"] == "B-48291"
    assert body["offer_code"]
    assert body["sources"]
    assert body["thresholds_applied"]


def test_offers_router_returns_404_for_unknown_borrower() -> None:
    response = client.post("/api/offers/recommend", json={"borrower_id": "B-0000000000000"})
    assert response.status_code == 404


def test_offers_router_sanitizes_invalid_governed_inputs() -> None:
    class InvalidOfferRepository:
        def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
            del borrower_id
            raise ValueError("raw bad value from mip.gold.borrower_dossier")

    previous = app.dependency_overrides.get(get_offer_repository)
    app.dependency_overrides[get_offer_repository] = InvalidOfferRepository
    try:
        response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_offer_repository, None)
        else:
            app.dependency_overrides[get_offer_repository] = previous

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Offer inputs are incomplete or invalid; refresh the governed data before retrying."
    )
    assert "borrower_dossier" not in response.text


def test_offers_router_uses_refresh_applied_thresholds_from_offer_inputs() -> None:
    class ThresholdStubOfferRepository:
        def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
            if borrower_id != "B-48291":
                return None
            return {
                "clip_id": "CLIP-48291",
                "borrower_id": "B-48291",
                "confidence": 82,
                "evidence_ids": ["E-48291-1"],
                "source_refreshed_at": "2026-04-20T06:12:00Z",
                "rate_spread_bps": 88,
                "equity_pct": 39,
                "has_permit": False,
                "listed_for_sale": False,
                "is_investor": False,
                "is_current_customer": False,
                "is_competitor_lien": False,
                "offer_code": "refi",
                "min_spread_bps": 88,
                "min_equity_pct": 11,
                "heloc_equity_min_pct": 42,
                "cashout_equity_min_pct": 29,
                "retention_min_spread_bps": 61,
            }

    app.dependency_overrides[get_offer_repository] = ThresholdStubOfferRepository

    response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})

    assert response.status_code == 200
    body = response.json()
    assert body["thresholds_applied"] == {
        "min_spread_bps": 88,
        "min_equity_pct": 11,
        "heloc_equity_min_pct": 42,
        "cashout_equity_min_pct": 29,
        "retention_min_spread_bps": 61,
    }
    assert body["offer_code"] == "refi"
    assert body["alternatives"][0]["reason_not_chosen"] == (
        "Equity 39% is below the HELOC threshold (42%), so the primary review stays refinance-only."
    )


def test_recommend_offer_audit_captures_decision_inputs() -> None:
    audit = InMemoryAuditStore()
    previous = app.dependency_overrides.get(get_audit_store)
    app.dependency_overrides[get_audit_store] = lambda: audit
    try:
        response = client.post(
            "/api/offers/recommend",
            json={"borrower_id": "B-48291"},
            headers={"X-Correlation-ID": "forensic-offer-audit"},
        )
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_audit_store, None)
        else:
            app.dependency_overrides[get_audit_store] = previous

    assert response.status_code == 200, response.text
    events = audit.list(limit=10, event_type="RECOMMEND_OFFER")
    assert len(events) == 1
    metadata = events[0].payload_json
    assert events[0].correlation_id == response.headers["X-Correlation-ID"]
    assert set(metadata["decision_inputs"]) == set(DECISION_INPUT_KEYS)
    expected = mock_population.BORROWER_OFFER_INPUTS["B-48291"]
    assert metadata["decision_inputs"] == decision_inputs_from_offer_inputs(expected)
