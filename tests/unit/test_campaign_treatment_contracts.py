from __future__ import annotations

from pathlib import Path

from backend.schemas.portfolio import (
    CampaignSummary,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.databricks_sql_helpers import qualify
from tools.render_sql import render

DDL = Path("sql/ddl/001_catalogs_schemas.sql").read_text(encoding="utf-8")
LAKEBASE_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
DEPLOY = Path("scripts/deploy.sh").read_text(encoding="utf-8")
ROLLBACK = Path("tools/databricks/app_deployment_rollback.py").read_text(encoding="utf-8")
GRANTS = Path("docs/security/GRANTS.md").read_text(encoding="utf-8")
LIVE_IDEMPOTENCY = Path("tests/integration/test_lakebase_idempotency_live.py").read_text(
    encoding="utf-8"
)
LIVE_AUDIT = Path("tests/integration/test_campaign_audit_workflow_live.py").read_text(
    encoding="utf-8"
)


def test_campaign_treatment_delta_retains_logs_and_rewritten_files(tmp_path: Path) -> None:
    render(
        catalog="mip",
        demo_first_party_enabled=False,
        source_root=Path("sql/ddl"),
        dest_root=tmp_path,
    )
    rendered_ddl = (tmp_path / "001_catalogs_schemas.sql").read_text(encoding="utf-8")

    for sql in (DDL, rendered_ddl):
        assert "CREATE TABLE IF NOT EXISTS mip.audit.campaign_treatment_snapshot" in sql
        assert "CONSTRAINT campaign_treatment_row_kind_chk" not in sql
        assert "CONSTRAINT campaign_treatment_assignment_chk" not in sql
        assert "ALTER TABLE mip.audit.campaign_treatment_snapshot SET TBLPROPERTIES" in sql
        assert "'delta.appendOnly' = 'true'" in sql
        assert "'delta.logRetentionDuration' = 'interval 2555 days'" in sql
        assert "'delta.deletedFileRetentionDuration' = 'interval 2555 days'" in sql


def test_runtime_relation_allowlist_includes_only_the_exact_treatment_table() -> None:
    assert qualify("audit", "campaign_treatment_snapshot") == (
        "mip.audit.campaign_treatment_snapshot"
    )


def test_deploy_grants_and_postflights_exact_app_table_privileges() -> None:
    exact_table = "${_GRANTS_CATALOG}.audit.campaign_treatment_snapshot"
    assert "tools.databricks.converge_campaign_treatment_access" in DEPLOY
    assert "--mode quiesce" in DEPLOY
    assert 'mode="runtime"' in ROLLBACK
    assert "atomically restore treatment authority and persist the last-good App contract" in DEPLOY
    assert f"GRANT SELECT, MODIFY ON TABLE {exact_table} TO \\`" not in DEPLOY
    assert f"SHOW TBLPROPERTIES {exact_table}" in DEPLOY
    assert '"delta.appendOnly": "true"' in DEPLOY
    assert '"delta.logRetentionDuration": "interval 2555 days"' in DEPLOY
    assert '"delta.deletedFileRetentionDuration": "interval 2555 days"' in DEPLOY
    assert "campaign treatment table property postflight failed" in DEPLOY
    assert "tools.databricks.ensure_campaign_treatment_table" in DEPLOY
    assert "GRANT SELECT, MODIFY ON TABLE mip.audit.campaign_treatment_snapshot" in GRANTS
    assert "The verifier receives no privilege on this table." in GRANTS


def test_lakebase_manifest_is_ready_only_after_fenced_finalize() -> None:
    assert "treatment_state TEXT NOT NULL DEFAULT 'legacy_unbound'" in LAKEBASE_SCHEMA
    assert "CREATE OR REPLACE FUNCTION mip_app.enforce_campaign_treatment_boundary()" in (
        LAKEBASE_SCHEMA
    )
    assert "NEW.treatment_state NOT IN ('legacy_unbound', 'building')" in LAKEBASE_SCHEMA
    assert "campaigns_treatment_reservation_chk" in LAKEBASE_SCHEMA
    assert "campaigns_treatment_lease_state_chk" in LAKEBASE_SCHEMA
    assert "campaigns_nonready_treatment_proof_empty_chk" in LAKEBASE_SCHEMA
    assert "campaign_household_summary_is_reviewed" in LAKEBASE_SCHEMA
    assert "campaign_creation_response_is_reviewed" in LAKEBASE_SCHEMA
    assert (
        "(document->>'household_count')::BIGINT "
        "<= selected_primary_count" in LAKEBASE_SCHEMA
    )
    assert (
        "(document->>'owner_link_household_count')::BIGINT"
        in LAKEBASE_SCHEMA
    )
    assert (
        "OR (document->>'household_count')::BIGINT = selected_primary_count"
        in LAKEBASE_SCHEMA
    )
    assert "document, 'selected_primary_count', 10000" in LAKEBASE_SCHEMA
    assert "OR selected_primary_count = candidate_count" in LAKEBASE_SCHEMA
    assert "WHEN holdout IS NULL THEN treatment_holdout_count = 0" in LAKEBASE_SCHEMA
    assert "treatment_holdout_count = 0" in LAKEBASE_SCHEMA
    assert "trunc((document->>'size_pct')::NUMERIC * 100)" in LAKEBASE_SCHEMA
    assert "building campaign requires a complete active reservation" in LAKEBASE_SCHEMA
    assert "building campaign cannot carry finalized treatment proof" in LAKEBASE_SCHEMA
    assert "campaign identity and creation chronology are immutable" in LAKEBASE_SCHEMA
    assert "NEW.campaign_id IS DISTINCT FROM OLD.campaign_id" in LAKEBASE_SCHEMA
    assert "NEW.created_at IS DISTINCT FROM OLD.created_at" in LAKEBASE_SCHEMA
    assert "NEW.treatment_state IS DISTINCT FROM OLD.treatment_state" in LAKEBASE_SCHEMA
    assert "NEW.treatment_state IN ('ready', 'failed')" in LAKEBASE_SCHEMA
    assert "campaign treatment state transition is not allowed" in LAKEBASE_SCHEMA
    assert "building campaign requires an active treatment lease" in LAKEBASE_SCHEMA
    assert "terminal campaign treatment state cannot retain a build lease" in LAKEBASE_SCHEMA
    assert "AND OLD.treatment_build_lease_until IS NOT NULL" in LAKEBASE_SCHEMA
    assert "AND NEW.treatment_build_lease_until IS NOT NULL" in LAKEBASE_SCHEMA
    assert ") IS TRUE" in LAKEBASE_SCHEMA
    assert (
        "campaign treatment proof may change only during building-to-ready finalization"
        in LAKEBASE_SCHEMA
    )
    assert (
        "campaign treatment lease may change only during reclaim or terminal transition"
        in LAKEBASE_SCHEMA
    )
    for proof_field in (
        "name",
        "message_variants",
        "channel_cascade",
        "send_window",
        "roi_assumptions",
        "idempotency_key",
        "request_payload_hash",
        "household_summary",
        "creation_response",
    ):
        assert (
            f"NEW.{proof_field} IS DISTINCT FROM OLD.{proof_field}"
            in LAKEBASE_SCHEMA
        )
    assert "treatment_materialization_id" in LAKEBASE_SCHEMA
    assert "campaigns_active_requires_ready_treatment_chk" in LAKEBASE_SCHEMA
    assert "CHECK (status <> 'active' OR treatment_state = 'ready')" in LAKEBASE_SCHEMA
    assert "WHERE status = 'active'" in LAKEBASE_SCHEMA
    assert "AND treatment_state <> 'ready'" in LAKEBASE_SCHEMA
    assert '"cancelled_reason":"campaign_treatment_reproof_required"' in LAKEBASE_SCHEMA
    assert "WHERE campaign_id IS NOT NULL" in LAKEBASE_SCHEMA
    assert "AND status IN ('dry_run','staged','failed')" in LAKEBASE_SCHEMA
    assert (
        "WHERE version = '2026_07_25_campaign_activation_delivery_reproof'"
        in LAKEBASE_SCHEMA
    )
    assert (
        "'2026_07_25_campaign_activation_delivery_reproof',"
        in LAKEBASE_SCHEMA
    )
    assert "ranked_activation_business_keys" in LAKEBASE_SCHEMA
    assert "ranked.business_key_rank > 1" in LAKEBASE_SCHEMA
    assert "CASE WHEN status = 'delivered' THEN 0 ELSE 1 END" in LAKEBASE_SCHEMA
    assert "WHEN 'staged' THEN" not in LAKEBASE_SCHEMA
    assert "WHERE status IN ('dry_run','staged','failed','delivered')" in (
        LAKEBASE_SCHEMA
    )
    assert "'archived'" in SEED
    assert "'active'" not in SEED
    assert "status = EXCLUDED.status" in SEED
    assert "WHERE campaigns.treatment_state = 'legacy_unbound'" in SEED


def test_live_campaign_fixtures_use_the_same_tiny_reviewed_filters() -> None:
    for source in (LIVE_IDEMPOTENCY, LIVE_AUDIT):
        assert '"criteria": {}' not in source
        assert '"/api/portfolio/preview"' in source
        assert '"campaign_build_config": {}' in source
        assert 'preview.get("campaign_build_eligible") is not True' in source
        assert 'preview.get("campaign_build_contact_count")' in source
        assert "min_equity_pct=99.9" in source
        assert '"min_equity_pct": 99.9' in source
        assert "recency=Untouched%2030d" in source
        assert '"recency": "Untouched 30d"' in source
    assert "candidate_borrower_ids=candidate_borrower_ids" in LIVE_IDEMPOTENCY


def test_portfolio_openapi_models_expose_typed_campaign_build_contract() -> None:
    preview_properties = PortfolioPreview.model_json_schema()["properties"]
    assert preview_properties["campaign_build_limit"]["default"] == 10_000
    assert preview_properties["campaign_build_limit"]["minimum"] == 1
    assert {
        item.get("type") for item in preview_properties["campaign_build_contact_count"]["anyOf"]
    } == {
        "integer",
        "null",
    }
    assert {
        item.get("type") for item in preview_properties["campaign_build_eligible"]["anyOf"]
    } == {
        "boolean",
        "null",
    }
    create_properties = PortfolioCreateResponse.model_json_schema()["properties"]
    assert create_properties["campaign_build_limit"]["default"] == 10_000
    assert create_properties["campaign_build_limit"]["minimum"] == 1
    assert create_properties["campaign_build_eligible"]["type"] == "boolean"
    request_properties = PortfolioPreviewRequest.model_json_schema()["properties"]
    assert "campaign_build_config" in request_properties


def test_campaign_summary_exposes_strict_treatment_state_contract() -> None:
    treatment_state = CampaignSummary.model_json_schema()["properties"]["treatment_state"]
    assert treatment_state["enum"] == ["legacy_unbound", "building", "ready", "failed"]
