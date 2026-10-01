"""The live-validation release gate (audit quality-03 item 3, D-platform-process-b).

tools/live_validation_gate.py is a pure verdict over GitHub run evidence plus a
local ``git diff``; these cases drive it with injected runs, diffs and job
readers, so no test talks to GitHub.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from tools import live_validation_gate as gate

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_PROD = ROOT / ".github" / "workflows" / "deploy-prod.yml"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
RELEASE = "a" * 40
OTHER = "b" * 40
ALL_GREEN = dict.fromkeys(gate.REQUIRED_JOBS, "success")


def run(run_id: int, sha: str, conclusion: str, age_days: float) -> gate.Run:
    return gate.Run(run_id, sha, conclusion, NOW - timedelta(days=age_days), f"https://github.com/o/r/actions/runs/{run_id}")


def same_code(*equivalent: str):
    """A diff reader: listed shas are runtime-equivalent to the release, others touch backend/."""

    def diff(sha: str, release: str) -> list[str] | None:
        return [] if sha in equivalent or sha == release else ["backend/main.py"]

    return diff


def decide(runs: list[gate.Run], diff=None, jobs=None, max_age_days: float = 14.0) -> gate.Verdict:
    return gate.verdict(
        runs,
        RELEASE,
        NOW,
        max_age_days,
        diff=diff or same_code(),
        jobs=jobs or (lambda run_id: dict(ALL_GREEN)),
    )


# ------------------------------------------------------------------ runtime


@pytest.mark.parametrize(
    "path",
    [
        "docs/x.md",
        "docs/audits/report.md",
        "README.md",
        "CLAUDE.md",
        ".github/workflows/ci.yml",
        ".claude/settings.json",
        "design_files/index.html",
        "tests/unit/test_x.py",
        "frontend/tests/e2e/real_data.spec.ts",
        "frontend/src/lib/x.test.ts",
        "frontend/src/routes/home.test.tsx",
        "frontend/src/test/setup.ts",
    ],
)
def test_the_non_runtime_list(path: str) -> None:
    assert gate.is_non_runtime(path)


@pytest.mark.parametrize(
    "path",
    [
        "backend/main.py",
        "genie/sample_questions.md",
        "genie/mortgage_lead_intelligence_space.yml",
        "tools/databricks/provision_genie_space.py",
        "foo/README.md",
        "newdir/anything.txt",
        "frontend/src/mocks/fixtureData.ts",
        "frontend/src/types/wireContract.leads.check.ts",
        "frontend/package-lock.json",
        "databricks.yml",
        "app.yaml",
        "sql/transformations/gold_x.sql",
        "jobs/x.py",
        "lakebase/schema.sql",
        "scripts/deploy.sh",
    ],
)
def test_everything_else_is_runtime(path: str) -> None:
    assert not gate.is_non_runtime(path)


def _fake_git(missing: set[str], changed: list[str]):
    def git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "cat-file":
            sha = args[2].split("^")[0]
            return subprocess.CompletedProcess(args, 1 if sha in missing else 0, "", "")
        return subprocess.CompletedProcess(args, 0, "\n".join(changed) + "\n", "")

    return git


def test_runtime_changed_paths_filters_non_runtime_and_a_missing_sha_is_not_equivalent() -> None:
    git = _fake_git(set(), ["docs/x.md", "backend/main.py", "README.md", "genie/sample_questions.md"])

    assert gate.runtime_changed_paths(OTHER, RELEASE, git=git) == ["backend/main.py", "genie/sample_questions.md"]
    assert gate.runtime_changed_paths(OTHER, RELEASE, git=_fake_git({OTHER}, [])) is None
    assert gate.runtime_changed_paths(OTHER, RELEASE, git=_fake_git(set(), ["docs/x.md"])) == []


# ------------------------------------------------------------------ verdict


def test_no_runs_is_no_green_run() -> None:
    result = decide([])

    assert (result.status, result.reason, result.exit_code) == ("FAIL", "no_green_run", 1)
    assert result.fix == f"dispatch deploy-dev.yml on {RELEASE}, then nightly.yml on the same sha"


def test_exactly_fourteen_days_passes_and_a_moment_later_is_stale() -> None:
    assert decide([run(1, RELEASE, "success", 14.0)]).status == "PASS"

    stale = decide([run(1, RELEASE, "success", 14.01)])
    assert (stale.status, stale.reason) == ("FAIL", "stale")


def test_a_docs_only_diff_passes() -> None:
    def diff(sha: str, release: str) -> list[str] | None:
        return [p for p in ["docs/x.md", "README.md"] if not gate.is_non_runtime(p)]

    assert decide([run(1, OTHER, "success", 1)], diff=diff).status == "PASS"


def test_a_backend_diff_is_runtime_changed_and_names_the_newest_green() -> None:
    result = decide([run(1, OTHER, "success", 1), run(2, OTHER, "success", 3)])

    assert (result.status, result.reason) == ("FAIL", "runtime_changed")
    assert result.run_url == "https://github.com/o/r/actions/runs/1"
    assert result.paths == ["backend/main.py"]


def test_a_sample_questions_only_diff_is_runtime_changed() -> None:
    def diff(sha: str, release: str) -> list[str] | None:
        return [p for p in ["genie/sample_questions.md"] if not gate.is_non_runtime(p)]

    assert decide([run(1, OTHER, "success", 1)], diff=diff).reason == "runtime_changed"


def test_a_missing_run_sha_is_reported_not_passed() -> None:
    result = decide([run(1, OTHER, "success", 1)], diff=lambda sha, release: None)

    assert result.reason == "runtime_changed"
    assert result.paths == [f"{OTHER} is missing from local history"]


def test_green_then_red_on_one_sha_is_a_later_failure() -> None:
    result = decide([run(1, RELEASE, "success", 3), run(2, RELEASE, "failure", 1)])

    assert (result.status, result.reason) == ("FAIL", "later_failure")
    assert result.run_url.endswith("/2")


def test_an_older_green_on_the_release_code_certifies_behind_a_newer_green_elsewhere() -> None:
    result = decide([run(1, OTHER, "success", 1), run(2, RELEASE, "success", 2)], diff=same_code(RELEASE))

    assert result.status == "PASS"
    assert result.run_sha == RELEASE and result.run_url.endswith("/2")


def test_a_cancelled_newest_run_is_ignored() -> None:
    payload = {
        "workflow_runs": [
            _raw(3, RELEASE, "cancelled", 0.5),
            _raw(2, RELEASE, "success", 2),
        ]
    }
    runs = gate.parse_runs(payload)

    assert [r.run_id for r in runs] == [2]
    assert decide(runs).status == "PASS"


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled", "failure", None])
def test_a_required_job_that_did_not_succeed_fails(conclusion: str | None) -> None:
    jobs = {**ALL_GREEN, gate.PLAYWRIGHT_JOB: conclusion}
    result = decide([run(1, RELEASE, "success", 1)], jobs=lambda run_id: {k: v for k, v in jobs.items() if v})

    assert (result.status, result.reason) == ("FAIL", "job_not_success")
    assert result.jobs[gate.PLAYWRIGHT_JOB] == (conclusion or "absent")


def test_a_pass_carries_the_url_sha_age_and_job_conclusions() -> None:
    result = decide([run(7, RELEASE, "success", 2.5)])

    assert result.status == "PASS" and result.exit_code == 0
    assert (result.run_url, result.run_sha, result.age_days) == ("https://github.com/o/r/actions/runs/7", RELEASE, 2.5)
    assert result.jobs == ALL_GREEN


# ------------------------------------------------------------------ GitHub


def _raw(run_id: int, sha: str, conclusion: str | None, age_days: float, **extra: str) -> dict[str, object]:
    return {
        "id": run_id,
        "head_sha": sha,
        "conclusion": conclusion,
        "updated_at": (NOW - timedelta(days=age_days)).isoformat().replace("+00:00", "Z"),
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "head_branch": extra.get("head_branch", "main"),
        "event": extra.get("event", "workflow_dispatch"),
    }


def test_runs_keep_main_dispatch_and_green_or_red_only() -> None:
    payload = {
        "workflow_runs": [
            _raw(1, RELEASE, "success", 1),
            _raw(2, RELEASE, "success", 1, head_branch="feature/x"),
            _raw(3, RELEASE, "success", 1, event="push"),
            _raw(4, RELEASE, "timed_out", 1),
            _raw(5, RELEASE, "startup_failure", 1),
            _raw(6, RELEASE, "neutral", 1),
        ]
    }

    assert [(r.run_id, r.green) for r in gate.parse_runs(payload)] == [(1, True), (4, False), (5, False)]


def test_listing_paginates_at_most_five_pages_and_stops_at_120_days() -> None:
    calls: list[str] = []

    def full(path: str) -> object:
        calls.append(path)
        return {"workflow_runs": [_raw(len(calls) * 1000 + i, RELEASE, "success", 1) for i in range(gate.PER_PAGE)]}

    assert len(gate.list_runs(full, NOW)) == 5 * gate.PER_PAGE
    assert len(calls) == 5
    assert calls[0].startswith("actions/workflows/nightly.yml/runs?branch=main&event=workflow_dispatch&status=completed")

    calls.clear()

    def aging(path: str) -> object:
        calls.append(path)
        return {"workflow_runs": [_raw(i, RELEASE, "success", 100 + 10 * i) for i in range(gate.PER_PAGE)]}

    runs = gate.list_runs(aging, NOW)
    assert len(calls) == 1
    assert all(NOW - r.updated_at <= timedelta(days=gate.LOOKBACK_DAYS) for r in runs)


@pytest.mark.parametrize(
    "fetch_result",
    [gate.GateApiError("401"), {"message": "Bad credentials"}, {"workflow_runs": [{"id": "x"}]}],
)
def test_an_api_error_is_api_error_with_exit_2(fetch_result: object) -> None:
    def fetch(path: str) -> object:
        if isinstance(fetch_result, Exception):
            raise fetch_result
        return fetch_result

    result = gate.evaluate(RELEASE, fetch=fetch, now=NOW, diff=same_code())

    assert (result.status, result.reason, result.exit_code) == ("API_ERROR", "api_error", 2)


def test_the_cli_exits_2_on_an_api_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def broken(path: str) -> object:
        raise gate.GateApiError("unreachable")

    monkeypatch.setattr(gate, "github_fetch", lambda env=None: broken)
    monkeypatch.setattr(gate, "resolve_sha", lambda ref, git=None: RELEASE)

    assert gate.main(["--release-sha", "HEAD"]) == 2
    assert "API_ERROR" in capsys.readouterr().out


def test_the_transport_is_https_only_and_falls_back_to_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gate.github_fetch({"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}) is not gate._gh_fetch
    assert gate.github_fetch({"GH_TOKEN": "t"}) is gate._gh_fetch
    assert gate.github_fetch({}) is gate._gh_fetch

    monkeypatch.setattr(gate, "API_ROOT", "http://api.github.com")
    with pytest.raises(gate.GateApiError, match="https"):
        gate.github_fetch({"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"})("actions/runs/1/jobs")


def test_rendered_output_names_the_fix_and_carries_no_free_text() -> None:
    text = gate.render(decide([run(1, OTHER, "success", 1)]))

    assert "FAIL (runtime_changed)" in text
    assert "https://github.com/o/r/actions/runs/1" in text
    assert "runtime path changed: backend/main.py" in text
    assert f"fix: dispatch deploy-dev.yml on {RELEASE}, then nightly.yml on the same sha" in text


# ---------------------------------------------------------- enforcement wiring


def test_prod_readiness_gate_runs_the_gate_without_deploying() -> None:
    text = DEPLOY_PROD.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)

    assert workflow["name"] == "prod-readiness-gate"
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["non-deploying-prod-gate"]["name"] == "Non-deploying production scaffold gate"
    job = workflow["jobs"]["live-validation-age"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    checkout, python, gate_step = job["steps"]
    assert checkout["uses"] == "actions/checkout@v6" and checkout["with"]["fetch-depth"] == 0
    assert python["uses"] == "actions/setup-python@v6" and str(python["with"]["python-version"]) == "3.11"
    assert gate_step["run"].strip() == (
        'python -m tools.live_validation_gate --release-sha "$GITHUB_SHA" --max-age-days 14'
    )
    assert gate_step["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    assert "DATABRICKS_" not in text and "scripts/deploy.sh" not in text and "secrets." not in text


def test_the_customer_deploy_and_package_paths_never_call_github() -> None:
    for rel in ("scripts/deploy.sh", "scripts/package_source.sh"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "live_validation_gate" not in text and "api.github.com" not in text, rel
    for path in (ROOT / "scripts" / "lib").glob("*.sh"):
        assert "live_validation_gate" not in path.read_text(encoding="utf-8"), path.name
