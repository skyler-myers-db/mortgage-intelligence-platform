"""Every read-only check or diff mode keeps drift apart from a probe that failed.

A pre-stop probe reads the exit code: 0 means the live state is exact, 3
(``CHECK_DRIFT``) means convergence or the App update would change it, and
anything else means the probe could not decide. Python exits 1 on an uncaught
exception and argparse 2 on a usage error, so a failure must propagate out of
``main`` and never be mapped onto 0 or 3.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied

from tests.unit import test_converge_app_lakebase_sync_access as sync_tests
from tests.unit.test_app_resource_bindings import _summary
from tests.unit.test_app_resource_bindings_diff import _live
from tools.databricks import app_lakebase_sync_access_check as sync_check
from tools.databricks import campaign_treatment_access_check as treatment_check
from tools.databricks import converge_app_lakebase_sync_access as sync_converger
from tools.databricks import converge_campaign_treatment_access as treatment_converger
from tools.databricks import converge_lakebase_oauth_role as role_converger
from tools.databricks import ensure_campaign_treatment_table as table_module
from tools.databricks.app_resource_bindings import build_resource_binding_payload
from tools.databricks.check_exit_codes import CHECK_DRIFT, CHECK_EXACT

_REPO = Path(__file__).resolve().parents[2]


def test_drift_has_a_code_no_failure_or_usage_error_can_produce() -> None:
    assert CHECK_EXACT == 0
    assert CHECK_DRIFT not in {0, 1, 2}


def _denied(*_: object, **__: object) -> Any:
    raise PermissionDenied("the deployer is not authorized")


def _treatment_access(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    monkeypatch.setattr(treatment_check, "deployment_workspace_client", _denied)
    return lambda: treatment_converger.main(
        ["--check", "--principal", "app-client", "--mode", "quiesce"]
    )


def _treatment_table(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    monkeypatch.setattr(table_module, "deployment_workspace_client", _denied)
    return lambda: table_module.main(["--warehouse-id", "warehouse-1", "--check"])


def _sync_access(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    monkeypatch.setattr(sync_check, "WorkspaceClient", _denied)
    argv = [
        "--warehouse-id", sync_tests.WAREHOUSE_ID,
        "--app-application-id", sync_tests.APP_ID,
        "--app-scim-id", sync_tests.APP_SCIM_ID,
        "--sync-catalog", sync_tests.CATALOG,
        "--sync-schema", sync_tests.SYNC_SCHEMA,
        "--sync-tables", ",".join(sync_tests.SYNC_TABLES),
        "--mode", "quiesce",
        "--check",
    ]  # fmt: skip
    return lambda: sync_converger.main(argv)


def _oauth_role(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    monkeypatch.setattr("databricks.sdk.WorkspaceClient", _denied)
    monkeypatch.setattr("databricks.sdk.config.Config", lambda **_: None)
    argv = [
        "--lakebase-instance", "instance",
        "--lakebase-database", "database",
        "--application-id", "app-client",
        "--role-contract", "app",
        "--check",
    ]  # fmt: skip
    return lambda: role_converger.main(argv)


@pytest.mark.parametrize(
    "entry",
    [_treatment_access, _treatment_table, _sync_access, _oauth_role],
    ids=["treatment-access", "treatment-table", "sync-access", "oauth-role"],
)
def test_an_authorization_failure_propagates_out_of_every_check(
    entry: Callable[[pytest.MonkeyPatch], Callable[[], int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = entry(monkeypatch)

    with pytest.raises(PermissionDenied):
        run()


def _payload() -> dict[str, Any]:
    return build_resource_binding_payload(_summary(), app_name="mip-app-staging")


def _bindings_diff(tmp_path: Path, live: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    expected = _payload()
    expected_path, live_path = tmp_path / "expected.json", tmp_path / "live.json"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    live_path.write_text(json.dumps(live), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.databricks.app_resource_bindings",
            "diff",
            "--expected",
            str(expected_path),
            "--live",
            str(live_path),
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_bindings_diff_process_exits_0_on_equal_3_on_drift_and_1_on_error(
    tmp_path: Path,
) -> None:
    exact = _live(_payload())

    assert _bindings_diff(tmp_path, exact).returncode == CHECK_EXACT
    assert _bindings_diff(tmp_path, {**exact, "resources": []}).returncode == CHECK_DRIFT
    wrong_app = _bindings_diff(tmp_path, {**exact, "name": "another-app"})
    assert wrong_app.returncode == 1
    assert "live App identity does not match" in wrong_app.stderr
