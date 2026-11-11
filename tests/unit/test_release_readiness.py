from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools import release_readiness
from tools.live_validation_gate import Verdict

LIVE_CHECKS = tuple(release_readiness.LIVE_CHECK_JOBS)
PARITY, PLAYWRIGHT, DRILL = (
    "SQL<->Python + Lakebase + Genie live",
    "Playwright (real-UC golden path)",
    "Credential-kill drill (simulated targets)",
)
RUN_URL = "https://github.com/o/r/actions/runs/7"
SHA = "a" * 40


def _pass_verdict() -> Verdict:
    return Verdict(
        "PASS",
        None,
        SHA,
        run_url=RUN_URL,
        run_sha=SHA,
        age_days=2.0,
        jobs={PARITY: "success", PLAYWRIGHT: "success", DRILL: "success"},
    )


def _write_talk_track(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "MLS listed-for-sale feed is live and connected.",
                "Building Permits Delta Share is pending.",
                "Do not claim building permits or permit filings are implemented until that share is connected.",
            ]
        ),
        encoding="utf-8",
    )


def test_writes_json_and_markdown_with_supplied_evidence(tmp_path: Path) -> None:
    release_zip = tmp_path / "release.zip"
    with zipfile.ZipFile(release_zip, "w") as archive:
        archive.writestr("README.md", "# ok\n")

    talk_track = tmp_path / "talk-track.md"
    _write_talk_track(talk_track)
    out = tmp_path / "dist" / "release-readiness.json"

    rc = release_readiness.main(
        [
            "--release-sha",
            SHA,
            "--out",
            str(out),
            "--app-url",
            "https://example.invalid",
            "--timestamp",
            "2026-05-21T12:00:00+00:00",
            "--release-zip",
            str(release_zip),
            "--talk-track",
            str(talk_track),
            "--bundle-validate",
            "passed",
            "--bundle-validate-evidence",
            "databricks bundle validate -t dev",
            "--sql-python-parity",
            "passed",
            "--lakebase-round-trip",
            "passed",
            "--genie-eval",
            "passed",
            "--genie-live",
            "passed",
            "--playwright-live",
            "passed",
            "--resilience-drill",
            "passed",
            "--non-admin-auth",
            "passed",
            "--source-readiness",
            "passed",
            "--mls-listing-status",
            "available",
            "--building-permit-status",
            "pending",
        ],
        live_verdict=_pass_verdict(),
    )

    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    markdown = out.with_suffix(".md").read_text(encoding="utf-8")

    assert report["app_url"] == "https://example.invalid"
    assert report["checks"]["package_hygiene"]["status"] == "passed"
    assert report["checks"]["bundle_validate"]["evidence"] == "databricks bundle validate -t dev"
    assert report["checks"]["mls_listing_status"]["status"] == "available"
    assert "Cannot claim MLS/listing or listed-for-sale triggers are live" not in markdown
    assert "Cannot claim building-permit or renovation-trigger segments are live" in markdown
    assert "| Databricks bundle validate | `passed` | databricks bundle validate -t dev" in markdown
    for name in LIVE_CHECKS:
        assert report["checks"][name]["status"] == "passed"
        assert RUN_URL in report["checks"][name]["evidence"]
    assert report["live_evidence"]["verdict"]["run_url"] == RUN_URL


def test_missing_live_evidence_stays_not_run_or_unknown(tmp_path: Path) -> None:
    talk_track = tmp_path / "talk-track.md"
    _write_talk_track(talk_track)
    missing_zip = tmp_path / "missing.zip"

    args = release_readiness.parse_args(
        [
            "--out",
            str(tmp_path / "release-readiness.json"),
            "--timestamp",
            "2026-05-21T12:00:00+00:00",
            "--release-zip",
            str(missing_zip),
            "--talk-track",
            str(talk_track),
            "--live-evidence",
            "none",
        ]
    )
    report = release_readiness.build_report(args)

    assert report["checks"]["package_hygiene"]["status"] == "unknown"
    assert report["checks"]["bundle_validate"]["status"] == "not_run"
    assert report["checks"]["sql_python_parity"]["status"] == "not_run"
    assert report["checks"]["lakebase_round_trip"]["status"] == "not_run"
    assert report["checks"]["genie_eval"]["status"] == "not_run"
    assert report["checks"]["genie_live"]["status"] == "not_run"
    assert report["checks"]["playwright_live"]["status"] == "not_run"
    assert report["checks"]["resilience_drill"]["status"] == "not_run"
    assert report["checks"]["non_admin_auth"]["status"] == "not_run"
    assert report["checks"]["source_readiness"]["status"] == "not_run"
    assert report["checks"]["mls_listing_status"]["status"] == "unknown"
    assert any("Cannot claim full Module 0 release readiness" in item for item in report["cannot_claim"])
    assert any("Genie eval" in item for item in report["cannot_claim"])


