"""A synthetic deploy-dev.yml dispatch, offline: the public log and summary hold no identifier (M1-PR7, G2).

The repository and its run logs are public. This drives a ``phase: plan``
dispatch's deploy-job steps in the offline sandbox (tests/fixtures/
deploy_sandbox.py) with a synthetic client profile whose accepted exposure
names other teams' objects:

1. the resolve-client-profile action's two steps, as the runner runs them;
2. the dispatch preflight, with GitHub's API unreachable (spike S8's worst
   case: the plan records ``environment_protections_unreadable``);
3. ``scripts/deploy.sh -t client --phase plan --dry-run --prebuilt-release``
   under the workflow's ref with ``MIP_REDACT_FOREIGN_NAMES=1``, from a sealed
   prebuilt release;
4. the plan-report summary of a PlanReport whose full half names foreign
   catalogs and owners.

The runner masks each secret's whole value and every ``::add-mask::`` value,
for the lines that follow it. Here, with that masking applied in order, no
resolved identifier, no spelling derived from one, no secret and no foreign
name is left in any captured line; the step summary holds none even before
masking, and every line but deploy.sh's dry-run commands holds none either.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures.client_profiles import EXPOSURE, paychex_shaped_profile, split_contexts
from tests.fixtures.deploy_sandbox import ANSI, sandbox
from tests.fixtures.prebuilt_release import seal
from tools.clients import profile as views
from tools.clients.environment import from_github, resolve
from tools.clients.redact import ForeignNames
from tools.clients.schema import FIELD_CLASS, FieldClass
from tools.databricks.deploy_plan_report import LookupRecord, PlanReport, Refusal

REPO = Path(__file__).resolve().parents[2]
ACTION = REPO / ".github" / "actions" / "resolve-client-profile" / "action.yml"
WORKFLOW = REPO / ".github" / "workflows" / "deploy-dev.yml"
CLIENT = "client-one"
REPOSITORY = "example-org/mortgage-intelligence-platform"
# Other teams' objects, owners and emails: in the profile's accepted exposure
# (identifying leaves) and in the PlanReport's full half only.
FOREIGN = ("zyrplax_team_catalog", "Zyrplax Shared Space", "zyrplax-readers", "owner.zyrplax@other-team.example")


def _values() -> dict[str, str]:
    exposure = json.loads(json.dumps(EXPOSURE))
    exposure[0]["securable"]["name"] = FOREIGN[1]
    exposure[1]["securable"] = {"type": "CATALOG", "id": FOREIGN[0], "name": FOREIGN[0]}
    exposure[1]["grantee"] = FOREIGN[2]
    return paychex_shaped_profile(MIP_ACCEPTED_EXPOSURE_JSON=json.dumps(exposure))


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=600)


def _shim(bin_dir: Path) -> None:
    (bin_dir / "python").write_text(f'#!/usr/bin/env bash\nexec {sys.executable} "$@"\n', encoding="utf-8")
    (bin_dir / "python").chmod(0o755)


@pytest.fixture(scope="module")
def dispatch(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp_path = tmp_path_factory.mktemp("dispatch")
    checkout, base_env, log = sandbox(tmp_path)
    (checkout / ".env.local").unlink()  # the action writes it, as in the deploy job
    bin_dir = Path(base_env["PATH"].split(os.pathsep)[0])
    _shim(bin_dir)
    home = tmp_path / "home"
    home.mkdir()
    values = _values()
    variables, secrets = split_contexts(values)
    summary, github_env, github_output = tmp_path / "summary.md", tmp_path / "github.env", tmp_path / "output"
    runner = {
        **{name: value for name, value in base_env.items() if name not in ("DATABRICKS_AGENT_RUNTIME_CLIENT_ID",)},
        "HOME": str(home),
        "PYTHONPATH": str(checkout),
        "GITHUB_ACTIONS": "true",
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_ENV": str(github_env),
        "GITHUB_OUTPUT": str(github_output),
        "MIP_REDACT_FOREIGN_NAMES": yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]["env"][
            "MIP_REDACT_FOREIGN_NAMES"
        ],
    }
    contexts = {"MIP_VARS_JSON": json.dumps(variables), "MIP_SECRETS_JSON": json.dumps(secrets), "MIP_CLIENT_ENVIRONMENT": CLIENT}
    steps: list[tuple[str, subprocess.CompletedProcess[str]]] = []
    for step in yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]:
        steps.append((step["name"], _run(["bash", "-c", step["run"]], cwd=checkout, env={**runner, **contexts})))
    head = _run(["git", "rev-parse", "HEAD"], cwd=checkout, env=runner).stdout.strip()
    preflight = {
        **runner,
        **contexts,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/deploy-dev.yml@refs/heads/main",
        "GITHUB_SHA": head,
        "GITHUB_ACTOR_ID": "4242",
        "GITHUB_TOKEN": "ghs_EXAMPLE_PLACEHOLDER_TOKEN",
        # Unreachable: S8's worst case, never a network call out of a test.
        "GITHUB_API_URL": "https://127.0.0.1:9",
        "MIP_DISPATCH_BREAK_GLASS": "true",
    }
    steps.append(
        (
            "Preflight the dispatch",
            _run(
                [
                    "python", "-m", "tools.clients.dispatch_preflight", "--phase", "plan", "--environment", CLIENT,
                    "--vars-json", "env:MIP_VARS_JSON", "--secrets-json", "env:MIP_SECRETS_JSON",
                    "--github-env", str(github_env), "--step-summary", str(summary),
                ],
                cwd=checkout,
                env=preflight,
            ),
        )
    )
    release = tmp_path / "prebuilt"
    digest = seal(checkout, release, requirements=checkout / "requirements-deploy.hashes.txt")
    deploy_env = {
        **runner,
        "GITHUB_WORKFLOW_REF": preflight["GITHUB_WORKFLOW_REF"],
        "DATABRICKS_CONFIG_PROFILE": "DEFAULT",
        "MIP_PREBUILT_RELEASE_SHA256": digest,
        **{name: value for name, value in secrets.items() if FIELD_CLASS[name] is FieldClass.SECRET},
    }
    steps.append(
        (
            "Run the client phase",
            _run(
                [
                    "bash", "scripts/deploy.sh", "-t", "client", "--phase", "plan", "--no-confirm", "--dry-run",
                    "--skip-silver", "--skip-smoke", "--prebuilt-release", str(release),
                ],
                cwd=checkout,
                env=deploy_env,
            ),
        )
    )
    report = PlanReport(
        position="pre-lease",
        app_resource="apps.mip_app",
        app_bound=False,
        plan_sha256="b" * 64,
        refusals=(Refusal("not_adopted", "apps.mip_app", "adopt it first", detail={"owner": FOREIGN[3], "catalog": FOREIGN[0]}),),
        lookups=(LookupRecord("catalogs.mip", "catalogs", (FOREIGN[0],), "metastore", "catalogs.get", "exists"),),
    )
    public = tmp_path / "public.json"
    public.write_text(json.dumps(report.public_summary()), encoding="utf-8")
    steps.append(
        (
            "Publish the public summary",
            _run(
                ["python", "-m", "tools.databricks.plan_report_publish", "summary", "--public", str(public), "--out", str(summary)],
                cwd=checkout,
                env=runner,
            ),
        )
    )
    profile = resolve(from_github(variables, secrets, environment=CLIENT))
    return {
        "steps": steps,
        "summary": summary.read_text(encoding="utf-8"),
        "log": log,
        "secrets": [value for name, value in secrets.items() if value and FIELD_CLASS[name] is not FieldClass.PUBLIC],
        "identifiers": sorted({*views.masks(profile), *views.derived_masks(profile)}, key=len, reverse=True),
    }


def _forbidden(dispatch: dict[str, Any]) -> list[str]:
    return [*dispatch["identifiers"], *dispatch["secrets"], *FOREIGN, *(name.lower() for name in FOREIGN)]


def _masked_log(dispatch: dict[str, Any]) -> list[tuple[str, str]]:
    """Each step's output as the public log shows it: registered masks applied in order."""

    masks = sorted(set(dispatch["secrets"]), key=len, reverse=True)
    shown = []
    for name, result in dispatch["steps"]:
        for line in ANSI.sub("", result.stdout + result.stderr).splitlines():
            if line.startswith("::add-mask::"):
                masks = sorted({*masks, line.removeprefix("::add-mask::")}, key=len, reverse=True)
                continue
            for mask in masks:
                line = line.replace(mask, "***")
            shown.append((name, line))
    return shown


