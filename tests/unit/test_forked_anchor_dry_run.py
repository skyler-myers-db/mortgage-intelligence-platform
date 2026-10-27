"""The forked-anchor CI deploy, run offline end to end (post-W5 review).

The static contracts in test_ci_install_isolation.py pin each rule. This runs
``scripts/deploy.sh -t pr105-host --dry-run`` in a sandbox clone, with a stub
``databricks`` that fails on anything but ``--version``, and checks the whole
plan: as deploy-dev.yml runs it, every bundle and job command addresses only
``pr105-host``, the scheduled refresh stays PAUSED (verified without the
live-install flag), and the App source check expects the CI target and its
``dev-ci`` root. Run the way an operator would, the target is refused before
any Databricks call.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit.test_ci_install_isolation import CI_TARGET, STAGING, _ci

REPO = Path(__file__).resolve().parents[2]
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_STUB = (
    "#!/usr/bin/env bash\n"
    'printf \'%s\\n\' "$*" >> "$DATABRICKS_STUB_LOG"\n'
    'if [[ "$1" == "--version" ]]; then\n'
    "  echo 'Databricks CLI v-test'\n"
    "  exit 0\n"
    "fi\n"
    'echo "unexpected Databricks dry-run invocation: $*" >&2\n'
    "exit 97\n"
)


def _venv_prefix() -> Path:
    """A prefix whose python imports the deploy tooling baseline (as in the first-install dry-run test)."""

    for candidate in (Path(sys.prefix), REPO / ".venv"):
        python = candidate / "bin" / "python"
        if python.exists() and subprocess.run([str(python), "-c", "import dotenv, pydantic"], capture_output=True).returncode == 0:
            return candidate
    pytest.fail("no interpreter prefix with the deploy tooling deps; run the suite from the project venv")


def _sandbox(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """A clone of HEAD plus the working tree's uncommitted tracked edits, a stub CLI and a base env."""

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", "--shared", str(REPO), str(checkout)], check=True, timeout=120)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    for rel in changed:
        if (REPO / rel).is_file():
            shutil.copy2(REPO / rel, checkout / rel)
    # deploy.sh refuses a dirty source, so the overlay is committed in the clone.
    subprocess.run(["git", "add", "--all"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Dry Run", "-c", "user.email=dry-run@example.invalid", "commit", "--allow-empty", "-qm", "overlay"],
        cwd=checkout,
        check=True,
    )
    with (checkout / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
        exclude.write(".venv\n")
    (checkout / ".venv").symlink_to(_venv_prefix(), target_is_directory=True)
    (checkout / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-1\n"
        "GENIE_SPACE_ID=space-1\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "databricks").write_text(_STUB, encoding="utf-8")
    (bin_dir / "databricks").chmod(0o755)
    log = tmp_path / "databricks.log"
    inherited = ("COV_CORE", "COVERAGE", "GITHUB_", "MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_")
    env = {name: value for name, value in os.environ.items() if not name.startswith(inherited)}
    env.update(
        {
            "APP_ENV": "local",
            # A Python SDK client the dry run builds by mistake must fail
            # offline, never resolve the developer's ~/.databrickscfg.
            "DATABRICKS_CONFIG_FILE": str(tmp_path / "no-databrickscfg"),
            "DATABRICKS_STUB_LOG": str(log),
            "GENIE_SPACE_ID": "space-1",
            "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": "dry-run-runtime-client-id",
            "MIP_ADMIN_EMAILS": "operator@example.invalid",
            "MIP_COTALITY_ID_MASK_SECRET": "stable-local-mask-secret",
            "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-local-action-secret",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }
    )
    return checkout, env, log


def _dry_run(checkout: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/deploy.sh", "-t", CI_TARGET, "--dry-run", "--no-confirm", "--skip-silver", "--skip-smoke"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=300,
    )


def test_the_deploy_dev_dry_run_addresses_only_the_forked_anchor_target(tmp_path: Path) -> None:
    checkout, env, log = _sandbox(tmp_path)

    result = _dry_run(checkout, {**env, **_ci(), "GENIE_SPACE_ID": STAGING["GENIE_SPACE_ID"]})

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    out = _ANSI.sub("", result.stdout)
    commands = [line[2:] for line in out.splitlines() if line.startswith("$ ")]
    targeted = [command for command in commands if " -t " in f" {command} " or "bundle_env" in command]
    assert len(targeted) >= 8, "validate, plan, deploy, bind and the job runs"
    for command in targeted:
        assert re.search(rf"(?:^| )-t {re.escape(CI_TARGET)}(?: |$)", command), command
    assert not any(re.search(r"(?:^| )-t (?!pr105-host\b)", command) for command in commands)
    assert f"target:     {CI_TARGET}" in out
    assert "scheduled refresh: PAUSED" in out
    assert not re.search(r"(?i)unpaus", out), "only the live install may unpause"
    verify = [command for command in commands if "verify_scheduled_refresh_state" in command]
    assert verify == [".venv/bin/python -m tools.databricks.verify_scheduled_refresh_state --expect PAUSED"]
    sources = [command for command in commands if "converge_static_app_source" in command]
    assert sources and all(command.endswith(f"--expected-target {CI_TARGET} --expected-root-leaf dev-ci") for command in sources)
    assert f"deployment bind mip_app {STAGING['MIP_APP_NAME']} -t {CI_TARGET}" in out


def test_an_operator_dry_run_of_the_forked_anchor_target_is_refused(tmp_path: Path) -> None:
    checkout, env, log = _sandbox(tmp_path)

    result = _dry_run(checkout, env)

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    assert f"{CI_TARGET} is deploy-dev.yml only" in result.stdout + result.stderr
    assert set(log.read_text(encoding="utf-8").splitlines()) <= {"--version"}, "refused before any Databricks call"
