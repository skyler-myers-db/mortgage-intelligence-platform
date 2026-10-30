import re
import subprocess
from pathlib import Path

import yaml

from tests.fixtures.deploy_script import deploy_entrypoint_text

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
        "MIP_GROWTH_AGENT_SCHEDULER_JOB_ID",
        "MIP_LIFECYCLE_SYNC_JOB_ID",
        "APP_ENV",
    ]:
        assert token in content


def test_app_yaml_disables_idle_lead_rewarm_by_default():
    """Deployed Apps must not keep the SQL warehouse warm while idle."""
    content = (REPO / "app.yaml").read_text(encoding="utf-8")
    assert re.search(
        r"(?ms)- name: MIP_LEADS_WARM_INTERVAL_S\s+value: \"0\"",
        content,
    ), "app.yaml must disable periodic lead-cache rewarm by default"


def test_app_yaml_pins_warehouse_keep_warm_off_by_default():
    """One keep-warm policy (audit delivery-v1): the deployed default is off,
    so an idle warehouse auto-stops; the lead interval is scheduled-only."""
    content = (REPO / "app.yaml").read_text(encoding="utf-8")
    assert re.search(
        r"(?ms)- name: MIP_WAREHOUSE_KEEP_WARM\s+value: \"off\"",
        content,
    ), "app.yaml must pin MIP_WAREHOUSE_KEEP_WARM to off"
    assert "Cadence of the \"scheduled\" keep-warm policy only" in content


def test_bundle_sql_warehouse_uses_minimal_idle_timeout():
    """The bundle-managed SQL warehouse should auto-stop quickly when idle."""
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")
    assert re.search(
        r"(?ms)mip_serverless_sql:.*?auto_stop_mins: 10",
        content,
    ), "mip_serverless_sql should use a 10 minute auto-stop"


def test_bundle_feature_pipeline_uses_serverless_compute():
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")
    assert re.search(
        r"(?ms)mip_feature_pipeline:.*?serverless: true",
        content,
    ), "mip_feature_pipeline should deploy in serverless-only workspaces"


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
        "growth_agent_scheduler_job",
        "lifecycle_sync_job",
        "mip_feature_pipeline",
        "mip_executive_dashboard",
        "mip_segment_dashboard",
        "mortgage_lead_intelligence",
        "mip_app_state",
        "/Shared/mip-lead-scoring",
        "mip",
        "raw,silver,gold,semantics,app,audit",
    ]:
        assert token in content


def test_bundle_resource_names_are_parameterized_for_isolated_staging():
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")

    assert "&default_host" not in content, "M1-PR2: no committed workspace host"
    assert "name: ${var.app_name}" in content
    assert "name: ${var.lakebase_instance_name}" in content
    assert "name: ${var.lakebase_catalog_name}" in content
    assert re.search(r"(?ms)^  app_name:.*?default: mip-app$", content)
    assert re.search(r"(?ms)^  lakebase_instance_name:.*?default: mip-app-state$", content)
    assert re.search(r"(?ms)^  lakebase_catalog_name:.*?default: mip_app_state$", content)

    client = yaml.safe_load(content)["targets"]["client"]
    assert "profile" not in client.get("workspace", {})
    assert "run_as" not in client


def test_no_target_runs_as_the_deploying_user():
    """M1-PR2: no target binds a top-level run_as; each job and the pipeline name the deployer SP.

    The dev and prod targets, which ran as the deploying user through
    ``user_name``, left the bundle (tests/unit/test_client_target_shape.py
    pins the per-resource run_as).
    """

    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    targets = bundle["targets"]

    assert set(targets) == {"client", "ci"}
    assert not any("run_as" in target for target in targets.values())
    assert "${workspace.current_user.userName}" not in (REPO / "databricks.yml").read_text(encoding="utf-8")


def test_workspace_unique_names_and_the_client_root_are_literal():
    """No variable suffixes the workspace-unique display names; the client root is the deployer's folder plus the client key."""
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")
    bundle = yaml.safe_load(content)

    for name in ("mip_serverless_sql", "mip_feature_pipeline", "/Shared/mip-lead-scoring"):
        assert f"\n      name: {name}\n" in content, name
    assert (
        bundle["targets"]["client"]["workspace"]["root_path"]
        == "/Workspace/Users/${var.deployer_principal}/.bundle/${bundle.name}/${var.client_key}"
    )


