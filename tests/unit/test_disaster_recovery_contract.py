from __future__ import annotations

from pathlib import Path


def test_disaster_recovery_runbook_covers_operational_scenarios() -> None:
    text = Path("docs/disaster-recovery.md").read_text(encoding="utf-8")

    for phrase in (
        "Lakebase Corrupt Or Rolled Back",
        "Gold Tables Corrupt Or Bad Refresh",
        "Bad App Snapshot Or Frontend Regression",
        "Bundle Resource Regression",
        "Genie Space Deleted Or Misconfigured",
        "Governed Action Secret Rotation",
        "Audit Ledger Archival",
        "RPO",
        "RTO target",
    ):
        assert phrase in text

    for command in (
        "databricks bundle run mip_lakebase_migrate -t dev",
        "databricks bundle run mip_refresh_scores -t dev",
        "databricks apps list-deployments mip-app",
        "git checkout <prior-good-sha>",
        "tools/databricks/provision_genie_space.py",
        "tools/databricks/export_action_audit.py",
    ):
        assert command in text


def test_lakebase_schema_records_migrations_and_archive_runs() -> None:
    schema_sql = Path("lakebase/schema.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS mip_app.schema_migrations" in schema_sql
    assert "version     TEXT PRIMARY KEY" in schema_sql
    assert "2026_05_18_dr_backup_contract" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS mip_app.action_audit_archive_runs" in schema_sql
    assert "cutoff_event_at" in schema_sql
    assert "destination_uri" in schema_sql
    assert "row_count" in schema_sql
    assert "INSERT INTO mip_app.schema_migrations" in schema_sql


def test_production_lakebase_targets_enable_readable_secondaries() -> None:
    bundle = Path("databricks.yml").read_text(encoding="utf-8")

    assert "prod:" in bundle
    assert "prod_otlp:" in bundle
    assert bundle.count("enable_readable_secondaries: true") >= 2
    assert "enable_readable_secondaries: false" in bundle


def test_action_audit_export_helper_is_copy_only() -> None:
    script = Path("tools/databricks/export_action_audit.py").read_text(encoding="utf-8")

    assert "FROM mip_app.action_audit" in script
    assert "INSERT INTO mip_app.action_audit_archive_runs" in script
    assert "DELETE FROM mip_app.action_audit" not in script
    assert "UPDATE mip_app.action_audit" not in script
    assert "jsonl.gz" in script


def test_hmac_rotation_contract_is_exposed_to_operators() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    settings_py = Path("backend/config/settings.py").read_text(encoding="utf-8")
    genie_actions = Path("backend/services/genie_actions.py").read_text(encoding="utf-8")

    for name in (
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
        "MIP_GENIE_ACTION_SECRET_KID",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID",
    ):
        assert name in env_example

    for attr in (
        "mip_genie_action_secret_current",
        "mip_genie_action_secret_previous",
        "mip_genie_action_secret_kid",
        "mip_genie_action_secret_previous_kid",
    ):
        assert attr in settings_py

    assert '"kid"' in genie_actions
    assert "_previous_action_token_key" in genie_actions
