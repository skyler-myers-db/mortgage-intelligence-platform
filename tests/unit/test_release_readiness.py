from __future__ import annotations

import json
import zipfile
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
