"""The fixture harness runs sharded, merges into one report and gates as one check.

PR #270 measured the unsharded ``e2e (fixture harness, every route)`` job at
26-48 min and PR #271 raised its cap to 70 "until it is sharded". Now:

- ``e2e-fixture-shard`` runs the Chromium harness as a fail-fast-off matrix,
  ``--shard=i/N`` with a blob reporter, and uploads each shard's blob (pass
  or fail) and, on failure, its raw test results;
- ``e2e-fixture-perf`` runs the built-dist checks and the single-worker
  perf / interaction budgets on its own runner, never split;
- ``e2e-fixture`` keeps the original check name: it merges the blobs into the
  ``e2e-fixture-playwright-report`` artifact (same name, same paths) and fails
  unless every shard passed and reported and the perf job passed.

The gate step's shell is EXECUTED here, not only read: ``playwright
merge-reports`` exits 0 on a merged report that holds failures (it fails only
on a reporter error; Playwright 1.63 and 1.64 alike), so that step is the only
thing between a red shard and a green check. The perf-step and built-dist
pins live in tests/unit/test_ci_frontend_gates.py.
"""
from __future__ import annotations

import fnmatch
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools import release_hygiene

ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SHARD_JOB, PERF_JOB, GATE_JOB = "e2e-fixture-shard", "e2e-fixture-perf", "e2e-fixture"
SHARD_RUN = (
    "npm --prefix frontend run e2e:fixture:ci -- "
    "--shard=${{ matrix.shard }}/${{ strategy.job-total }} --reporter=list,github,blob"
)
MERGE_RUN = "npx playwright merge-reports --reporter=dot,html blob-report"
REPORT_ARTIFACT = "e2e-fixture-playwright-report"


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _step(steps: list[dict[str, Any]], name: str) -> tuple[int, dict[str, Any]]:
    for index, step in enumerate(steps):
        if step.get("name") == name:
            return index, step
    raise AssertionError(f"no step named {name!r}")


def _shards() -> list[int]:
    shards: list[int] = _jobs()[SHARD_JOB]["strategy"]["matrix"]["shard"]
    return shards


def test_the_shards_cover_the_suite_once_each() -> None:
    job = _jobs()[SHARD_JOB]
    shards = _shards()
    assert len(shards) >= 2 and shards == list(range(1, len(shards) + 1)), "1..N, each index once"
    assert job["strategy"]["fail-fast"] is False, "every shard finishes, so the report holds every failure"
    assert set(job["strategy"]["matrix"]) == {"shard"}
    assert job["name"] == "e2e (fixture harness, shard ${{ matrix.shard }}/${{ strategy.job-total }})"
    # Measured: slowest shard ~10.3 min + slowest setup ~8.6 min (ci.yml comment).
    assert job["timeout-minutes"] == 25

    _, run = _step(job["steps"], "Run the fixture harness (one shard)")
    assert run["run"] == SHARD_RUN, "N comes from the matrix itself, never a second literal"
    assert run["env"] == {"E2E_FIXTURE_WORKERS": "4"}
    dump = yaml.safe_dump(job)
    for opt_in in ("MIP_PERF", "MIP_VRT", "MIP_CROSS_ENGINE"):
        assert opt_in not in dump, f"a shard runs the normal fixture-chromium suite only ({opt_in})"
    assert all("continue-on-error" not in step for step in job["steps"])


def test_each_shard_uploads_its_blob_pass_or_fail() -> None:
    steps = _jobs()[SHARD_JOB]["steps"]
    run, _ = _step(steps, "Run the fixture harness (one shard)")
    blob_index, blob = _step(steps, "Upload the shard's blob report")
    results_index, results = _step(steps, "Upload the shard's test results")

    assert run < blob_index < results_index
    assert blob["if"] == "${{ !cancelled() }}", "a failing shard's blob is the report the gate merges"
    assert str(blob["uses"]).startswith("actions/upload-artifact@")
    assert blob["with"] == {
        "name": "e2e-fixture-blob-${{ matrix.shard }}",
        "path": "frontend/blob-report",
        "if-no-files-found": "error",
        "retention-days": 7,
        "overwrite": True,
    }
    assert results["if"] == "failure()"
    assert results["with"]["name"] == "e2e-fixture-results-${{ matrix.shard }}"
    assert results["with"]["path"] == "frontend/test-results"
    assert results["with"]["overwrite"] is True


