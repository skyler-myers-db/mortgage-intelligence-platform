"""No known os.environ writer leaks a name to the next test, and no profile env inherits one.

Code under test that writes os.environ directly (Lakebase connection minting,
the exact OAuth M2M binding, .env.local loaders) leaked names to the next test
on the same xdist worker. ``monkeypatch.delenv(name, raising=False)`` records
nothing for an absent name, and in CI these names are never ambient, so the
writes were never undone. A leaked LAKEBASE_DATABASE then made a later dry run
refuse the client profile ("must agree"): PR #321, 2026-11-05.

The child pytest runs below start from an environment with none of these
names, which is the CI case.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tests.fixtures.deploy_sandbox import inherited
from tests.fixtures.env_isolation import (
    EXACT_M2M_BINDING_ENV,
    LAKEBASE_CONNECTION_ENV,
    restore_on_teardown,
)
from tests.fixtures.env_leak_probe import OUT_ENV
from tools.databricks.m2m_workspace_auth import _SENSITIVE_ENV_MARKERS

REPO = Path(__file__).resolve().parents[2]

# Modules whose code under test writes os.environ, and what each one leaked.
WRITER_MODULES = (
    # LAKEBASE_DATABASE, LAKEBASE_PORT, LAKEBASE_SSLMODE and their PG* twins.
    "tests/unit/test_ai_gateway_exact_proof.py",
    # LAKEBASE_USER and PGUSER; DATABRICKS_WAREHOUSE_ID from .env.local.
    "tests/unit/test_sync_lifecycle_warehouse_cli.py",
    # LAKEBASE_PORT, PGPORT and PGSSLMODE.
    "tests/unit/test_agent_eval_runner.py",
    # DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET and DATABRICKS_AUTH_TYPE.
    "tests/unit/test_verify_agent_proxy_identity_boundary.py",
    # DATABRICKS_HOST from .env.local.
    "tests/unit/test_provision_genie_space.py",
    # DATABRICKS_HOST, DATABRICKS_AUTH_TYPE, DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET.
    "tests/unit/test_verify_agent_proxy_uc_boundary_dual_authority.py",
    "tests/unit/test_verify_agent_runtime_uc_boundary_dual_authority.py",
    # DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET.
    "tests/unit/test_verify_agent_runtime_identity_boundary.py",
    "tests/unit/test_verify_verifier_identity_boundary.py",
    # The exact M2M binder itself; its tests already restore what it writes.
    "tests/unit/test_m2m_workspace_auth.py",
)
# A tools module that writes the exact OAuth M2M binding into os.environ: it
# defines or calls bind_exact_workspace_m2m_auth, or sets the client secret.
_EXACT_M2M_WRITE = re.compile(
    r"\bbind_exact_workspace_m2m_auth\(|os\.environ\[\s*\"DATABRICKS_CLIENT_SECRET\"\s*\]\s*="
)
# Dry runs and in-process profile reads whose env once dropped ambient names
# by prefix alone, so a leaked unprefixed profile name refused the profile.
PROFILE_ENV_READERS = (
    "tests/unit/test_deploy_dev_workflow_contract.py::test_first_install_dry_run_executes_no_databricks_operations",
    "tests/unit/test_deploy_dev_workflow_contract.py::test_deploy_preflight_requires_no_runtime_hmac_input[sandbox-no-inputs]",
    "tests/unit/test_python_deps_check.py::test_a_real_deploy_with_a_drifted_override_stops_at_step_0_before_any_workspace_call",
    "tests/unit/test_catalog_storage_root.py::test_bundle_env_renders_the_profiles_root",
)
# The unprefixed profile names (tools/clients/schema.py), as a leak would leave them.
LEAKED_PROFILE_NAMES = {
    "LAKEBASE_DATABASE": "leaked-by-an-earlier-test",
    "LAKEBASE_INSTANCE_NAME": "leaked-by-an-earlier-test",
    "APP_ENV": "leaked-by-an-earlier-test",
}


def _ci_shaped_env(extra: Mapping[str, str]) -> dict[str, str]:
    """This process's env without any name a leak could have left, plus ``extra``."""

    env = {
        name: value
        for name, value in os.environ.items()
        if not inherited(name)
        and name not in LAKEBASE_CONNECTION_ENV
        and name not in EXACT_M2M_BINDING_ENV
        and not name.startswith(("PYTEST_", "COV_", "COVERAGE"))
        # The M2M binding sweeps these out of os.environ: absent, nothing is swept.
        and not any(marker in name.upper() for marker in _SENSITIVE_ENV_MARKERS)
    }
    env.update(PYTHONPATH=str(REPO), **extra)
    return env