def test_the_synthetic_dispatch_shape_passes_every_step(dispatch: dict[str, Any]) -> None:
    for name, result in dispatch["steps"]:
        assert result.returncode == 0, (name, result.stdout[-3000:], result.stderr[-3000:])
    assert dispatch["log"].read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    preflight = dict(dispatch["steps"])["Preflight the dispatch"].stdout
    assert "environment protections: environment_protections_unreadable" in preflight, "S8's worst case, recorded"
    run = ANSI.sub("", dict(dispatch["steps"])["Run the client phase"].stdout)
    assert "plan phase complete: read-only, stopped before the lease" in run
    assert "[prebuilt-release] provenance: " in run and "staged source files are this checkout's commit" in run


def test_the_public_log_holds_no_identifier_secret_or_foreign_name(dispatch: dict[str, Any]) -> None:
    for name, line in _masked_log(dispatch):
        for value in _forbidden(dispatch):
            assert value not in line, (name, "a value escaped its mask")


def test_every_step_but_the_dry_run_commands_prints_no_value_even_unmasked(dispatch: dict[str, Any]) -> None:
    """The mask, the write, the preflight and the summary print codes, digests, names and paths only."""

    for name, result in dispatch["steps"]:
        if name == "Run the client phase":
            continue
        for line in (result.stdout + result.stderr).splitlines():
            if line.startswith("::add-mask::"):
                continue
            for value in _forbidden(dispatch):
                assert value not in line, name


def test_the_step_summary_holds_no_value_even_before_masking(dispatch: dict[str, Any]) -> None:
    summary = dispatch["summary"]
    assert "### deploy-dev dispatch preflight (--phase plan)" in summary
    assert "### Bundle plan gate (pre-lease): refused" in summary and "not_adopted" in summary
    for value in _forbidden(dispatch):
        assert value not in summary


def test_foreign_names_print_as_handles_under_the_jobs_redaction_switch() -> None:
    switch = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]["env"]["MIP_REDACT_FOREIGN_NAMES"]
    names = ForeignNames(enabled=switch == "1")
    assert [names.handle(name) for name in FOREIGN[:2]] == ["foreign#1", "foreign#2"]
    assert names.mapping() == {"foreign#1": FOREIGN[0], "foreign#2": FOREIGN[1]}, "the mapping, for the full report only"
