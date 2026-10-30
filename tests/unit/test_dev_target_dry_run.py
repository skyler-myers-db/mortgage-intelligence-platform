"""The operator's dev deploy, run offline end to end (M0-PR3).

This runs ``scripts/deploy.sh -t dev --dry-run`` in a sandbox clone
(tests/fixtures/deploy_sandbox.py), with a stub ``databricks`` that fails on
anything but ``--version``, and checks the whole plan: every bundle and job
command addresses ``dev``, the live install's scheduled refresh is unpaused
and verified with the live-install flag, and every App source check expects
the dev target under its own ``dev`` root. The same dry run under GitHub
Actions on the deploy-dev workflow's ref is refused before any Databricks
call: that workflow is parked until a client environment is selected
(M1-PR7).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.fixtures.deploy_sandbox import ANSI, dry_run, planned_commands, sandbox

TARGET = "dev"
IDENTITY = "--expected-target dev --expected-root-leaf dev"


def test_the_operator_dev_dry_run_addresses_only_the_dev_target(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)

    result = dry_run(checkout, env, TARGET)

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    out = ANSI.sub("", result.stdout)
    commands = planned_commands(result.stdout)
    targeted = [command for command in commands if " -t " in f" {command} " or "bundle_env" in command]
    assert len(targeted) >= 8, "validate, plan, deploy, bind and the job runs"
    for command in targeted:
        assert re.search(r"(?:^| )-t dev(?: |$)", command), command
    assert not any(re.search(r"(?:^| )-t (?!dev\b)", command) for command in commands)
    assert f"target:     {TARGET}" in out
    assert "scheduled refresh: UNPAUSED" in out, "the dev target's live install runs the weekly refresh"
    verify = [command for command in commands if "verify_scheduled_refresh_state" in command]
    assert verify == [".venv/bin/python -m tools.databricks.verify_scheduled_refresh_state --expect UNPAUSED --live-install"]
    assert "deployment bind mip_app mip-app -t dev" in out
    # The per-release App source: preflight checks the closure for real (git
    # only), step 1 prints the stage, step 5 the publish, and every
    # activation's converge names the one release folder of the dry run.
    assert "app runtime closure: current" in out
    sha = re.search(r"exact source: ([0-9a-f]{40}) ", out).group(1)
    release = f"{sha[:12]}-dry-run"
    publish = [command for command in commands if "app_release_source publish" in command]
    assert len(publish) == 1 and f"--release-id {release} " in publish[0]
    assert publish[0].endswith(IDENTITY)
    sources = [command for command in commands if "converge_static_app_source" in command]
    folder = f"/Workspace/dry-run/mortgage-intelligence-platform/app-src/{release}"
    # A first-install dry run: the first-install and Agent Evaluation activations.
    assert len(sources) == 2 and all(command.endswith(IDENTITY) for command in sources)
    assert all(f"--source-code-path {folder} --release-id {release} " in command for command in sources)
    prune = [command for command in commands if "app_release_prune" in command]
    assert len(prune) == 1 and prune[0].endswith(IDENTITY)


def test_the_parked_deploy_dev_workflow_is_refused_before_any_databricks_call(tmp_path: Path) -> None:
    checkout, env, log = sandbox(tmp_path)
    ci = {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/deploy-dev.yml@refs/heads/main"}

    result = dry_run(checkout, {**env, **ci}, TARGET)

    assert result.returncode == 2, result.stdout[-2000:] + result.stderr[-2000:]
    assert "deploy-dev.yml is parked until a client environment is selected (M1-PR7)" in result.stderr
    assert not log.exists() or log.read_text(encoding="utf-8") == "", "refused before any Databricks call"
    assert "step 1" not in result.stdout
