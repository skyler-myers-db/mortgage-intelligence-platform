from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_render_sql_demo_feed_switch_defaults_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare render must not seed synthetic first-party rows by accident."""
    from tools import render_sql

    monkeypatch.delenv("MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS", raising=False)

    assert render_sql._resolve_demo_first_party_enabled(None) is False
    assert render_sql._resolve_demo_first_party_enabled("true") is True


def test_bundle_env_refuses_demo_feeds_for_non_dev_without_override() -> None:
    from tools.databricks import bundle_env

    assert bundle_env._target_from_args(["-t", "prod"]) == "prod"
    assert bundle_env._target_from_args(["--target=customer"]) == "customer"
    assert bundle_env._demo_feeds_allowed_for_target(
        target="dev",
        enabled=True,
        env={},
    )
    assert not bundle_env._demo_feeds_allowed_for_target(
        target="prod",
        enabled=True,
        env={},
    )
    assert bundle_env._demo_feeds_allowed_for_target(
        target="prod",
        enabled=True,
        env={"MIP_ALLOW_DEMO_FIRST_PARTY_IN_PROD": "1"},
    )


def test_local_rendered_demo_feed_artifact_is_fail_closed_when_present() -> None:
    """A checked-out workstation must not be left with a stale enabled seed."""
    rendered = REPO / "sql/_rendered/transformations/demo_first_party_feeds.sql"
    if not rendered.exists():
        pytest.skip("rendered SQL tree has not been materialized")

    sql = rendered.read_text(encoding="utf-8")

    assert "SELECT FALSE AS enabled" in sql
    assert "SELECT TRUE AS enabled" not in sql


def test_demo_first_party_seed_is_explicitly_render_gated() -> None:
    sql = (REPO / "sql/transformations/demo_first_party_feeds.sql").read_text(
        encoding="utf-8"
    )
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")

    assert "{{mip_enable_demo_first_party_feeds}}" in sql
    assert ":enable_demo_first_party_feeds" not in sql
    assert "feed_mode" in sql
    assert "'demo_synthetic' AS feed_mode" in sql
    assert "TRUE AS synthetic_demo" in sql
    assert "seed_demo_first_party_feeds" in bundle
    seed_task = bundle[
        bundle.index("seed_demo_first_party_feeds") : bundle.index(
            "ctas_property_owner_bridge"
        )
    ]
    assert "parameters:" not in seed_task


def test_first_party_contracts_carry_demo_disclosure_columns() -> None:
    for relative in ("sql/ddl/001_catalogs_schemas.sql", "sql/ddl/003_gold_tables.sql"):
        ddl = (REPO / relative).read_text(encoding="utf-8")
        for table in (
            "loan_applications",
            "servicing_portfolio",
            "crm_campaign_membership",
            "customer_interactions",
            "product_balances",
        ):
            assert f"mip.first_party.{table}" in ddl
        assert "source_system" in ddl
        assert "feed_mode" in ddl
        assert "synthetic_demo" in ddl


def test_gold_consumes_first_party_relationship_signals() -> None:
    borrower_sql = (REPO / "sql/transformations/gold_borrower_360.sql").read_text(
        encoding="utf-8"
    )
    score_sql = (REPO / "sql/transformations/gold_lead_scores.sql").read_text(
        encoding="utf-8"
    )
    readiness_sql = (REPO / "sql/transformations/gold_source_readiness.sql").read_text(
        encoding="utf-8"
    )

    for table in (
        "mip.first_party.servicing_portfolio",
        "mip.first_party.loan_applications",
        "mip.first_party.crm_campaign_membership",
        "mip.first_party.customer_interactions",
        "mip.first_party.product_balances",
    ):
        assert table in borrower_sql

    assert "has_first_party_relationship" in borrower_sql
    assert "first_party_relationship_depth" in borrower_sql
    assert "first_party_recent_interactions" in score_sql
    assert "first_party_recent_application" in score_sql
    assert "'demo_synthetic'" in readiness_sql
    assert "COUNT_IF(COALESCE(synthetic_demo, FALSE)) = COUNT(*) THEN 'demo_synthetic'" in readiness_sql
    assert "Summit Mortgage synthetic servicing feed" in readiness_sql