def _child_pytest(
    tmp_path: Path, targets: Sequence[str], env: Mapping[str, str], *plugins: str
) -> subprocess.CompletedProcess[str]:
    loads = [arg for plugin in plugins for arg in ("-p", plugin)]
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *loads,
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "-o",
            "tmp_path_retention_policy=failed",
            "--basetemp",
            str(tmp_path / "child"),
            *targets,
        ],
        cwd=REPO,
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )


def _tail(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout + result.stderr)[-6000:]


def test_no_writer_module_leaves_os_environ_changed(tmp_path: Path) -> None:
    probe = tmp_path / "probe.jsonl"
    result = _child_pytest(
        tmp_path, WRITER_MODULES, _ci_shaped_env({OUT_ENV: str(probe)}), "tests.fixtures.env_leak_probe"
    )

    assert result.returncode == 0, _tail(result)
    records = [json.loads(line) for line in probe.read_text(encoding="utf-8").splitlines()]
    assert {record["test"].split("::", 1)[0] for record in records} == set(WRITER_MODULES)
    leaks = {
        record["test"]: {kind: record[kind] for kind in ("added", "removed", "changed") if record[kind]}
        for record in records
        if record["added"] or record["removed"] or record["changed"]
    }
    assert leaks == {}, "these tests left os.environ changed for the next test on the worker"


def test_every_exact_m2m_binding_writer_has_its_tests_probed() -> None:
    writers = sorted(
        path.relative_to(REPO).as_posix()
        for path in (REPO / "tools").rglob("*.py")
        if _EXACT_M2M_WRITE.search(path.read_text(encoding="utf-8"))
    )
    # The scan is real: the binder and a writer the probe covered first both match.
    assert {
        "tools/databricks/m2m_workspace_auth.py",
        "tools/databricks/verify_agent_proxy_identity_boundary.py",
    } <= set(writers), writers

    test_modules = {writer: f"tests/unit/test_{Path(writer).stem}.py" for writer in writers}
    missing = {
        writer: module
        for writer, module in test_modules.items()
        if not (REPO / module).is_file() or module not in WRITER_MODULES
    }
    assert missing == {}, "add each exact M2M writer's test module to WRITER_MODULES"


def test_a_leaked_profile_name_never_refuses_a_dry_run_or_profile_read(tmp_path: Path) -> None:
    result = _child_pytest(tmp_path, PROFILE_ENV_READERS, _ci_shaped_env(LEAKED_PROFILE_NAMES))

    assert result.returncode == 0, _tail(result)
    assert f"{len(PROFILE_ENV_READERS)} passed" in result.stdout, _tail(result)


@pytest.mark.parametrize("ambient", [None, "ambient-value"])
def test_restore_on_teardown_undoes_a_direct_write(ambient: str | None) -> None:
    name = "MIP_ENV_ISOLATION_PROBE"
    with pytest.MonkeyPatch.context() as outer:
        outer.setenv(name, ambient or "")  # so this test leaks nothing even when the helper fails
        if ambient is None:
            outer.delenv(name)

        with pytest.MonkeyPatch.context() as monkeypatch:
            restore_on_teardown(monkeypatch, [name])
            assert os.environ.get(name) == ambient, "registering changes nothing now"
            os.environ[name] = "written-by-code-under-test"

        assert os.environ.get(name) == ambient

