from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_bundle_uploads_prebuilt_frontend_without_root_npm_wrapper() -> None:
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    app = yaml.safe_load((ROOT / "app.yaml").read_text(encoding="utf-8"))
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    sync = bundle["sync"]

    assert "frontend/dist/**" in sync["include"]
    assert {"/package.json", "/package-lock.json"} <= set(sync["exclude"])
    assert "build" not in app
    assert app["command"] == ["python", "-m", "backend.runtime"]
    assert deploy.index("run npm --prefix frontend run build") < deploy.index(
        'run "$PYTHON" -m tools.databricks.bundle_env deploy -t "$TARGET"'
    )


def test_every_snapshot_converges_static_source_immediately_before_activation() -> None:
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    function_start = deploy.index("deploy_app_snapshot() {")
    function_end = deploy.index("\n}", function_start)
    function_body = deploy[function_start:function_end]

    assert deploy.count("-m tools.databricks.converge_static_app_source") == 1
    assert deploy.count("databricks apps deploy") == 1
    assert (
        '  assert_expected_app_identity "$APP_NAME"\n'
        '  run "$PYTHON" -m tools.databricks.converge_static_app_source \\\n'
        '    --source-code-path "$APP_SOURCE_PATH" \\\n'
        '    --expected-principal "$APP_CURRENT_USER" \\\n'
        '    --expected-target "$TARGET"\n'
        '  run databricks apps deploy "$APP_NAME"'
    ) in function_body


def test_lakebase_recovery_watches_the_exact_created_dev_workflow() -> None:
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    recovery_start = runbook.index("If the Lakebase instance or App is stopped")
    recovery_end = runbook.index("### 1.3", recovery_start)
    recovery = runbook[recovery_start:recovery_end]

    assert 'RUN_URL="$(gh workflow run deploy-dev.yml' in recovery
    assert "BASH_REMATCH[1]" in recovery
    assert 'gh run watch "$RUN_ID" --exit-status' in recovery
    assert "gh run list" not in recovery
    assert "REBASE_UNVERIFIED_APP=false" in recovery
    assert "The GitHub workflow above is dev-only." in recovery
