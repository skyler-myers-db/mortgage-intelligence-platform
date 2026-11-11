"""ci.yml runs the second-client portability proof once per push, in its own job (M3-PR4).

The proof (tests/unit/test_client_portability_proof.py, with its in-process
half tests/unit/test_client_portability_integrity.py) replays three
deploy-dev.yml dispatches offline, so it has a job of its own,
``client-portability-proof``, and backend-tests ignores both modules instead
of running them a second time. This module lives outside the proof so that it
keeps running in backend-tests when the proof's job is edited away: deleting
the job, renaming the module or letting backend-tests drop it without the job
taking it fails here. So does switching the job off while it still exists: a
job or step ``if``, a ``continue-on-error``, a ``needs`` that can skip it, an
environment that can change what pytest collects, or a pytest command that
deselects, stops early or swallows the exit status (review of #330).

The job's steps are pinned whole, not by their keys (round 2 of that
review): a step that writes ``$GITHUB_ENV`` (``PYTEST_ADDOPTS=--collect-only``
for the proof step), rewrites the proof module or checks out another ref
changes the pinned list. So does a trigger filter (``paths``,
``paths-ignore``, ``branches``) that would skip the workflow, a
workflow-wide ``defaults`` shell, and a workflow-wide variable that changes
what the shell or pytest runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PROOF = ("tests/unit/test_client_portability_integrity.py", "tests/unit/test_client_portability_proof.py")
# The job's keys and each step's: an allowlist, so a key that could skip the
# job, a step or the failure (``if``, ``continue-on-error``, ``needs``,
# ``env``, ``strategy``) fails here instead of passing unnoticed.
JOB_KEYS = {"name", "runs-on", "timeout-minutes", "permissions", "steps"}
STEP_KEYS = {"name", "uses", "with", "run"}
# The proof's pytest command, token for token: no -k, --deselect, -x, --lf or
# shell operator, so every collected test runs and its status is the step's.
PYTEST = ("pytest", "-rfE", "-p", "no:cacheprovider", "--durations=10", *PROOF)
# The job's steps, whole: what each one runs and how (review of #330, round 2).
STEPS = [
    {"uses": "actions/checkout@v6"},
    {"uses": "actions/setup-python@v6", "with": {"python-version": "${{ env.PYTHON_VERSION }}", "cache": "pip"}},
    {"name": "Install dependencies", "run": "python -m pip install --upgrade pip\npip install -r requirements-dev.txt\n"},
    {"name": "pytest (the portability proof)", "run": " ".join(PYTEST)},
]
# Workflow-wide variables that change what a run step's shell or pytest runs.
SHELL_OR_PYTEST_ENV = ("PYTEST", "PYTHON", "BASH", "ENV", "PATH", "SHELLOPTS", "LD_", "DYLD_")


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _jobs() -> dict[str, Any]:
    return dict(_workflow()["jobs"])


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step.get("run", "")) for step in job["steps"]]


def test_the_proof_job_runs_exactly_the_proof_modules_with_the_dev_set() -> None:
    job = _jobs()["client-portability-proof"]
    runs = _runs(job)

    assert all((ROOT / module).is_file() for module in PROOF)
    assert job["runs-on"] == "ubuntu-latest" and job["permissions"] == {"contents": "read"}
    assert any("pip install -r requirements-dev.txt" in run for run in runs)
    pytest_runs = [run for run in runs if run.lstrip().startswith("pytest")]
    assert len(pytest_runs) == 1
    assert tuple(token for token in pytest_runs[0].split() if token.startswith("tests/")) == PROOF
    assert "-rfE" in pytest_runs[0].split()
    # Offline: no secret, no OIDC token, no environment.
    assert "secrets." not in yaml.safe_dump(job) and "id-token" not in job.get("permissions", {})
    assert "environment" not in job


def test_backend_tests_ignore_the_proof_modules_and_nothing_else() -> None:
    runs = _runs(_jobs()["backend-tests"])
    parallel = [run for run in runs if "--cov=backend" in run]

    assert len(parallel) == 1
    ignored = [token.removeprefix("--ignore=") for token in parallel[0].split() if token.startswith("--ignore")]
    assert tuple(ignored) == PROOF


def test_the_proof_job_cannot_be_switched_off_while_it_stays_in_the_file() -> None:
    """Review of #330: the pin above held with ``if: false`` and ``continue-on-error: true`` planted."""

    workflow = _workflow()
    job = workflow["jobs"]["client-portability-proof"]

    assert set(job) == JOB_KEYS, sorted(set(job) ^ JOB_KEYS)
    assert 0 < int(job["timeout-minutes"]) <= 60
    for step in job["steps"]:
        assert set(step) <= STEP_KEYS, (step.get("name"), sorted(set(step) - STEP_KEYS))
    (pytest_run,) = [run for run in _runs(job) if run.lstrip().startswith("pytest")]
    assert tuple(pytest_run.split()) == PYTEST
    # Nothing workflow-wide reshapes what pytest collects or how it exits.
    assert [name for name in workflow.get("env", {}) if name.startswith(SHELL_OR_PYTEST_ENV) and name != "PYTHON_VERSION"] == []


def test_the_proof_jobs_steps_are_pinned_whole() -> None:
    """Review of #330, round 2: an allowlisted step key can still carry a step that switches the proof off."""

    job = _jobs()["client-portability-proof"]

    assert job["steps"] == STEPS


def test_the_workflow_runs_on_every_pull_request_and_main_push() -> None:
    """No trigger filter or workflow-wide shell can skip or hollow out the proof job (review of #330, round 2)."""

    workflow = _workflow()
    # PyYAML reads the bare key ``on`` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))

    assert set(workflow) <= {"name", "on", True, "concurrency", "env", "permissions", "jobs"}, sorted(map(str, workflow))
    assert triggers["pull_request"] in (None, {}), triggers["pull_request"]
    assert set(triggers["push"]) == {"branches"} and "main" in triggers["push"]["branches"], triggers["push"]
