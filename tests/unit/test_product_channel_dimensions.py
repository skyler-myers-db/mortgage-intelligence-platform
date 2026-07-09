"""S1.6 product-type + origination-channel dimension tests.

Covers, in order:

1. ``loan_product_type`` Python/SQL parity pinned by the golden fixture
   (``tests/fixtures/loan_product_type_golden.json``) plus the SQL-side
   validation file staying in sync.
2. ``PortfolioCriteria`` reviewed-label validation for the two new filter
   dimensions (including snake_case aliases used in deep links).
3. ``build_preview_predicates`` SQL clause generation, including the
   NULL-bucket "Unknown" semantics.
4. Router passthrough: /api/segments and /api/leads accept the new query
   params and deliver them to the repository as validated criteria.
5. Gold SQL contract: the CTAS files carry the new columns and the evidence
   rows stay explainability-only (excluded from the evidence sub-score).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.lead import DimensionFacetCount, LeadSummary, SegmentSummary
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.repositories import get_segment_repository
from backend.services.repositories.databricks_portfolio import build_preview_predicates
from backend.services.scoring import (
    LOAN_PRODUCT_TYPES,
    ORIGINATION_CHANNELS,
    loan_product_type,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = REPO_ROOT / "tests" / "fixtures" / "loan_product_type_golden.json"
VALIDATION_SQL_PATH = REPO_ROOT / "sql" / "fixtures" / "loan_product_type_validation.sql"
TRANSFORM_DIR = REPO_ROOT / "sql" / "transformations"

with GOLDEN_PATH.open() as f:
    GOLDEN = json.load(f)


# ---------------------------------------------------------------------------
# 1. Scoring parity: loan_product_type golden fixture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["id"] for c in GOLDEN["cases"]])
def test_product_channel_loan_product_type_matches_golden_fixture(case: dict) -> None:
    assert loan_product_type(**case["inputs"]) == case["expected_product_type"], case.get(
        "note", ""
    )


def test_product_channel_golden_fixture_and_validation_sql_stay_in_sync() -> None:
    """Every golden case id must appear in the SQL validation file, and the
    governed default limit must match the offer_rules_config seed."""
    validation_sql = VALIDATION_SQL_PATH.read_text(encoding="utf-8")
    for case in GOLDEN["cases"]:
        assert case["id"] in validation_sql, f"{case['id']} missing from validation SQL"
    assert GOLDEN["default_thresholds"]["conforming_limit_usd"] == 806500
    seed_sql = (REPO_ROOT / "sql" / "ref" / "offer_rules_config_seed.sql").read_text(
        encoding="utf-8"
    )
    assert "'mip_conforming_loan_limit_usd', 806500.0" in seed_sql


def test_product_channel_vocab_constants_cover_fixture_outputs() -> None:
    expected = {c["expected_product_type"] for c in GOLDEN["cases"]} - {None}
    assert expected <= set(LOAN_PRODUCT_TYPES)
    assert ORIGINATION_CHANNELS == ("loan_officer", "digital", "branch", "call_center")


# ---------------------------------------------------------------------------
# 2. PortfolioCriteria reviewed-label validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Conventional", "Conventional"),
        ("Jumbo", "Jumbo"),
        ("FHA", "FHA"),
        ("VA", "VA"),
        ("Other", "Other"),
        ("Unknown", "Unknown"),
        ("conventional", "Conventional"),  # snake/lower alias
        ("jumbo", "Jumbo"),
        ("all", "All loan products"),
    ],
)
def test_product_channel_loan_product_labels_normalize(raw: str, expected: str) -> None:
    assert PortfolioCriteria(loan_product=raw).loan_product == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Loan officer", "Loan officer"),
        ("Digital", "Digital"),
        ("Branch", "Branch"),
        ("Call center", "Call center"),
        ("Unknown", "Unknown"),
        ("loan_officer", "Loan officer"),
        ("call_center", "Call center"),
        ("all", "All channels"),
    ],
)
def test_product_channel_origination_channel_labels_normalize(raw: str, expected: str) -> None:
    assert PortfolioCriteria(origination_channel=raw).origination_channel == expected


@pytest.mark.parametrize(
    "field", ["loan_product", "origination_channel"],
)
def test_product_channel_unreviewed_labels_rejected(field: str) -> None:
    with pytest.raises(ValueError):
        PortfolioCriteria(**{field: "definitely-not-reviewed"})


def test_product_channel_filters_count_as_effective_predicates() -> None:
    assert PortfolioCriteria(loan_product="Jumbo").has_effective_predicate(
        count_default_marketing=False
    )
    assert PortfolioCriteria(origination_channel="Digital").has_effective_predicate(
        count_default_marketing=False
    )
    assert not PortfolioCriteria(
        loan_product="All loan products",
        origination_channel="All channels",
        marketing_eligibility="Any",
    ).has_effective_predicate(count_default_marketing=False)


# ---------------------------------------------------------------------------
# 3. SQL predicate generation
# ---------------------------------------------------------------------------


def _predicates(criteria: PortfolioCriteria) -> tuple[str, dict[str, object]]:
    return build_preview_predicates(criteria, state_sets={})


def test_product_channel_loan_product_predicate_parameterized() -> None:
    where, params = _predicates(
        PortfolioCriteria(loan_product="Jumbo", marketing_eligibility="Any")
    )
    assert "loan_product_type = :loan_product_type" in where
    assert params["loan_product_type"] == "jumbo"


def test_product_channel_origination_channel_predicate_parameterized() -> None:
    where, params = _predicates(
        PortfolioCriteria(origination_channel="Call center", marketing_eligibility="Any")
    )
    assert "origination_channel = :origination_channel" in where
    assert params["origination_channel"] == "call_center"


def test_product_channel_unknown_buckets_match_null() -> None:
    where, params = _predicates(
        PortfolioCriteria(
            loan_product="Unknown",
            origination_channel="Unknown",
            marketing_eligibility="Any",
        )
    )
    assert "loan_product_type IS NULL" in where
    assert "origination_channel IS NULL" in where
    assert "loan_product_type" not in params
    assert "origination_channel" not in params


def test_product_channel_all_labels_produce_no_predicate() -> None:
    where, params = _predicates(
        PortfolioCriteria(
            loan_product="All loan products",
            origination_channel="All channels",
            marketing_eligibility="Any",
        )
    )
    assert "loan_product_type" not in where
    assert "origination_channel" not in where
    assert not params


# ---------------------------------------------------------------------------
# 4. Router passthrough (segments + leads)
# ---------------------------------------------------------------------------


class _CaptureSegmentRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list(self, **kwargs: object) -> list[SegmentSummary]:
        self.calls.append(kwargs)
        return [
            SegmentSummary(
                code="itm",
                name="Prime Refi",
                count=1,
                delta="+0%",
                avg_score=80,
                description="test",
                color="#22d3ee",
                loan_product_mix=[DimensionFacetCount(value="conventional", count=1)],
                origination_channel_mix=[DimensionFacetCount(value="unknown", count=1)],
            )
        ]


def test_product_channel_segments_api_passes_filters_to_repository() -> None:
    repo = _CaptureSegmentRepo()
    prior = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: repo
    try:
        response = TestClient(app).get(
            "/api/segments?loan_product=jumbo&origination_channel=loan_officer",
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_repository, None)
        else:
            app.dependency_overrides[get_segment_repository] = prior
    assert response.status_code == 200
    criteria = repo.calls[-1]["portfolio_criteria"]
    assert isinstance(criteria, PortfolioCriteria)
    assert criteria.loan_product == "Jumbo"
    assert criteria.origination_channel == "Loan officer"
    body = response.json()[0]
    assert body["loan_product_mix"] == [{"value": "conventional", "count": 1}]
    assert body["origination_channel_mix"] == [{"value": "unknown", "count": 1}]


def test_product_channel_segments_api_rejects_unreviewed_label() -> None:
    repo = _CaptureSegmentRepo()
    prior = app.dependency_overrides.get(get_segment_repository)
    app.dependency_overrides[get_segment_repository] = lambda: repo
    try:
        response = TestClient(app).get("/api/segments?loan_product=exotic")
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_segment_repository, None)
        else:
            app.dependency_overrides[get_segment_repository] = prior
    assert response.status_code == 422
    assert not repo.calls


def test_product_channel_leads_api_passes_filters_to_repository() -> None:
    from backend.services.repositories import get_lead_repository

    captured: list[dict[str, object]] = []

    class _CaptureLeadRepo:
        def list(self, **kwargs: object) -> list[LeadSummary]:
            captured.append(kwargs)
            return []

        def count(self, **kwargs: object) -> int:
            return 0

    prior = app.dependency_overrides.get(get_lead_repository)
    app.dependency_overrides[get_lead_repository] = lambda: _CaptureLeadRepo()
    try:
        response = TestClient(app).get(
            "/api/leads?loan_product=fha&origination_channel=digital",
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_lead_repository, None)
        else:
            app.dependency_overrides[get_lead_repository] = prior
    assert response.status_code == 200
    criteria = captured[-1]["portfolio_criteria"]
    assert isinstance(criteria, PortfolioCriteria)
    assert criteria.loan_product == "FHA"
    assert criteria.origination_channel == "Digital"


def test_product_channel_lead_summary_carries_dimension_fields() -> None:
    from backend.services.pii_redaction import redact_lead_row

    row = {
        "borrower_id": "B-0000000000000",
        "display_name": "Owner 12345678",
        "city": "Chicago",
        "state": "IL",
        "zip": "60611",
        "segment_codes": ["itm"],
        "equity_estimate": 100000,
        "rate_spread_bps": 90,
        "opportunity_score": 80,
        "confidence": 75,
        "recommended_offer": "Refinance",
        "why_now": "test",
        "evidence_ids": [],
        "loan_product_type": "jumbo",
        "origination_channel": "branch",
    }
    lead = LeadSummary(**redact_lead_row(row))
    assert lead.loan_product_type == "jumbo"
    assert lead.origination_channel == "branch"


# ---------------------------------------------------------------------------
# 5. Gold SQL contract for the new dimensions
# ---------------------------------------------------------------------------


def test_product_channel_gold_columns_carried_through_models() -> None:
    for name in ("gold_borrower_360.sql", "gold_lead_population.sql", "gold_borrower_dossier.sql"):
        text = (TRANSFORM_DIR / name).read_text(encoding="utf-8")
        assert "loan_product_type" in text, name
        assert "origination_channel" in text, name


def test_product_channel_borrower_360_uses_frozen_udf_and_governed_limit() -> None:
    text = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    assert re.search(
        r"fn_loan_product_type\(\s*e\.first_pos_loan_type,\s*e\.first_pos_amount,\s*r\.conforming_loan_limit_usd\s*\)",
        text,
    )
    assert "mip_conforming_loan_limit_usd" in text


def test_product_channel_origination_channel_is_funded_applications_only() -> None:
    text = (TRANSFORM_DIR / "gold_borrower_360.sql").read_text(encoding="utf-8")
    assert re.search(
        r"MAX_BY\(LOWER\(TRIM\(application_channel\)\), application_at\)\s*"
        r"FILTER \(WHERE application_status = 'funded'",
        text,
    )


def test_product_channel_evidence_rows_are_explainability_only() -> None:
    evidence_sql = (TRANSFORM_DIR / "gold_evidence_events.sql").read_text(encoding="utf-8")
    assert "'product_type'                                   AS signal_type" in evidence_sql
    assert "'origination_channel'                            AS signal_type" in evidence_sql
    assert "'mip.first_party.loan_applications'              AS source_table" in evidence_sql
    exclusion = "signal_type NOT IN ('permit', 'loan_type_fit', 'product_type', 'origination_channel')"
    for name in ("gold_borrower_360.sql", "gold_lead_scores.sql"):
        assert exclusion in (TRANSFORM_DIR / name).read_text(encoding="utf-8"), name


def test_product_channel_segment_population_emits_facet_mixes() -> None:
    text = (TRANSFORM_DIR / "gold_segment_population.sql").read_text(encoding="utf-8")
    assert "AS loan_product_mix" in text
    assert "AS origination_channel_mix" in text
    # NULL rolls up as 'unknown' so facet counts always sum to segment counts.
    assert "COALESCE(b.loan_product_type, 'unknown')" in text
    assert "COALESCE(b.origination_channel, 'unknown')" in text
