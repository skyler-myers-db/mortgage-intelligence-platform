"""A synthetic deploy-dev.yml dispatch, offline, as the runner prints it: no identifier in the public log (M1-PR7, G2).

The repository and its run logs are public. Before each step runs, the runner
prints the step's ``with:`` inputs and its ``env:`` (the job's and the step's)
in the step's log header, and only secrets are masked there: a variable prints
in clear. So this drives a ``phase: plan`` dispatch from deploy-dev.yml itself,
in the offline sandbox (tests/fixtures/deploy_sandbox.py): every step's header
is rendered from the workflow with the job's contexts
(tests/fixtures/workflow_expressions.py), as the runner renders it, and every
step the sandbox can run runs under that env, with each ``$GITHUB_ENV`` write
carried to the steps after it:

1. check-dispatch, whose contexts hold the repository's own variables and
   secrets only;
2. the deploy job's prebuilt-release verify, from a sealed release;
3. the collect step, which reads the client environment name by name;
4. the resolve-client-profile action's two steps;
5. the dispatch preflight, with GitHub's API unreachable (S8's worst case);
6. ``scripts/deploy.sh -t client --phase plan --prebuilt-release`` under the
   step's own env, with ``--dry-run`` added (the sandbox is offline);
7. the plan-report summary of a PlanReport whose full half names foreign
   catalogs and owners.

The contexts hold what GitHub merges into them: the client environment's
profile, plus a repository-level variable and secret that are no profile
name (an identifier-shaped tenant id and a retired token). With the runner's
masking applied in order (every secret's whole value and each of its lines
from the job's start, then each ``::add-mask::``), no resolved identifier,
derived spelling, secret, repository value or foreign name is left in any
header or output line, and the step summary holds none even before masking.

Foreign names: no tool prints a live foreign name through
tools/clients/redact.foreign() yet (plan §4.16), so ``MIP_REDACT_FOREIGN_NAMES=1``
is set but inert. This proves only that the foreign names the profile and the
PlanReport hold (the accepted exposure's leaves, the report's full half) are
masked or kept out of the public half; the handles are tested as a class.
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
from tests.fixtures.prebuilt_release import head, seal
from tests.fixtures.workflow_expressions import evaluate, parse, render
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
TOKEN = "ghs_EXAMPLE_PLACEHOLDER_TOKEN"
# Other teams' objects, owners and emails: in the profile's accepted exposure
# (identifying leaves) and in the PlanReport's full half only.
FOREIGN = ("zyrplax_team_catalog", "Zyrplax Shared Space", "zyrplax-readers", "owner.zyrplax@other-team.example")
# What the repository's own scope adds to every environment's contexts: no profile name.
REPOSITORY_VARS = {"MIP_TENANT_ID": "tenant-zyrplax-EXAMPLE-7f3a91"}
REPOSITORY_SECRETS = {"DATABRICKS_TOKEN": "dapi-EXAMPLE-PLACEHOLDER-retired"}
# Steps the offline sandbox cannot run: their headers are still rendered and checked.
HEADER_ONLY = {"Install the deploy tooling offline and hash-pinned", "Prove the GitHub OIDC deployer identity",
               "Record the quiesce window journal state"}


def _values() -> dict[str, str]:
    exposure = json.loads(json.dumps(EXPOSURE))
    exposure[0]["securable"]["name"] = FOREIGN[1]
    exposure[1]["securable"] = {"type": "CATALOG", "id": FOREIGN[0], "name": FOREIGN[0]}
    exposure[1]["grantee"] = FOREIGN[2]
    return paychex_shaped_profile(MIP_ACCEPTED_EXPOSURE_JSON=json.dumps(exposure))


def _shim(bin_dir: Path) -> None:
    (bin_dir / "python").write_text(f'#!/usr/bin/env bash\nexec {sys.executable} "$@"\n', encoding="utf-8")
    (bin_dir / "python").chmod(0o755)


def _header(step: dict[str, Any], job_env: dict[str, str], context: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    """The runner's log header for ``step`` (its with: or env:), and the env the step runs with."""

    title = step.get("uses") or str(step.get("run", "")).strip().splitlines()[0]
    lines = [f"Run {title}"]
    inputs = {name: render(str(value), context) for name, value in step.get("with", {}).items()}
    if inputs:
        lines.append("  with:")
        lines += [f"    {name}: {line}" for name, value in inputs.items() for line in (value.splitlines() or [""])]
    env = {**job_env, **{name: render(str(value), context) for name, value in step.get("env", {}).items()}}
    if env:
        lines.append("  env:")
        lines += [f"    {name}: {line}" for name, value in env.items() for line in (value.splitlines() or [""])]
    return lines, env


def _github_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


