from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tools import release_readiness


def _write_talk_track(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "MLS listed-for-sale feed is pending.",
                "Building Permits Delta Share is pending.",
                "Do not call MLS or permit overlays implemented until those shares are connected.",
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
            "--source-readiness",
            "passed",
            "--mls-listing-status",
            "pending",
            "--building-permit-status",
            "pending",
        ]
    )

    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    markdown = out.with_suffix(".md").read_text(encoding="utf-8")

    assert report["app_url"] == "https://example.invalid"
    assert report["checks"]["package_hygiene"]["status"] == "passed"
    assert report["checks"]["bundle_validate"]["evidence"] == "databricks bundle validate -t dev"
    assert report["checks"]["mls_listing_status"]["status"] == "pending"
    assert "Cannot claim MLS/listing or listed-for-sale triggers are live" in markdown
    assert "| Databricks bundle validate | `passed` | databricks bundle validate -t dev" in markdown


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
        ]
    )
    report = release_readiness.build_report(args)
    markdown = release_readiness.render_markdown(report)

    assert report["checks"]["package_hygiene"]["status"] == "failed"
    assert ".env.local" in report["checks"]["package_hygiene"]["evidence"]
    assert report["checks"]["bundle_validate"]["status"] == "not_run"
    assert "Cannot claim release readiness while failing checks remain: Package hygiene." in markdown
