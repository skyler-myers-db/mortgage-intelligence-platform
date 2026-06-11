import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_app_yaml_contains_required_runtime_bindings():
    content = (REPO / "app.yaml").read_text(encoding="utf-8")
    for token in [
        "backend.main:app",
        "DATABRICKS_WAREHOUSE_ID",
        "GENIE_SPACE_ID",
        "LAKEBASE_HOST",
        "MIP_FRED_RATES_JOB_ID",
        "MIP_SILVER_REFRESH_JOB_ID",
        "MIP_GOLD_REFRESH_JOB_ID",
        "MIP_LIFECYCLE_SYNC_JOB_ID",
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
        "fred_rates_job",
        "silver_refresh_job",
        "gold_refresh_job",
        "lifecycle_sync_job",
        "mip_feature_pipeline",
        "mip_executive_dashboard",
        "mip_segment_dashboard",
        "mortgage_lead_intelligence",
        "mip_app_state",
        "/Shared/mip/lead-scoring",
        "mip",
        "raw,silver,gold,semantics,app,audit",
    ]:
        assert token in content


def test_dev_bundle_pins_real_genie_space_for_bare_deploy():
    """The dev target must not inherit the CI placeholder for app binding.

    Databricks Apps requires a concrete Genie ``space_id`` in the app resource.
    If the dev target falls back to the root placeholder, bare
    ``databricks bundle deploy -t dev --profile DEFAULT`` reaches Apps with an
    invalid binding and returns an opaque permission error.
    """
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")
    dev_match = re.search(r"(?ms)^  dev:\n(?P<body>.*?)(?=^  prod:\n)", content)
    assert dev_match, "databricks.yml must define a dev target before prod."

    dev_body = dev_match.group("body")
    assert "genie_space_id: 01f13d4968af1b249dc388fd5b18b195" in dev_body
    assert "genie_space_id: 00000000PLACEHOLDER" not in dev_body


def test_python_jobs_receive_bundle_catalog_variable():
    """Spark Python jobs must follow the same catalog as rendered SQL."""
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")

    sync_match = re.search(
        r"(?ms)python_file: jobs/sync_lifecycle_state.py\n"
        r"\s+parameters: \[\"--catalog=\$\{var\.uc_catalog\}\"\]",
        content,
    )
    assert sync_match, "lifecycle sync must receive --catalog=${var.uc_catalog}"

    for mode in ("seed", "fred"):
        pattern = (
            rf"(?ms)python_file: jobs/fred_rates_ingest.py\n"
            rf"\s+parameters:\n"
            rf"\s+- \"--mode={mode}\"\n"
            rf"\s+- \"--table=\$\{{var\.uc_catalog\}}\.silver\.market_rates_weekly\""
        )
        assert re.search(pattern, content), (
            f"FRED {mode} task must target ${{var.uc_catalog}}.silver.market_rates_weekly"
        )


def test_runtime_requirements_include_otlp_exporter_wheels():
    """A configured collector must produce log_export=otlp, not fallback.

    The OTLP handler is still gated by MIP_OTEL_ENDPOINT, but the deployed
    App image must carry the exporter packages so production can turn on a
    collector with deployment env vars or app.yaml env/resource wiring.
    """
    files = ("requirements.txt", "requirements.in")
    lines = [
        line.strip()
        for filename in files
        for line in (REPO / filename).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert any(line.startswith("opentelemetry-sdk") for line in lines)
    assert any(line.startswith("opentelemetry-exporter-otlp") for line in lines)


def test_prod_otlp_target_wires_secret_resource_without_global_app_yaml_secret():
    """Durable OTLP uses an explicit target, not a dev-breaking global env.

    ``app.yaml`` stays portable for normal dev/customer deploys. The
    ``prod_otlp`` bundle target attaches the secret resource; operators then
    promote with a full deployment env_vars payload that sets
    ``MIP_OTEL_HEADERS`` from that resource.
    """
    app_yaml = (REPO / "app.yaml").read_text(encoding="utf-8")
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")

    assert "MIP_OTEL_HEADERS" not in app_yaml
    assert "prod_otlp:" in bundle
    assert "name: otel_headers" in bundle
    assert "secret:" in bundle
    assert "scope: ${var.otel_headers_secret_scope}" in bundle
    assert "key: ${var.otel_headers_secret_key}" in bundle


def test_workspace_host_configuration_script_rewrites_only_anchor(tmp_path):
    source = REPO / "databricks.yml"
    target = tmp_path / "databricks.yml"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    script = REPO / "scripts" / "configure-workspace.sh"
    result = subprocess.run(
        [
            str(script),
            "--file",
            str(target),
            "adb-1234567890123456.7.azuredatabricks.net",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    content = target.read_text(encoding="utf-8")
    assert "[configure-workspace] updated workspace.host anchor." in result.stdout
    assert "host: &default_host https://adb-1234567890123456.7.azuredatabricks.net" in content
    assert "https://dbc-3aa503a9-4fa8.cloud.databricks.com" not in content
    assert len(re.findall(r"^  host: &default_host ", content, re.MULTILINE)) == 1
    assert len(re.findall(r"host: \*default_host", content)) >= 4


def test_workspace_host_configuration_script_rejects_non_origin_url(tmp_path):
    target = tmp_path / "databricks.yml"
    target.write_text((REPO / "databricks.yml").read_text(encoding="utf-8"), encoding="utf-8")

    script = REPO / "scripts" / "configure-workspace.sh"
    result = subprocess.run(
        [
            str(script),
            "--file",
            str(target),
            "https://adb-1234567890123456.7.azuredatabricks.net/?token=bad",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "workspace host must be only the workspace origin" in result.stderr
