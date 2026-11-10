"""ci.yml runs the second-client portability proof once per push, in its own job (M3-PR4).

The proof (tests/unit/test_client_portability_proof.py, with its in-process
half tests/unit/test_client_portability_integrity.py) replays three
deploy-dev.yml dispatches offline, so it has a job of its own,
``client-portability-proof``, and backend-tests ignores both modules instead
of running them a second time. This module lives outside the proof so that it
keeps running in backend-tests when the proof's job is edited away: deleting
the job, renaming the module or letting backend-tests drop it without the job
taking it fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
PROOF = ("tests/unit/test_client_portability_integrity.py", "tests/unit/test_client_portability_proof.py")


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


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
