from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.schemas.portfolio import HouseholdDedupSummary, PortfolioCreateRequest
from backend.services.audit_store import AuditMetadataValueViolation, build_safe_audit_metadata
from backend.services.repositories.databricks_repo import DatabricksPortfolioRepository

REPO_ROOT = Path(__file__).resolve().parents[2]


class _HouseholdStubClient:
    def __init__(self) -> None:
        self.sql: str | None = None
        self.params: dict[str, Any] | None = None

    def execute_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sql = sql
        self.params = params
        return {
            "candidate_borrower_count": 12,
            "selected_primary_count": 9,
            "suppressed_co_owner_count": 3,
            "household_count": 9,
            "owner_link_household_count": 6,
            "mailing_address_household_count": 2,
            "singleton_household_count": 1,
        }


def test_household_dedup_defaults_to_borrower_unit() -> None:
    payload = PortfolioCreateRequest(name="Q3 household test")

    assert payload.household_dedup.enabled is False
    assert payload.household_dedup.dedupe_unit == "borrower"
    assert payload.household_dedup.primary_contact_strategy == "highest_opportunity_eligible"


def test_household_dedup_enabled_normalizes_to_household_unit() -> None:
    payload = PortfolioCreateRequest(
        name="Q3 household test",
        household_dedup={"enabled": True, "dedupe_unit": "borrower"},
    )

    assert payload.household_dedup.enabled is True
    assert payload.household_dedup.dedupe_unit == "household"


def test_household_summary_counts_are_bounded() -> None:
    with pytest.raises(ValidationError):
        HouseholdDedupSummary(enabled=True, suppressed_co_owner_count=-1)

    with pytest.raises(ValidationError):
        HouseholdDedupSummary(
            enabled=True,
            source_assets=["mip.silver.property_owners"],
        )


def test_household_audit_metadata_allows_bounded_counts_only() -> None:
    metadata = build_safe_audit_metadata(
        {
            "source": "portfolio_builder",
            "portfolio_criteria": {"marketing_eligibility": "Eligible only"},
            "dedupe_unit": "household",
            "household_dedup_enabled": True,
            "household_primary_strategy": "highest_opportunity_eligible",
            "household_candidate_count": 12,
            "household_primary_count": 9,
            "household_suppressed_count": 3,
            "household_household_count": 9,
            "household_owner_link_count": 6,
            "household_mailing_address_count": 2,
            "household_singleton_count": 1,
            "source_assets": ["mip.gold.household_rollup", "mip.gold.borrower_360"],
        },
        action="portfolio.create",
    )

    assert metadata["dedupe_unit"] == "household"
    assert metadata["household_suppressed_count"] == 3

    with pytest.raises(AuditMetadataValueViolation):
        build_safe_audit_metadata(
            {
                "source": "portfolio_builder",
                "dedupe_unit": "neighborhood",
                "household_dedup_enabled": True,
            },
            action="portfolio.create",
        )

    with pytest.raises(AuditMetadataValueViolation):
        build_safe_audit_metadata(
            {
                "source": "portfolio_builder",
                "dedupe_unit": "household",
                "household_dedup_enabled": True,
                "household_suppressed_count": -1,
            },
            action="portfolio.create",
        )


def test_household_summary_query_forces_primary_contact_eligibility() -> None:
    client = _HouseholdStubClient()
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    payload = PortfolioCreateRequest(
        name="Q3 household test",
        household_dedup={"enabled": True},
    )

    summary = repo._load_household_dedup_summary(payload)

    assert summary.suppressed_co_owner_count == 3
    assert client.sql is not None
    assert "mip.gold.household_rollup" in client.sql
    assert "ROW_NUMBER() OVER" in client.sql
    assert "b.marketing_eligible = TRUE" in client.sql
    assert "COALESCE(b.has_unresolved_owner, FALSE) = FALSE" in client.sql
    assert "campaign_household_rank > 1" in client.sql
    assert client.params == {}


def test_household_rollup_sql_documents_deterministic_non_pii_derivation() -> None:
    ddl = (REPO_ROOT / "sql" / "ddl" / "gold_household_rollup.sql").read_text(
        encoding="utf-8"
    )
    transform = (
        REPO_ROOT / "sql" / "transformations" / "gold_household_rollup.sql"
    ).read_text(encoding="utf-8")

    assert "mip.silver.property_owners" in ddl
    assert "mip.silver.property_master" in ddl
    assert "owner_link_reach AS" in transform
    assert "mailing_households AS" in transform
    assert "mailing_street_address" not in transform
    assert "owner_full_name" not in transform
    assert "CONCAT('HH-'" in transform
    assert "marketing_eligible = TRUE" in transform
    assert "has_unresolved_owner" in transform


def test_household_rollup_is_deployable_and_lakebase_persisted() -> None:
    bundle = (REPO_ROOT / "databricks.yml").read_text(encoding="utf-8")
    lakebase = (REPO_ROOT / "lakebase" / "schema.sql").read_text(encoding="utf-8")

    assert "task_key: ctas_household_rollup" in bundle
    assert "sql/_rendered/transformations/gold_household_rollup.sql" in bundle
    assert "household_dedup JSONB" in lakebase
    assert "household_summary JSONB" in lakebase
    assert "2026_07_09_campaign_household_dedup" in lakebase
