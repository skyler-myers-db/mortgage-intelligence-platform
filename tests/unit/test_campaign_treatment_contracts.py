from __future__ import annotations

from pathlib import Path

from backend.schemas.portfolio import (
    CampaignSummary,
    PortfolioCreateResponse,
    PortfolioPreview,
    PortfolioPreviewRequest,
)
from backend.services.databricks_sql_helpers import qualify

DDL = Path("sql/ddl/001_catalogs_schemas.sql").read_text(encoding="utf-8")
RENDERED_DDL = Path("sql/_rendered/ddl/001_catalogs_schemas.sql").read_text(encoding="utf-8")
LAKEBASE_SCHEMA = Path("lakebase/schema.sql").read_text(encoding="utf-8")
SEED = Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")
DEPLOY = Path("scripts/deploy.sh").read_text(encoding="utf-8")
GRANTS = Path("docs/security/GRANTS.md").read_text(encoding="utf-8")
LIVE_IDEMPOTENCY = Path("tests/integration/test_lakebase_idempotency_live.py").read_text(
    encoding="utf-8"
)
LIVE_AUDIT = Path("tests/integration/test_campaign_audit_workflow_live.py").read_text(
    encoding="utf-8"
)


def test_campaign_treatment_delta_retains_logs_and_rewritten_files() -> None:
    for sql in (DDL, RENDERED_DDL):
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
    assert "--mode runtime" in DEPLOY
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
    assert "NEW.treatment_state = 'ready'" in LAKEBASE_SCHEMA
    assert "OLD.treatment_state <> 'building'" in LAKEBASE_SCHEMA
    assert "ready campaign treatment manifest is immutable" in LAKEBASE_SCHEMA
    assert "treatment_materialization_id" in LAKEBASE_SCHEMA
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
    assert {item.get("type") for item in preview_properties["campaign_build_contact_count"]["anyOf"]} == {
        "integer",
        "null",
    }
    assert {item.get("type") for item in preview_properties["campaign_build_eligible"]["anyOf"]} == {
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