class _Job:
    """One job's log: header and output lines, with the masks the runner registers from its start."""

    def __init__(self, name: str, secrets: dict[str, str]) -> None:
        self.name = name
        self.masks = {line.strip() for value in secrets.values() for line in (value, *value.splitlines()) if line.strip()}
        self.lines: list[tuple[str, str, bool]] = []  # (step, line, is a header line)
        self.results: dict[str, subprocess.CompletedProcess[str]] = {}

    def record(self, step: str, header: list[str], result: subprocess.CompletedProcess[str] | None) -> None:
        self.lines += [(step, line, True) for line in header]
        if result is not None:
            self.results[step] = result
            self.lines += [(step, line, False) for line in ANSI.sub("", result.stdout + result.stderr).splitlines()]

    def shown(self) -> list[tuple[str, str]]:
        """Each line as the public log shows it: registered masks applied in order."""

        masks = set(self.masks)
        shown = []
        for step, line, _header in self.lines:
            if line.startswith("::add-mask::"):
                masks.add(line.removeprefix("::add-mask::"))
                continue
            for mask in sorted(masks, key=len, reverse=True):
                line = line.replace(mask, "***")
            shown.append((step, line))
        return shown


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=600)


@pytest.fixture(scope="module")
def dispatch(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp_path = tmp_path_factory.mktemp("dispatch")
    checkout, base_env, log = sandbox(tmp_path)
    (checkout / ".env.local").unlink()  # the action writes it, as in the deploy job
    bin_dir = Path(base_env["PATH"].split(os.pathsep)[0])
    _shim(bin_dir)
    home, runner_temp = tmp_path / "home", tmp_path / "runner-temp"
    home.mkdir()
    runner_temp.mkdir()
    variables, secrets = split_contexts(_values())
    summary, github_env = tmp_path / "summary.md", tmp_path / "github.env"
    sha = head(checkout)
    digest = seal(checkout, runner_temp / "prebuilt-release", requirements=checkout / "requirements-deploy.hashes.txt")
    runner = {
        **{name: value for name, value in base_env.items() if name not in ("DATABRICKS_AGENT_RUNTIME_CLIENT_ID",)},
        "HOME": str(home),
        "PYTHONPATH": str(checkout),
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_ACTIONS": "true",
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_ENV": str(github_env),
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/deploy-dev.yml@refs/heads/main",
        "GITHUB_SHA": sha,
        "GITHUB_ACTOR_ID": "4242",
        # Unreachable: S8's worst case, never a network call out of a test.
        "GITHUB_API_URL": "https://127.0.0.1:9",
    }
    inputs = {
        "client": CLIENT, "phase": "plan", "owner_attestation": "", "operation_acks": "",
        "expected_active_deployment_sha256": "", "protections_unverified_ack": "", "skip_silver": True,
        "skip_smoke": True, "rebase_unverified_app": False, "acknowledge_single_maintainer_break_glass": True,
    }
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs: dict[str, _Job] = {}
    scopes = {
        "check-dispatch": (REPOSITORY_VARS, REPOSITORY_SECRETS),
        "deploy": ({**REPOSITORY_VARS, **variables}, {**REPOSITORY_SECRETS, **secrets}),
    }
    for job_name, (job_vars, job_secrets) in scopes.items():
        job = jobs[job_name] = _Job(job_name, {**job_secrets, "github_token": TOKEN})
        context: dict[str, Any] = {
            "vars": job_vars, "secrets": {**job_secrets, "github_token": TOKEN}, "inputs": inputs,
            "needs": {"build": {"outputs": {"manifest-sha256": digest}}}, "steps": {},
            "runner": {"temp": str(runner_temp), "debug": ""}, "github": {"token": TOKEN},
        }
        job_env = {name: str(value) for name, value in workflow["jobs"][job_name].get("env", {}).items()}
        for step in workflow["jobs"][job_name]["steps"]:
            name = step.get("name") or step["uses"]
            condition = str(step.get("if", "success()")).removeprefix("${{").removesuffix("}}")
            if not evaluate(parse(condition), context):
                continue
            header, env = _header(step, job_env, context)
            step_env = {**runner, **_github_file(github_env), **env}
            if name == "Resolve the client profile":
                job.record(name, header, None)
                action_context = {**context, "inputs": {key: render(str(value), context) for key, value in step["with"].items()}}
                output = tmp_path / "profile.output"
                for inner in yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]:
                    inner_header, inner_env = _header(inner, job_env, action_context)
                    result = _run(["bash", "-c", inner["run"]], cwd=checkout,
                                  env={**runner, **_github_file(github_env), **inner_env, "GITHUB_OUTPUT": str(output)})
                    job.record(f"{name} / {inner['name']}", inner_header, result)
                context["steps"]["profile"] = {"outputs": _github_file(output)}
            elif name == "Run the client phase":
                argv = ["bash", "scripts/deploy.sh", "-t", "client", "--phase", "plan", "--no-confirm", "--dry-run",
                        "--skip-silver", "--skip-smoke", "--prebuilt-release", str(runner_temp / "prebuilt-release")]
                job.record(name, header, _run(argv, cwd=checkout, env=step_env))
            elif "run" in step and name not in HEADER_ONLY:
                job.record(name, header, _run(["bash", "-c", step["run"]], cwd=checkout, env=step_env))
            else:
                job.record(name, header, None)
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
    jobs["deploy"].record(
        "Publish the public summary",
        [],
        _run(["python", "-m", "tools.databricks.plan_report_publish", "summary", "--public", str(public), "--out", str(summary)],
             cwd=checkout, env=runner),
    )
    profile = resolve(from_github(variables, secrets, environment=CLIENT))
    return {
        "jobs": jobs,
        "summary": summary.read_text(encoding="utf-8"),
        "log": log,
        "runner_temp": runner_temp,
        "secrets": [value for name, value in secrets.items() if value and FIELD_CLASS[name] is not FieldClass.PUBLIC],
        "identifiers": sorted({*views.masks(profile), *views.derived_masks(profile)}, key=len, reverse=True),
    }