def test_the_gate_keeps_the_check_name_and_runs_after_a_failed_shard() -> None:
    job = _jobs()[GATE_JOB]
    assert job["name"] == "e2e (fixture harness, every route)"
    assert job["needs"] == [SHARD_JOB, PERF_JOB]
    # The default success() would SKIP the gate when a shard fails, and a
    # skipped job reads as passing.
    assert job["if"] == "${{ !cancelled() }}"
    assert job["env"]["E2E_FIXTURE_SHARD_TOTAL"] == str(len(_shards()))
    assert job["timeout-minutes"] == 10
    assert "strategy" not in job
    assert all("continue-on-error" not in step for step in job["steps"])


@pytest.mark.parametrize("name", [SHARD_JOB, PERF_JOB, GATE_JOB])
def test_no_fixture_job_tolerates_its_own_failure(name: str) -> None:
    # A job-level `continue-on-error: true` makes `needs.<job>.result` read
    # `success` for a red job, so the gate below would go green over a red
    # shard or a red perf run. The step-level pins above cannot see it.
    assert "continue-on-error" not in _jobs()[name], f"{name}: no job-level continue-on-error"


def test_the_gate_merges_every_blob_into_the_report_artifact() -> None:
    steps = _jobs()[GATE_JOB]["steps"]
    blobs_index, blobs = _step(steps, "Download the shard blob reports")
    results_index, results = _step(steps, "Download the shard test results")
    merge_index, merge = _step(steps, "Merge the shard reports into one HTML report")
    gate_index, _ = _step(steps, "Every shard passed and reported")
    upload_index, upload = _step(steps, "Upload Playwright report")
    assert blobs_index < results_index < merge_index < gate_index < upload_index

    shards = _shards()
    blob_names = [f"e2e-fixture-blob-{shard}" for shard in shards]
    result_names = [f"e2e-fixture-results-{shard}" for shard in shards]
    assert str(blobs["uses"]).startswith("actions/download-artifact@")
    assert blobs["with"] == {"pattern": "e2e-fixture-blob-*", "path": "frontend/blob-report", "merge-multiple": True}
    assert all(fnmatch.fnmatchcase(name, blobs["with"]["pattern"]) for name in blob_names)
    assert not any(fnmatch.fnmatchcase(name, blobs["with"]["pattern"]) for name in result_names)
    assert results["with"] == {"pattern": "e2e-fixture-results-*", "path": "frontend/test-results", "merge-multiple": True}
    assert all(fnmatch.fnmatchcase(name, results["with"]["pattern"]) for name in result_names)

    assert merge["run"] == MERGE_RUN and merge["working-directory"] == "frontend"
    # The artifact the unsharded job uploaded: same name, same two folders.
    assert upload["if"] == "failure()"
    assert upload["with"] == {
        "name": REPORT_ARTIFACT,
        "path": "frontend/playwright-report\nfrontend/test-results\n",
        "if-no-files-found": "ignore",
        "retention-days": 7,
    }


def _run_gate(tmp_path: Path, blobs: int, shard: str, perf: str, total: int | None = None) -> subprocess.CompletedProcess[str]:
    steps = _jobs()[GATE_JOB]["steps"]
    _, gate = _step(steps, "Every shard passed and reported")
    assert gate["working-directory"] == "frontend"
    assert gate["env"] == {
        "SHARD_RESULT": "${{ needs.e2e-fixture-shard.result }}",
        "PERF_RESULT": "${{ needs.e2e-fixture-perf.result }}",
    }
    (tmp_path / "blob-report").mkdir()
    for index in range(1, blobs + 1):
        (tmp_path / "blob-report" / f"report-{index}.zip").write_bytes(b"PK")
    env = {
        "PATH": "/usr/bin:/bin",
        "E2E_FIXTURE_SHARD_TOTAL": str(len(_shards()) if total is None else total),
        "SHARD_RESULT": shard,
        "PERF_RESULT": perf,
    }
    # GitHub runs an unspecified `shell` as `bash -e {0}`.
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-c", gate["run"]],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False,
    )