def test_no_target_pins_a_genie_space_and_the_wrapper_refuses_the_placeholder():
    """The dev target's Genie pin left with it (M1-PR2).

    Databricks Apps requires a concrete Genie ``space_id`` in the app resource,
    and a placeholder reaches Apps as an opaque permission error. No target
    pins one now: tools/databricks/bundle_env.py resolves the governed space by
    its pin (tools/databricks/genie_space_pin.py, M1-PR3; never its title) and
    refuses a plan, summary, deploy or binding that would still carry the
    placeholder.
    """
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")
    bundle = yaml.safe_load(content)
    wrapper = (REPO / "tools" / "databricks" / "bundle_env.py").read_text(encoding="utf-8")

    assert not any("genie_space_id" in (target.get("variables") or {}) for target in bundle["targets"].values())
    assert bundle["variables"]["genie_space_id"]["default"] == "00000000PLACEHOLDER"
    assert "genie = _pinned_genie_space_id(env, profile)" in wrapper
    assert "refusing bundle {subcmd} with placeholder bundle variables" in wrapper


def test_databricks_yml_names_no_genie_space_id():
    """Neither the retired staging pin nor the live space's id is committed to the bundle any more."""
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")

    assert "01f18188d7a41311abe3d99932b5aa9a" not in content, "the retired staging-workspace pin"
    assert re.search(r"genie_space_id: (?!00000000PLACEHOLDER)[0-9a-f]{32}", content) is None
    assert re.search(r"genie_space_id: (?!00000000PLACEHOLDER)[0-9a-f]{32}", "genie_space_id: " + "a" * 32)


