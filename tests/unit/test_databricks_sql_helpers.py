from __future__ import annotations

import pytest

from backend.config.settings import settings
from backend.services.databricks_sql_helpers import qualify


def test_qualify_uses_default_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_default_catalog", "acme_mip")

    assert qualify("gold", "borrower_360") == "acme_mip.gold.borrower_360"


def test_qualify_accepts_explicit_catalog() -> None:
    assert qualify("gold", "lead_population", catalog="mip") == "mip.gold.lead_population"


@pytest.mark.parametrize(
    ("schema", "table", "expected"),
    [
        ("silver", "property_master", "mip.silver.property_master"),
        ("first_party", "loan_applications", "mip.first_party.loan_applications"),
        (
            "semantics",
            "lead_generation_metric_view",
            "mip.semantics.lead_generation_metric_view",
        ),
        (
            "semantics",
            "certified_lead_generation_metric_view",
            "mip.semantics.certified_lead_generation_metric_view",
        ),
        (
            "semantics",
            "certified_segment_performance_metric_view",
            "mip.semantics.certified_segment_performance_metric_view",
        ),
        (
            "semantics",
            "certified_borrower_opportunity_metric_view",
            "mip.semantics.certified_borrower_opportunity_metric_view",
        ),
        ("gold", "fn_estimated_upb", "mip.gold.fn_estimated_upb"),
        ("gold", "fn_bounded_mortgage_rate", "mip.gold.fn_bounded_mortgage_rate"),
        (
            "gold",
            "fn_estimated_upb_confidence_band",
            "mip.gold.fn_estimated_upb_confidence_band",
        ),
        ("gold", "fn_next_best_offer", "mip.gold.fn_next_best_offer"),
        ("gold", "fn_build_cohort", "mip.gold.fn_build_cohort"),
        ("gold", "fn_segment_counts", "mip.gold.fn_segment_counts"),
        ("gold", "fn_lead_queue_url", "mip.gold.fn_lead_queue_url"),
    ],
)
def test_qualify_allows_known_public_relations(
    schema: str,
    table: str,
    expected: str,
) -> None:
    assert qualify(schema, table, catalog="mip") == expected


@pytest.mark.parametrize(
    ("schema", "table"),
    [
        ("gold.leak", "borrower_360"),
        ("gold", "borrower_360.other"),
        ("gold; DROP TABLE mip.gold.borrower_360", "borrower_360"),
        ("gold", "borrower_360; DROP TABLE mip.gold.lead_population"),
        ("gold --", "borrower_360"),
        ("gold", "borrower_360 --"),
        ("gold/*x*/", "borrower_360"),
        ("gold", "borrower_360/*x*/"),
        ("`gold`", "borrower_360"),
        ("gold", "`borrower_360`"),
        ("", "borrower_360"),
        ("gold", ""),
        ("gold schema", "borrower_360"),
        ("gold", "borrower 360"),
    ],
)
def test_qualify_rejects_invalid_schema_or_table(schema: str, table: str) -> None:
    with pytest.raises(ValueError, match="Invalid Unity Catalog"):
        qualify(schema, table, catalog="mip")


@pytest.mark.parametrize(
    "catalog",
    [
        "mip.prod",
        "mip; DROP TABLE mip.gold.borrower_360",
        "mip --",
        "mip/*x*/",
        "`mip`",
        "",
        "mip catalog",
    ],
)
def test_qualify_rejects_invalid_explicit_catalog(catalog: str) -> None:
    with pytest.raises(ValueError, match="Invalid Unity Catalog catalog identifier"):
        qualify("gold", "borrower_360", catalog=catalog)


def test_qualify_rejects_invalid_default_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_default_catalog", "mip; SELECT 1")

    with pytest.raises(ValueError, match="Invalid Unity Catalog catalog identifier"):
        qualify("gold", "borrower_360")


def test_qualify_rejects_unknown_relation() -> None:
    with pytest.raises(ValueError, match="Unknown Unity Catalog relation"):
        qualify("gold", "unregistered_public_table", catalog="mip")