def _forbidden(dispatch: dict[str, Any]) -> list[str]:
    return [
        *dispatch["identifiers"], *dispatch["secrets"], *REPOSITORY_VARS.values(), *REPOSITORY_SECRETS.values(),
        *FOREIGN, *(name.lower() for name in FOREIGN),
    ]


def test_the_synthetic_dispatch_shape_passes_every_step(dispatch: dict[str, Any]) -> None:
    for job in dispatch["jobs"].values():
        for name, result in job.results.items():
            assert result.returncode == 0, (job.name, name, result.stdout[-3000:], result.stderr[-3000:])
    deploy = dispatch["jobs"]["deploy"].results
    assert "Collect the client's profile from its environment" in deploy, "the collect step ran"
    assert "Refuse a profile name set at repository or organization level" in dispatch["jobs"]["check-dispatch"].results
    assert dispatch["log"].read_text(encoding="utf-8").splitlines() == ["--version"], "no Databricks call"
    assert "environment protections: environment_protections_unreadable" in deploy["Preflight the dispatch"].stdout
    run = ANSI.sub("", deploy["Run the client phase"].stdout)
    assert "plan phase complete: stopped before the lease; no governed resource was changed" in run
    assert "staged source files are this checkout's commit" in deploy["Verify the prebuilt release against its manifest"].stdout
    assert not (dispatch["runner_temp"] / "mip-client-vars.json").exists(), "the last step removes the profile files"


def test_the_public_log_headers_and_output_hold_no_identifier_secret_or_repository_value(dispatch: dict[str, Any]) -> None:
    """The runner prints each step's with: and env: before it runs; only secrets are masked there."""

    for job in dispatch["jobs"].values():
        for name, line in job.shown():
            for value in _forbidden(dispatch):
                assert value not in line, (job.name, name, "a value escaped its mask")


def test_no_step_header_holds_a_whole_context(dispatch: dict[str, Any]) -> None:
    """A whole context would print every name it holds, the repository's own included; no header does."""

    for job in dispatch["jobs"].values():
        headers = [line for _step, line, is_header in job.lines if is_header]
        assert headers, job.name
        for line in headers:
            for name in (*REPOSITORY_VARS, *REPOSITORY_SECRETS):
                assert name not in line, (job.name, line[:120])


def test_every_step_but_the_dry_run_commands_prints_no_value_even_unmasked(dispatch: dict[str, Any]) -> None:
    """The steps print codes, digests, names and paths only; headers are the runner's (masked above)."""

    for job in dispatch["jobs"].values():
        for name, result in job.results.items():
            if name == "Run the client phase":
                continue
            for line in (result.stdout + result.stderr).splitlines():
                if line.startswith("::add-mask::"):
                    continue
                for value in _forbidden(dispatch):
                    assert value not in line, (job.name, name)


def test_the_step_summary_holds_no_value_even_before_masking(dispatch: dict[str, Any]) -> None:
    summary = dispatch["summary"]
    assert "### deploy-dev dispatch preflight (--phase plan)" in summary
    assert "### Bundle plan gate (pre-lease): refused" in summary and "not_adopted" in summary
    for value in _forbidden(dispatch):
        assert value not in summary


def test_foreign_name_handles_as_a_class_the_jobs_switch_is_set_but_no_probe_prints_through_it_yet() -> None:
    switch = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]["env"]["MIP_REDACT_FOREIGN_NAMES"]
    names = ForeignNames(enabled=switch == "1")
    assert [names.handle(name) for name in FOREIGN[:2]] == ["foreign#1", "foreign#2"]
    assert names.mapping() == {"foreign#1": FOREIGN[0], "foreign#2": FOREIGN[1]}, "the mapping, for the full report only"