def test_the_gate_step_passes_when_every_shard_passed_and_reported(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, blobs=len(_shards()), shard="success", perf="success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error::" not in result.stdout


@pytest.mark.parametrize(
    ("blobs_delta", "shard", "perf", "message"),
    [
        (-1, "success", "success", "shard blob reports, found"),  # a shard lost its report
        (-99, "success", "success", "shard blob reports, found 0"),  # no shard reported
        (1, "success", "success", "shard blob reports, found"),  # a stale extra blob
        (0, "failure", "success", "fixture harness shards: failure"),
        (0, "cancelled", "success", "fixture harness shards: cancelled"),  # a timed-out shard
        (0, "skipped", "success", "fixture harness shards: skipped"),
        (0, "success", "failure", "fixture perf + interaction budgets: failure"),
        (0, "success", "skipped", "fixture perf + interaction budgets: skipped"),
    ],
)
def test_the_gate_step_fails_unless_every_shard_passed_and_reported(
    tmp_path: Path, blobs_delta: int, shard: str, perf: str, message: str
) -> None:
    blobs = max(len(_shards()) + blobs_delta, 0)
    result = _run_gate(tmp_path, blobs=blobs, shard=shard, perf=perf)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error::" in result.stdout and message in result.stdout, result.stdout


def test_the_gate_reports_every_reason_at_once(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, blobs=0, shard="failure", perf="failure")
    assert result.returncode == 1
    assert result.stdout.count("::error::") == 3, result.stdout


def test_only_the_perf_job_runs_the_timing_budgets() -> None:
    jobs = _jobs()
    perf = jobs[PERF_JOB]
    assert perf["name"] == "e2e (fixture perf + interaction budgets, single worker)"
    assert "strategy" not in perf, "the timing budgets are never split"
    assert perf["timeout-minutes"] == 15
    for name, job in jobs.items():
        if name != PERF_JOB:
            assert "MIP_PERF" not in yaml.safe_dump(job), f"{name} must not set MIP_PERF"
            assert "MIP_REQUIRE_FRONTEND_DIST" not in yaml.safe_dump(job), f"{name}: the built-dist checks run once"


def test_blob_reports_stay_out_of_git_and_the_app_source() -> None:
    # A local `--reporter=blob` run writes frontend/blob-report/ (failure
    # screenshots and traces); the bundle sync and git archive both honour
    # .gitignore, like playwright-report/ and test-results/.
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "blob-report/" in ignored


def test_the_release_source_zip_refuses_a_blob_report(tmp_path: Path) -> None:
    # Belt and braces, like playwright-report/ and test-results/: the source
    # package starts from `git archive`, but its hygiene check and cleanup
    # list name the blob folder too.
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("mip/frontend/blob-report/report-1.zip", b"PK")
        zipped.writestr("mip/frontend/src/main.tsx", b"")
    findings = release_hygiene.inspect_zip(str(archive))
    assert [(finding.path, finding.reason) for finding in findings] == [
        ("mip/frontend/blob-report/report-1.zip", "banned directory or path segment: blob-report")
    ]
    package = (ROOT / "scripts" / "package_source.sh").read_text(encoding="utf-8")
    assert '"$STAGE/frontend/blob-report"' in package


def test_docs_name_the_sharded_jobs_and_the_report_artifact() -> None:
    testing = (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
    for needle in (f"`{SHARD_JOB}`", f"`{PERF_JOB}`", f"`{GATE_JOB}`", f"`{REPORT_ARTIFACT}`", "--shard="):
        assert needle in testing, needle