def test_bad_zip_fails_package_hygiene_without_hiding_other_unknowns(tmp_path: Path) -> None:
    release_zip = tmp_path / "release.zip"
    with zipfile.ZipFile(release_zip, "w") as archive:
        archive.writestr(".env.local", "SECRET=x\n")
        archive.writestr("backend/__pycache__/bad.pyc", "compiled")

    talk_track = tmp_path / "talk-track.md"
    _write_talk_track(talk_track)
    args = release_readiness.parse_args(
        [
            "--release-zip",
            str(release_zip),
            "--talk-track",
            str(talk_track),
            "--live-evidence",
            "none",
        ]
    )
    report = release_readiness.build_report(args)
    markdown = release_readiness.render_markdown(report)

    assert report["checks"]["package_hygiene"]["status"] == "failed"
    assert ".env.local" in report["checks"]["package_hygiene"]["evidence"]
    assert report["checks"]["bundle_validate"]["status"] == "not_run"
    assert "Cannot claim release readiness while failing checks remain: Package hygiene." in markdown


def test_root_mlflow_database_fails_package_hygiene(tmp_path: Path) -> None:
    release_zip = tmp_path / "release.zip"
    with zipfile.ZipFile(release_zip, "w") as archive:
        archive.writestr("mortgage-intelligence-platform/mlflow.db", b"SQLite format 3\x00")

    talk_track = tmp_path / "talk-track.md"
    _write_talk_track(talk_track)
    args = release_readiness.parse_args(
        [
            "--release-zip",
            str(release_zip),
            "--talk-track",
            str(talk_track),
            "--live-evidence",
            "none",
        ]
    )

    report = release_readiness.build_report(args)

    assert report["checks"]["package_hygiene"]["status"] == "failed"
    assert "mlflow.db (banned file: mlflow.db)" in report["checks"]["package_hygiene"]["evidence"]


def test_mlflow_run_directory_fails_package_hygiene(tmp_path: Path) -> None:
    release_zip = tmp_path / "release.zip"
    with zipfile.ZipFile(release_zip, "w") as archive:
        archive.writestr("mortgage-intelligence-platform/mlruns/0/meta.yaml", "artifact")

    talk_track = tmp_path / "talk-track.md"
    _write_talk_track(talk_track)
    args = release_readiness.parse_args(
        [
            "--release-zip",
            str(release_zip),
            "--talk-track",
            str(talk_track),
            "--live-evidence",
            "none",
        ]
    )

    report = release_readiness.build_report(args)

    assert report["checks"]["package_hygiene"]["status"] == "failed"
    assert "mlruns/0/meta.yaml" in report["checks"]["package_hygiene"]["evidence"]
    assert (
        "banned directory or path segment: mlruns"
        in report["checks"]["package_hygiene"]["evidence"]
    )


def _live_report(tmp_path: Path, verdict: Verdict | None, *extra: str) -> dict:
    talk_track = tmp_path / "talk-track.md"
    _write_talk_track(talk_track)
    args = release_readiness.parse_args(
        ["--release-zip", str(tmp_path / "missing.zip"), "--talk-track", str(talk_track), *extra]
    )
    return release_readiness.build_report(args, verdict)


def test_the_live_checks_default_to_github_evidence() -> None:
    args = release_readiness.parse_args([])

    assert (args.live_evidence, args.release_sha) == ("github", "HEAD")


def test_github_pass_marks_the_six_live_checks_passed_with_run_evidence(tmp_path: Path) -> None:
    report = _live_report(tmp_path, _pass_verdict())

    for name in LIVE_CHECKS:
        check = report["checks"][name]
        assert check["status"] == "passed", name
        assert RUN_URL in check["evidence"] and SHA in check["evidence"] and "2.0 days old" in check["evidence"]
    assert report["checks"]["genie_eval"]["status"] == "not_run"


