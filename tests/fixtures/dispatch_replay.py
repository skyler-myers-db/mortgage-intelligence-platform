"""A deploy-dev.yml dispatch, replayed offline step by step as the runner logs it (M1-PR7; M3-PR4).

Moved from tests/unit/test_public_summary_redaction.py so the second-client
portability proof (tests/unit/test_client_portability_proof.py) replays the
same workflow for another client. ``replay`` drives the ``check-dispatch``
and ``deploy`` jobs of deploy-dev.yml itself in the offline sandbox
(tests/fixtures/deploy_sandbox.py): every step's header is rendered from the
workflow with the job's contexts (tests/fixtures/workflow_expressions.py), as
the runner renders it, and every step the sandbox can run runs under that
env, with each ``$GITHUB_ENV`` write carried to the steps after it:

1. check-dispatch, whose contexts hold the repository's own variables and
   secrets only;
2. the deploy job's prebuilt-release verify, from a sealed release;
3. the collect step, which reads the client environment name by name;
4. the resolve-client-profile action's two steps;
5. the dispatch preflight, with GitHub's API unreachable (S8's worst case);
6. ``scripts/deploy.sh -t client --phase <phase> --prebuilt-release`` under
   the step's own env, with ``--dry-run`` added (the sandbox is offline).

``Job.shown`` applies the runner's masking in order: every secret's whole
value and each of its lines from the job's start, then each ``::add-mask::``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.fixtures.client_profiles import split_contexts
from tests.fixtures.deploy_sandbox import ANSI, sandbox
from tests.fixtures.prebuilt_release import head, seal
from tests.fixtures.workflow_expressions import evaluate, parse, render

REPO = Path(__file__).resolve().parents[2]
ACTION = REPO / ".github" / "actions" / "resolve-client-profile" / "action.yml"
WORKFLOW = REPO / ".github" / "workflows" / "deploy-dev.yml"
REPOSITORY = "example-org/mortgage-intelligence-platform"
TOKEN = "ghs_EXAMPLE_PLACEHOLDER_TOKEN"
# Steps the offline sandbox cannot run: their headers are still rendered and checked.
HEADER_ONLY = {"Install the deploy tooling offline and hash-pinned", "Prove the GitHub OIDC deployer identity",
               "Record the quiesce window journal state", "Freeze or thaw the emergency publisher"}
DEFAULT_INPUTS: dict[str, Any] = {
    "phase": "plan", "owner_attestation": "", "operation_acks": "",
    "expected_active_deployment_sha256": "", "protections_unverified_ack": "", "skip_silver": True,
    "skip_smoke": True, "rebase_unverified_app": False, "acknowledge_single_maintainer_break_glass": True,
}


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


def github_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


class Job:
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


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=600)


@dataclass
class Replay:
    jobs: dict[str, Job]
    checkout: Path
    runner: dict[str, str]
    runner_temp: Path
    summary: Path
    log: Path
    variables: dict[str, str]
    secrets: dict[str, str]
    sha: str
    inputs: dict[str, Any] = field(default_factory=dict)


def replay(
    tmp_path: Path,
    *,
    values: Mapping[str, str],
    client: str,
    inputs: Mapping[str, Any] | Callable[[str], Mapping[str, Any]] | None = None,
    repository_vars: Mapping[str, str] | None = None,
    repository_secrets: Mapping[str, str] | None = None,
    ambient: Mapping[str, str] | None = None,
    prepare: Callable[[Path, dict[str, str]], None] | None = None,
    runner_env: Mapping[str, str] | None = None,
) -> Replay:
    """One dispatch of ``client`` with the profile ``values`` held in its GitHub Environment.

    ``inputs`` overrides the dispatch inputs (a callable gets the sandbox's
    head sha). ``prepare(checkout, base_env)`` edits and commits the clone
    before it is sealed; ``runner_env`` adds names every step inherits, as the
    runner's own environment does (never printed in a header).
    """

    with pytest.MonkeyPatch.context() as patched:
        for name, value in (ambient or {}).items():
            patched.setenv(name, value)
        checkout, base_env, log = sandbox(tmp_path)
    if prepare is not None:
        prepare(checkout, base_env)
    (checkout / ".env.local").unlink()  # the action writes it, as in the deploy job
    bin_dir = Path(base_env["PATH"].split(os.pathsep)[0])
    _shim(bin_dir)
    home, runner_temp = tmp_path / "home", tmp_path / "runner-temp"
    home.mkdir()
    runner_temp.mkdir()
    variables, secrets = split_contexts(dict(values))
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
        **(runner_env or {}),
    }
    given = inputs(sha) if callable(inputs) else (inputs or {})
    dispatched = {**DEFAULT_INPUTS, "client": client, **given}
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs: dict[str, Job] = {}
    own_vars, own_secrets = dict(repository_vars or {}), dict(repository_secrets or {})
    scopes = {
        "check-dispatch": (own_vars, own_secrets),
        "deploy": ({**own_vars, **variables}, {**own_secrets, **secrets}),
    }
    for job_name, (job_vars, job_secrets) in scopes.items():
        job = jobs[job_name] = Job(job_name, {**job_secrets, "github_token": TOKEN})
        context: dict[str, Any] = {
            "vars": job_vars, "secrets": {**job_secrets, "github_token": TOKEN}, "inputs": dispatched,
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
            step_env = {**runner, **github_file(github_env), **env}
            if name == "Resolve the client profile":
                job.record(name, header, None)
                action_context = {**context, "inputs": {key: render(str(value), context) for key, value in step["with"].items()}}
                output = tmp_path / "profile.output"
                for inner in yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]:
                    inner_header, inner_env = _header(inner, job_env, action_context)
                    result = run(["bash", "-c", inner["run"]], cwd=checkout,
                                 env={**runner, **github_file(github_env), **inner_env, "GITHUB_OUTPUT": str(output)})
                    job.record(f"{name} / {inner['name']}", inner_header, result)
                context["steps"]["profile"] = {"outputs": github_file(output)}
            elif name == "Run the client phase":
                argv = ["bash", "scripts/deploy.sh", "-t", "client", "--phase", str(dispatched["phase"]), "--no-confirm",
                        "--dry-run", "--skip-silver", "--skip-smoke", "--prebuilt-release", str(runner_temp / "prebuilt-release")]
                job.record(name, header, run(argv, cwd=checkout, env=step_env))
            elif "run" in step and name not in HEADER_ONLY:
                job.record(name, header, run(["bash", "-c", step["run"]], cwd=checkout, env=step_env))
            else:
                job.record(name, header, None)
    return Replay(jobs, checkout, runner, runner_temp, summary, log, variables, secrets, sha, dispatched)
