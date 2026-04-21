from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_app_yaml_contains_required_runtime_bindings():
    content = (REPO / "app.yaml").read_text(encoding="utf-8")
    for token in [
        "backend.main:app",
        "DATABRICKS_WAREHOUSE_ID",
        "GENIE_SPACE_ID",
        "LAKEBASE_HOST",
        "APP_ENV",
    ]:
        assert token in content


def test_databricks_yml_contains_required_resource_names():
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")
    for token in [
        "mip-app",
        "mip_serverless_sql",
        "mip_refresh_silver",
        "mip_refresh_scores",
        "mip_snapshot_dashboards",
        "mip_feature_pipeline",
        "mip_gold_pipeline",
        "mip_executive_dashboard",
        "mip_segment_dashboard",
        "mortgage_lead_intelligence",
        "mip_app_state",
        "/Shared/mip/lead-scoring",
        "mip",
        "raw,silver,gold,semantics,app,audit",
    ]:
        assert token in content