def test_github_later_failure_fails_all_six(tmp_path: Path) -> None:
    verdict = Verdict("FAIL", "later_failure", SHA, run_url=RUN_URL, run_sha=SHA, age_days=1.0)
    report = _live_report(tmp_path, verdict, "--playwright-live", "passed")

    assert {report["checks"][name]["status"] for name in LIVE_CHECKS} == {"failed"}
    assert any("Playwright live" in item for item in report["cannot_claim"])


def test_github_job_not_success_fails_only_that_jobs_checks(tmp_path: Path) -> None:
    verdict = Verdict(
        "FAIL",
        "job_not_success",
        SHA,
        run_url=RUN_URL,
        run_sha=SHA,
        age_days=1.0,
        jobs={PARITY: "success", PLAYWRIGHT: "skipped", DRILL: "success"},
    )
    report = _live_report(tmp_path, verdict)
    statuses = {name: report["checks"][name]["status"] for name in LIVE_CHECKS}

    assert statuses == {
        "sql_python_parity": "unknown",
        "lakebase_round_trip": "unknown",
        "genie_live": "unknown",
        "playwright_live": "failed",
        "non_admin_auth": "failed",
        "resilience_drill": "unknown",
    }
    assert "skipped" in report["checks"]["playwright_live"]["evidence"]


@pytest.mark.parametrize("reason", ["no_green_run", "stale", "runtime_changed"])
def test_github_fail_without_a_certifying_run_leaves_the_six_unknown(tmp_path: Path, reason: str) -> None:
    report = _live_report(tmp_path, Verdict("FAIL", reason, SHA), "--sql-python-parity", "passed")

    for name in LIVE_CHECKS:
        assert report["checks"][name]["status"] == "unknown"
        assert reason in report["checks"][name]["evidence"]
    assert "operator assertion without a verified live run" in report["checks"]["sql_python_parity"]["evidence"]
    assert any("SQL/Python parity" in item for item in report["cannot_claim"])


def test_github_api_error_leaves_the_six_unknown(tmp_path: Path) -> None:
    report = _live_report(tmp_path, Verdict("API_ERROR", "api_error", SHA))

    assert {report["checks"][name]["status"] for name in LIVE_CHECKS} == {"unknown"}
    assert "API_ERROR" in report["checks"]["genie_live"]["evidence"]


def test_none_mode_never_trusts_an_operator_passed(tmp_path: Path) -> None:
    flags = [arg for name in LIVE_CHECKS for arg in (f"--{name.replace('_', '-')}", "passed")]
    report = _live_report(tmp_path, None, "--live-evidence", "none", *flags)

    for name in LIVE_CHECKS:
        assert report["checks"][name] == {
            "status": "unknown",
            "evidence": "operator assertion without a verified live run",
            "claim": report["checks"][name]["claim"],
        }
    assert any("Playwright live" in item for item in report["cannot_claim"])


def test_an_operator_failed_stays_failed_in_both_modes(tmp_path: Path) -> None:
    for mode, verdict in (("none", None), ("github", _pass_verdict())):
        report = _live_report(tmp_path, verdict, "--live-evidence", mode, "--genie-live", "failed")
        assert report["checks"]["genie_live"]["status"] == "failed", mode


def _gate_runs(monkeypatch: pytest.MonkeyPatch) -> str:
    """The real tools/live_validation_gate.py, loaded the way release_readiness loads it, over a fake GitHub.

    Two green titled runs on this checkout's own commit: client-one's, then a
    newer one of client-two. Only the GitHub fetch is fake: the run selection,
    the client filter and the verdict are the gate's.
    """

    loader = release_readiness._load_tool
    sha = loader("live_validation_gate").resolve_sha("HEAD")
    now = datetime.now(UTC)
    runs = [
        {
            "id": run_id,
            "head_sha": sha,
            "conclusion": "success",
            "updated_at": (now - timedelta(days=age)).isoformat().replace("+00:00", "Z"),
            "html_url": f"https://example.invalid/runs/{run_id}",
            "head_branch": "main",
            "event": "workflow_dispatch",
            "display_title": f"nightly client={client} sha={sha}",
        }
        for run_id, client, age in ((1, "client-one", 2.0), (2, "client-two", 1.0))
    ]

    def fetch(path: str) -> object:
        if path.startswith("actions/runs/"):
            return {"jobs": [{"name": name, "conclusion": "success"} for name in (PARITY, PLAYWRIGHT, DRILL)]}
        return {"workflow_runs": runs}

    def load(name: str) -> object:
        module = loader(name)
        if name == "live_validation_gate":
            monkeypatch.setattr(module, "github_fetch", lambda env=None: fetch)
        return module

    monkeypatch.setattr(release_readiness, "_load_tool", load)
    return sha


