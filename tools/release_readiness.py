#!/usr/bin/env python3
"""Generate an honest Module 0 release-readiness evidence artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path("dist/release-readiness.json")
DEFAULT_RELEASE_ZIP = Path("dist/mortgage-intelligence-platform.zip")
TALK_TRACK = Path("docs/module0-talk-track.md")

VALID_CHECK_STATUSES = ("passed", "failed", "unknown", "not_run")
VALID_FEED_STATUSES = ("available", "pending", "unknown")

CHECK_LABELS = {
    "package_hygiene": "Package hygiene",
    "bundle_validate": "Databricks bundle validate",
    "sql_python_parity": "SQL/Python parity",
    "lakebase_round_trip": "Lakebase round trip",
    "genie_eval": "Genie eval",
    "genie_live": "Genie live",
    "playwright_live": "Playwright live",
    "resilience_drill": "Resilience/degraded drill",
    "non_admin_auth": "Authenticated non-admin proof",
    "source_readiness": "Source readiness",
    "mls_listing_status": "MLS/listing feed",
    "building_permit_status": "Building permit feed",
    "talk_track_pending_claims": "Talk-track pending-claim guard",
}


@dataclass(frozen=True)
class Evidence:
    status: str
    evidence: str
    claim: str

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "evidence": self.evidence,
            "claim": self.claim,
        }


def _repo_path(path: str | Path) -> Path:
    raw = Path(path)
    if raw.is_absolute():
        return raw
    return REPO_ROOT / raw


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _inspect_zip(path: Path) -> list[Any]:
    hygiene_path = REPO_ROOT / "tools" / "release_hygiene.py"
    spec = importlib.util.spec_from_file_location("release_hygiene", hygiene_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {_display_path(hygiene_path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.inspect_zip(str(path))


def _check_zip_hygiene(path: Path) -> Evidence:
    if not path.exists():
        return Evidence(
            status="unknown",
            evidence=f"{_display_path(path)} was not found; package hygiene was not evaluated.",
            claim="No clean source package can be claimed from this artifact.",
        )

    findings = _inspect_zip(path)
    if findings:
        sample = "; ".join(f"{finding.path} ({finding.reason})" for finding in findings[:5])
        suffix = "" if len(findings) <= 5 else f"; +{len(findings) - 5} more"
        return Evidence(
            status="failed",
            evidence=f"{_display_path(path)} contains banned release contents: {sample}{suffix}",
            claim="Release package hygiene failed.",
        )

    return Evidence(
        status="passed",
        evidence=f"{_display_path(path)} passed tools.release_hygiene inspection.",
        claim="The inspected source package excludes known local/generated artifacts.",
    )


def _status_evidence(status: str, provided: str | None, absent_message: str, passed_claim: str) -> Evidence:
    if status == "passed":
        return Evidence(
            status=status,
            evidence=provided or "Operator supplied passed status.",
            claim=passed_claim,
        )
    if status == "failed":
        return Evidence(
            status=status,
            evidence=provided or "Operator supplied failed status.",
            claim=f"{passed_claim} cannot be claimed.",
        )
    if status == "unknown":
        return Evidence(
            status=status,
            evidence=provided or absent_message,
            claim=f"{passed_claim} is unverified.",
        )
    return Evidence(
        status="not_run",
        evidence=provided or absent_message,
        claim=f"{passed_claim} cannot be claimed because no run evidence was provided.",
    )


def _feed_evidence(status: str, feed_name: str) -> Evidence:
    if status == "available":
        return Evidence(
            status=status,
            evidence=f"Operator marked {feed_name} as available.",
            claim=f"{feed_name} can be presented as connected only if live data-estate proof agrees.",
        )
    if status == "pending":
        return Evidence(
            status=status,
            evidence=f"Operator marked {feed_name} as pending.",
            claim=f"{feed_name} cannot be claimed as live.",
        )
    return Evidence(
        status=status,
        evidence=f"No operator evidence was supplied for {feed_name}.",
        claim=f"{feed_name} cannot be claimed as live.",
    )


def _talk_track_pending_claims(path: Path) -> Evidence:
    if not path.exists():
        return Evidence(
            status="unknown",
            evidence=f"{_display_path(path)} was not found.",
            claim="The demo talk track was not checked for MLS/permit source-readiness guardrails.",
        )

    text = path.read_text(encoding="utf-8").lower()
    has_mls_available = (
        "mls" in text
        and "listed-for-sale" in text
        and any(token in text for token in ("live", "available", "connected"))
    )
    has_permit_pending = "building permits" in text and "pending" in text
    has_no_claim_guard = (
        "do not call building permits implemented" in text
        or "do not claim building permits" in text
        or "do not claim permit filings" in text
    )
    if has_mls_available and has_permit_pending and has_no_claim_guard:
        return Evidence(
            status="passed",
            evidence=f"{_display_path(path)} names MLS/listing as live and building permits as pending.",
            claim="The talk track includes explicit source-readiness guardrails.",
        )

    return Evidence(
        status="failed",
        evidence=(
            f"{_display_path(path)} is missing one or more explicit pending-feed guardrails "
            "for live MLS/listing and pending building permits."
        ),
        claim="The demo talk track does not adequately prevent source overclaims.",
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = args.timestamp or datetime.now(UTC).replace(microsecond=0).isoformat()
    release_zip = _repo_path(args.release_zip)

    checks: dict[str, Evidence] = {
        "package_hygiene": (
            _status_evidence(
                args.package_hygiene,
                args.package_hygiene_evidence,
                "Operator supplied package hygiene status without evidence text.",
                "Package hygiene passed",
            )
            if args.package_hygiene is not None
            else _check_zip_hygiene(release_zip)
        ),
        "bundle_validate": _status_evidence(
            args.bundle_validate,
            args.bundle_validate_evidence,
            "No Databricks bundle validate result was supplied.",
            "Databricks bundle validate passed",
        ),
        "sql_python_parity": _status_evidence(
            args.sql_python_parity,
            args.sql_python_parity_evidence,
            "No SQL/Python parity result was supplied.",
            "SQL/Python scoring parity passed",
        ),
        "lakebase_round_trip": _status_evidence(
            args.lakebase_round_trip,
            args.lakebase_round_trip_evidence,
            "No Lakebase round-trip result was supplied.",
            "Lakebase campaign/approval round trip passed",
        ),
        "genie_eval": _status_evidence(
            args.genie_eval,
            args.genie_eval_evidence,
            "No offline Genie eval result was supplied.",
            "Genie regression eval passed",
        ),
        "genie_live": _status_evidence(
            args.genie_live,
            args.genie_live_evidence,
            "No live Genie result was supplied.",
            "Live Genie validation passed",
        ),
        "playwright_live": _status_evidence(
            args.playwright_live,
            args.playwright_live_evidence,
            "No live Playwright result was supplied.",
            "Live Playwright product flow passed",
        ),
        "resilience_drill": _status_evidence(
            args.resilience_drill,
            args.resilience_drill_evidence,
            "No live resilience or degraded-banner drill result was supplied.",
            "Resilience/degraded-mode drill passed",
        ),
        "non_admin_auth": _status_evidence(
            args.non_admin_auth,
            args.non_admin_auth_evidence,
            "No authenticated non-admin token proof was supplied.",
            "Authenticated non-admin authorization proof passed",
        ),
        "source_readiness": _status_evidence(
            args.source_readiness,
            args.source_readiness_evidence,
            "No live source-readiness/data-estate result was supplied.",
            "Live source readiness passed",
        ),
        "mls_listing_status": _feed_evidence(args.mls_listing_status, "MLS/listing feed"),
        "building_permit_status": _feed_evidence(args.building_permit_status, "building permit feed"),
        "talk_track_pending_claims": _talk_track_pending_claims(_repo_path(args.talk_track)),
    }

    return {
        "schema_version": 1,
        "module": "Module 0",
        "generated_at": generated_at,
        "app_url": args.app_url or None,
        "release_zip": _display_path(release_zip),
        "checks": {name: evidence.as_dict() for name, evidence in checks.items()},
        "cannot_claim": cannot_claim(checks),
    }


def cannot_claim(checks: dict[str, Evidence]) -> list[str]:
    blocked: list[str] = []

    if checks["mls_listing_status"].status != "available":
        blocked.append(
            "Cannot claim MLS/listing or listed-for-sale triggers are live until the "
            "Cotality MLS/Listings feed is available and source readiness is proven."
        )
    if checks["building_permit_status"].status != "available":
        blocked.append(
            "Cannot claim building-permit or renovation-trigger segments are live until "
            "the Cotality Building Permits feed is available and source readiness is proven."
        )

    release_checks = (
        "package_hygiene",
        "bundle_validate",
        "sql_python_parity",
        "lakebase_round_trip",
        "genie_eval",
        "genie_live",
        "playwright_live",
        "resilience_drill",
        "non_admin_auth",
        "source_readiness",
    )
    missing_release = [
        CHECK_LABELS[name]
        for name in release_checks
        if checks[name].status in {"unknown", "not_run"}
    ]
    if missing_release:
        blocked.append(
            "Cannot claim full Module 0 release readiness while these checks lack "
            f"run evidence: {', '.join(missing_release)}."
        )

    failed = [CHECK_LABELS[name] for name, evidence in checks.items() if evidence.status == "failed"]
    if failed:
        blocked.append(f"Cannot claim release readiness while failing checks remain: {', '.join(failed)}.")

    return blocked


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Module 0 Release Readiness",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- App URL: `{report['app_url'] or 'not provided'}`",
        f"- Release zip: `{report['release_zip']}`",
        "",
        "## Evidence Summary",
        "",
        "| Area | Status | Evidence | Claim boundary |",
        "|---|---:|---|---|",
    ]

    for name, evidence in report["checks"].items():
        label = CHECK_LABELS[name]
        lines.append(
            f"| {label} | `{evidence['status']}` | {evidence['evidence']} | {evidence['claim']} |"
        )

    lines.extend(["", "## What Cannot Be Claimed", ""])
    for item in report["cannot_claim"]:
        lines.append(f"- {item}")
    if not report["cannot_claim"]:
        lines.append("- No claim blockers were recorded by this artifact.")

    lines.extend(
        [
            "",
            "This artifact records supplied and locally inspectable evidence only. "
            "It does not run Databricks, Lakebase, Genie, or browser validations.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], out: Path) -> tuple[Path, Path]:
    json_path = _repo_path(out)
    md_path = json_path.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSON output path")
    parser.add_argument("--app-url", default=None, help="Deployed Databricks App URL, if known")
    parser.add_argument("--timestamp", default=None, help="Override generated timestamp for tests/CI")
    parser.add_argument("--release-zip", default=str(DEFAULT_RELEASE_ZIP), help="Release zip to inspect")
    parser.add_argument("--talk-track", default=str(TALK_TRACK), help="Demo talk-track markdown to inspect")

    parser.add_argument("--package-hygiene", choices=VALID_CHECK_STATUSES, default=None)
    parser.add_argument("--package-hygiene-evidence", default=None)

    for name in (
        "bundle-validate",
        "sql-python-parity",
        "lakebase-round-trip",
        "genie-eval",
        "genie-live",
        "playwright-live",
        "resilience-drill",
        "non-admin-auth",
        "source-readiness",
    ):
        parser.add_argument(f"--{name}", choices=VALID_CHECK_STATUSES, default="not_run")
        parser.add_argument(f"--{name}-evidence", default=None)

    parser.add_argument("--mls-listing-status", choices=VALID_FEED_STATUSES, default="unknown")
    parser.add_argument("--building-permit-status", choices=VALID_FEED_STATUSES, default="unknown")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    json_path, md_path = write_report(report, Path(args.out))
    print(f"wrote {_display_path(json_path)}")
    print(f"wrote {_display_path(md_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
