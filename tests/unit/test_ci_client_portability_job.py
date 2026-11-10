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
    assert [name for name in workflow.get("env", {}) if name.startswith(("PYTEST", "PYTHON")) and name != "PYTHON_VERSION"] == []