def _github_report(tmp_path: Path, sha: str, *extra: str) -> tuple[dict, str]:
    release_zip = tmp_path / "release.zip"
    with zipfile.ZipFile(release_zip, "w") as archive:
        archive.writestr("README.md", "# ok\n")
    talk_track = tmp_path / "talk-track.md"
    _write_talk_track(talk_track)
    out = tmp_path / "out.json"
    common = ["--release-sha", sha, "--release-zip", str(release_zip), "--talk-track", str(talk_track), "--timestamp", "2026-05-21T12:00:00+00:00"]
    assert release_readiness.main([*common, "--out", str(out), *extra]) == 0
    return json.loads(out.read_text(encoding="utf-8")), out.with_suffix(".md").read_text(encoding="utf-8")


def test_with_client_the_gate_certifies_only_that_clients_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--client reaches gate.evaluate: client-two's newer green run never certifies client-one's release."""

    sha = _gate_runs(monkeypatch)
    report, markdown = _github_report(tmp_path, sha, "--client", "client-one")

    assert report["live_evidence"]["verdict"]["client"] == "client-one"
    assert report["live_evidence"]["client"] == "client-one"
    for name in LIVE_CHECKS:
        evidence = report["checks"][name]["evidence"]
        assert report["checks"][name]["status"] == "passed", name
        assert "client: client-one" in evidence and "https://example.invalid/runs/1 " in evidence, evidence
    assert "- Live evidence: `github`, client: client-one" in markdown


def test_without_client_every_live_check_says_the_evidence_may_be_any_clients(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sha = _gate_runs(monkeypatch)
    report, markdown = _github_report(tmp_path, sha)

    assert report["live_evidence"]["verdict"]["client"] is None
    for name in LIVE_CHECKS:
        evidence = report["checks"][name]["evidence"]
        assert f"client: {release_readiness.ANY_CLIENT}" in evidence and "https://example.invalid/runs/2 " in evidence, evidence
        assert f"client: {release_readiness.ANY_CLIENT}" in markdown


def test_the_notes_are_the_gates() -> None:
    from tools import live_validation_gate as gate

    assert release_readiness.ANY_CLIENT == gate.ANY_CLIENT
    assert release_readiness.PLAYWRIGHT_JOB == gate.PLAYWRIGHT_JOB
    for client in ("client-one", None):
        assert release_readiness.client_note(client) == gate.client_note(client)
    for conclusion in ("skipped", "success", "failure", "absent", None):
        assert release_readiness.app_api_mutation_note(conclusion) == gate.app_api_mutation_note(conclusion)


def test_a_prod_apps_skipped_app_api_suites_show_in_the_playwright_evidence(tmp_path: Path) -> None:
    verdict = _pass_verdict()
    verdict.client, verdict.app_api_mutation = "client-one", "skipped"
    report = _live_report(tmp_path, verdict)

    for name in LIVE_CHECKS:
        evidence = report["checks"][name]["evidence"]
        assert report["checks"][name]["status"] == "passed"
        skipped = "App API mutation suites: skipped (a prod App" in evidence
        assert skipped == (release_readiness.LIVE_CHECK_JOBS[name] == PLAYWRIGHT), (name, evidence)


def test_the_customer_release_recipe_names_the_client() -> None:
    """A customer's release-readiness.json is certified by that customer's run, so the recipe passes --client."""

    doc = (Path(__file__).resolve().parents[2] / "docs" / "deployment.md").read_text(encoding="utf-8")
    recipe = " ".join(doc.split("A customer release is the", 1)[1].split("Its six live checks", 1)[0].split())

    assert "`python tools/release_readiness.py --release-sha <sha> --client <client>`" in recipe, recipe