def test_python_jobs_receive_bundle_catalog_variable():
    """Spark Python jobs must follow the same catalog as rendered SQL."""
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")

    sync_match = re.search(
        r"(?ms)python_file: jobs/sync_lifecycle_state.py\n"
        r"\s+parameters:\n"
        r"\s+- \"--catalog=\$\{var\.uc_catalog\}\"",
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
        assert re.search(
            pattern, content
        ), f"FRED {mode} task must target ${{var.uc_catalog}}.silver.market_rates_weekly"


def test_lakebase_jobs_receive_the_same_bundle_resource_namespace():
    content = (REPO / "databricks.yml").read_text(encoding="utf-8")

    for source in (
        "jobs/lakebase_migrate.py",
        "jobs/sync_lifecycle_state.py",
        "jobs/kpi_snapshot.py",
    ):
        start = content.index(f"python_file: {source}")
        end = content.find("\n          max_retries:", start)
        block = content[start:end]
        assert "--lakebase-instance=${var.lakebase_instance_name}" in block
        assert "--lakebase-database=${var.lakebase_database_name}" in block

    migration_start = content.index("python_file: jobs/lakebase_migrate.py")
    migration_end = content.find("\n          max_retries:", migration_start)
    assert "--app-name=${var.app_name}" in content[migration_start:migration_end]


def test_deploy_resolves_resource_namespace_before_workspace_mutation():
    content = deploy_entrypoint_text()
    namespace = content.index('MIP_APP_NAME="$(deployment_control_value MIP_APP_NAME mip-app)"')
    genie_resolution = content.index(
        'step "resolve governed Genie space by its pin before App secret and bundle mutation"'
    )
    runtime_secret_mutation = content.index(
        'step "provision Databricks App runtime secret bindings"'
    )
    bundle_deploy = content.index("bundle_env deploy", runtime_secret_mutation)

    assert namespace < genie_resolution < runtime_secret_mutation < bundle_deploy
    namespace_block = content[namespace:genie_resolution]
    for variable in (
        "BUNDLE_VAR_app_name",
        "BUNDLE_VAR_lakebase_instance_name",
        "BUNDLE_VAR_lakebase_catalog_name",
        "BUNDLE_VAR_lakebase_database_name",
    ):
        assert f"export {variable}=" in namespace_block


def test_deploy_overwrites_stale_genie_id_before_bundle_and_after_rebind():
    content = deploy_entrypoint_text()
    first_resolve = content.index(
        'step "resolve governed Genie space by its pin before App secret and bundle mutation"'
    )
    runtime_secret_mutation = content.index(
        'step "provision Databricks App runtime secret bindings"'
    )
    first_block = content[first_resolve:runtime_secret_mutation]

    assert 'GENIE_SPACE_ID="$(< genie/space_id.txt)"' in first_block
    assert "export GENIE_SPACE_ID" in first_block
    assert "GENIE_SPACE_ID_FROM_ENV" not in first_block
    # M1-PR3: by its pin, signed when it pins, and an ambient GENIE_SPACE_ID is dropped first.
    assert 'run_with_proof_signing_authority "$PYTHON" -m tools.databricks.provision_genie_space' in first_block
    assert '"${GENIE_PIN_ARGS[@]}" "${GENIE_PIN_ACK_ARGS[@]}"' in first_block
    assert content.index("\nunset GENIE_SPACE_ID\n") < first_resolve

    rebind = content.index(
        'step "rebind Genie space — bind trusted assets from '
        'genie/mortgage_lead_intelligence_space.yml"'
    )
    downstream = content.index(
        'step "prove agentic Lakebase Sync under deployer authority"', rebind
    )
    rebind_block = content[rebind:downstream]
    assert 'GENIE_SPACE_ID="$(< genie/space_id.txt)"' in rebind_block
    assert "export GENIE_SPACE_ID" in rebind_block


def test_every_verifier_convergence_uses_the_normalized_lakebase_instance():
    content = deploy_entrypoint_text()
    calls = content.split("-m tools.databricks.provision_m2m_oauth")[1:]
    verifier_calls = [
        call.split("step ", 1)[0]
        for call in calls
        if "--identity-role verifier" in call.split("step ", 1)[0]
    ]

    assert len(verifier_calls) == 2
    assert all('--lakebase-instance "$MIP_LAKEBASE_INSTANCE"' in call for call in verifier_calls)


def test_live_workflows_propagate_the_resource_namespace():
    deploy = (REPO / ".github" / "workflows" / "deploy-dev.yml").read_text(encoding="utf-8")
    nightly = (REPO / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")

    variables = (
        "MIP_APP_NAME",
        "MIP_LAKEBASE_INSTANCE",
        "LAKEBASE_DATABASE",
        "MIP_LAKEBASE_SYNC_CATALOG",
        "MIP_GENIE_SPACE_NAME",
    )
    for variable in variables:
        assert f"vars.{variable}" in deploy
        assert f"vars.{variable}" in nightly
    for variable in (
        "BUNDLE_VAR_app_name",
        "BUNDLE_VAR_lakebase_instance_name",
        "BUNDLE_VAR_lakebase_catalog_name",
        "BUNDLE_VAR_lakebase_database_name",
    ):
        assert f"{variable}:" in nightly

    assert "LAKEBASE_INSTANCE_NAME: mip-app-state" not in nightly
    assert "MIP_APP_NAME: mip-app" not in nightly


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
    assert any(line.startswith("opentelemetry-instrumentation-logging") for line in lines)


def test_governed_deploy_wires_otlp_without_global_app_yaml_secret():
    """Durable OTLP overlays the existing governed target, not a parallel one."""
    app_yaml = (REPO / "app.yaml").read_text(encoding="utf-8")
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")
    deploy = deploy_entrypoint_text()

    assert "MIP_OTEL_HEADERS" not in app_yaml
    assert "prod_otlp:" not in bundle
    assert "MIP_OTEL_HEADERS_SECRET_SCOPE" in deploy
    assert "MIP_OTEL_HEADERS_SECRET_KEY" in deploy
    assert "--otel-header-secret-scope" in deploy
    assert "--otel-header-resource otel_headers" in deploy
    assert "MIP_OTEL_HEADERS must never be provided as plaintext" in deploy


def test_the_host_anchor_tooling_is_retired():
    """M1-PR2: no host is committed, so nothing rebinds or greps one.

    scripts/configure-workspace.sh and the Makefile's configure-workspace and
    check-workspace-host targets left with the anchor; the host comes from the
    client profile and the authenticated environment.
    """
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert not (REPO / "scripts" / "configure-workspace.sh").exists()
    assert "configure-workspace" not in makefile and "check-workspace-host" not in makefile
    assert "bundle_env.py validate -t client" in makefile and "validate -t ci" not in makefile
    deploy_dev = makefile[makefile.index("\ndeploy-dev:\n") :]
    assert "gh workflow run deploy-dev.yml" in deploy_dev and "./scripts/deploy.sh\n" not in deploy_dev
    result = subprocess.run(["make", "-s", "-C", str(REPO), "deploy-dev"], capture_output=True, text=True, check=False)
    assert result.returncode == 2 and "gh workflow run deploy-dev.yml" in result.stderr
