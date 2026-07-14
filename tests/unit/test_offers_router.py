from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.schemas.offer import OfferRecommendation
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
        "evidence_ids": ["ev-001"],
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


def _valid_router_offer_inputs() -> dict[str, object]:
    return {
        **mock_population.BORROWER_OFFER_INPUTS["B-48291"],
        "clip_id": "CLIP-48291",
        "borrower_id": "B-48291",
        "confidence": 82,
        "evidence_ids": ["ev-482911"],
        "source_refreshed_at": "2026-04-20T06:12:00Z",
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("has_permit", None),
        ("has_heloc_propensity_trigger", "false"),
        ("has_refi_propensity_trigger", 1),
        ("listed_for_sale", 0),
        ("is_investor", "true"),
        ("is_current_customer", None),
        ("is_competitor_lien", "no"),
    ],
)
def test_databricks_offer_repository_rejects_non_boolean_governed_flags(
    field: str,
    value: object,
) -> None:
    row = {**_valid_databricks_offer_row(), field: value}
    repo = DatabricksOfferRepository(_OneRowClient(row))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match=field):
        repo.get_offer_inputs("B-48291")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_ids", []),
        ("evidence_ids", ["ev-001", "raw borrower evidence"]),
        ("evidence_ids", [f"ev-{'a' * 62}"]),
        ("refreshed_at", "not-a-timestamp"),
        ("refreshed_at", "2026-04-20T06:12:00"),
        ("refreshed_at", None),
        ("refreshed_at", "2999-04-20T06:12:00Z"),
    ],
)
def test_databricks_offer_repository_rejects_invalid_recommendation_proof(
    field: str,
    value: object,
) -> None:
    row = {**_valid_databricks_offer_row(), field: value}
    repo = DatabricksOfferRepository(_OneRowClient(row))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="evidence_ids|source_refreshed_at"):
        repo.get_offer_inputs("B-48291")


def _valid_offer_recommendation_payload() -> dict[str, object]:
    return {
        "borrower_id": "B-48291",
        "source_refreshed_at": "2026-04-20T06:12:00Z",
        "offer_code": "refi",
        "offer_type": "refi",
        "product_label": "Refinance",
        "confidence": 80,
        "rationale": "Reviewed rate and equity signals support a refinance review.",
        "evidence_ids": ["ev-001"],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_ids", []),
        ("evidence_ids", ["ev-001", "../unbounded"]),
        ("source_refreshed_at", "2026-04-20T06:12:00"),
        ("source_refreshed_at", "tomorrow"),
        ("source_refreshed_at", None),
        ("source_refreshed_at", "2999-04-20T06:12:00Z"),
    ],
)
def test_offer_recommendation_schema_rejects_invalid_proof(
    field: str,
    value: object,
) -> None:
    payload = {**_valid_offer_recommendation_payload(), field: value}

    with pytest.raises(ValidationError):
        OfferRecommendation.model_validate(payload)


def test_offer_recommendation_schema_allows_small_source_clock_skew() -> None:
    payload = {
        **_valid_offer_recommendation_payload(),
        "source_refreshed_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }

    recommendation = OfferRecommendation.model_validate(payload)

    assert recommendation.source_refreshed_at == payload["source_refreshed_at"]


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_ids", []),
        ("evidence_ids", ["ev-001", "invalid/evidence"]),
        ("source_refreshed_at", "2026-04-20T06:12:00"),
        ("source_refreshed_at", "not-a-timestamp"),
        ("source_refreshed_at", None),
        ("source_refreshed_at", "2999-04-20T06:12:00Z"),
    ],
)
def test_offers_router_rejects_invalid_proof_before_audit(
    field: str,
    value: object,
) -> None:
    class InvalidProofOfferRepository:
        def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
            if borrower_id != "B-48291":
                return None
            return {
                "clip_id": "CLIP-48291",
                "borrower_id": borrower_id,
                "confidence": 82,
                "evidence_ids": ["ev-001"],
                "source_refreshed_at": "2026-04-20T06:12:00Z",
                "rate_spread_bps": 88,
                "equity_pct": 39,
                "has_permit": False,
                "listed_for_sale": False,
                "is_investor": False,
                "is_current_customer": False,
                "is_competitor_lien": False,
                "offer_code": "refi",
                "min_spread_bps": 75,
                "min_equity_pct": 15,
                "heloc_equity_min_pct": 35,
                "cashout_equity_min_pct": 25,
                "retention_min_spread_bps": 50,
                field: value,
            }

    audit = InMemoryAuditStore()
    app.dependency_overrides[get_offer_repository] = InvalidProofOfferRepository
    app.dependency_overrides[get_audit_store] = lambda: audit

    response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Offer inputs are incomplete or invalid; refresh the governed data before retrying."
    )
    assert audit.list(limit=10, event_type="RECOMMEND_OFFER") == []


def test_offers_router_uses_refresh_applied_thresholds_from_offer_inputs() -> None:
    class ThresholdStubOfferRepository:
        def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
            if borrower_id != "B-48291":
                return None
            return {
                **_valid_router_offer_inputs(),
                "offer_code": "refi",
                "rate_spread_bps": 88,
                "equity_pct": 39,
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", 101),
        ("confidence", "82"),
        ("has_permit", "false"),
        ("has_heloc_propensity_trigger", 0),
        ("has_refi_propensity_trigger", None),
        ("listed_for_sale", "true"),
        ("is_investor", 1),
        ("is_current_customer", "no"),
        ("is_competitor_lien", None),
        ("borrower_id", "B-48294"),
        ("source_refreshed_at", "2999-04-20T06:12:00Z"),
    ],
)
def test_offers_router_rejects_malformed_complete_recommendation_before_audit(
    field: str,
    value: object,
) -> None:
    class MalformedOfferRepository:
        def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
            if borrower_id != "B-48291":
                return None
            return {**_valid_router_offer_inputs(), field: value}

    audit = InMemoryAuditStore()
    app.dependency_overrides[get_offer_repository] = MalformedOfferRepository
    app.dependency_overrides[get_audit_store] = lambda: audit

    response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})

    assert response.status_code == 500
    assert audit.list(limit=10, event_type="RECOMMEND_OFFER") == []


def test_offer_rationale_keeps_permit_separate_from_heloc_propensity() -> None:
    class PermitOfferRepository:
        def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
            if borrower_id != "B-48291":
                return None
            return {
                **_valid_router_offer_inputs(),
                "offer_code": "heloc",
                "has_permit": True,
                "has_heloc_propensity_trigger": False,
                "heloc_propensity_score": None,
                "rate_spread_bps": 20,
                "equity_pct": 45,
            }

    app.dependency_overrides[get_offer_repository] = PermitOfferRepository

    response = client.post("/api/offers/recommend", json={"borrower_id": "B-48291"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "Filed permit activity" in body["rationale"]
    assert "Cotality HELOC propensity" not in body["rationale"]
    assert not any("heloc_propensity" in source for source in body["sources"])


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
