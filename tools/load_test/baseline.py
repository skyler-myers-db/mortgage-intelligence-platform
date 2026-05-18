#!/usr/bin/env python3
"""Compare Locust stats against the committed Module 0 load baseline."""
from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _float(row: dict[str, str], *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            try:
                return float(value)
            except ValueError:
                return 0.0
    return 0.0


def _endpoint_key(row: dict[str, str]) -> str:
    method = row.get("Type") or row.get("Method") or ""
    name = row.get("Name") or row.get("name") or ""
    return f"{method} {name}".strip()


def read_locust_stats(path: Path) -> dict[str, dict[str, float]]:
    metrics: dict[str, dict[str, float]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = row.get("Name") or row.get("name") or ""
            if name == "Aggregated":
                continue
            key = _endpoint_key(row)
            if not key:
                continue
            total = _float(row, "Request Count")
            failures = _float(row, "Failure Count")
            metrics[key] = {
                "p50_ms": _float(row, "50%", "Median Response Time"),
                "p95_ms": _float(row, "95%"),
                "p99_ms": _float(row, "99%"),
                "request_count": total,
                "requests_per_second": _float(row, "Requests/s"),
                "failure_count": failures,
                "failure_rate_pct": (failures / total * 100.0) if total else 0.0,
            }
    return metrics


def _load_baseline(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _is_write_endpoint(key: str, expected: dict[str, Any] | None = None) -> bool:
    if expected and expected.get("profile") == "write-opt-in":
        return True
    return any(part in key for part in ("/genie/", "/outreach/", "/portfolio/create"))


def compare(
    stats: dict[str, dict[str, float]],
    baseline: dict[str, Any],
    *,
    write_enabled: bool = False,
) -> list[str]:
    regressions: list[str] = []
    global_fail_budget = float(baseline.get("global_failure_rate_budget_pct", 2.0))
    for key, observed in stats.items():
        expected = (baseline.get("endpoints") or {}).get(key)
        if not isinstance(expected, dict):
            continue
        fail_rate = observed["failure_rate_pct"]
        endpoint_fail_budget = float(expected.get("failure_rate_budget_pct", global_fail_budget))
        if fail_rate > endpoint_fail_budget:
            regressions.append(
                f"{key}: failure rate {fail_rate:.2f}% exceeds budget {endpoint_fail_budget:.2f}%"
            )
        if write_enabled and not _is_write_endpoint(key, expected):
            # Read endpoints have their own sustained read-only baseline.
            # Mixed write drills include a small, noisy sample of reads so
            # users can keep borrowing real IDs, but those reads should not
            # overwrite or fail the 20-user read profile.
            continue
        p95_budget = expected.get("p95_budget_ms")
        if p95_budget is not None and observed["p95_ms"] > float(p95_budget):
            regressions.append(
                f"{key}: p95 {observed['p95_ms']:.0f}ms exceeds budget {float(p95_budget):.0f}ms"
            )
        baseline_p95 = expected.get("p95_ms")
        tolerance_pct = float(expected.get("regression_tolerance_pct", baseline.get("regression_tolerance_pct", 25.0)))
        if baseline_p95 is not None:
            allowed = float(baseline_p95) * (1.0 + tolerance_pct / 100.0)
            if observed["p95_ms"] > allowed:
                regressions.append(
                    f"{key}: p95 {observed['p95_ms']:.0f}ms exceeds baseline tolerance {allowed:.0f}ms"
                )
    return regressions


def write_baseline(
    *,
    path: Path,
    stats: dict[str, dict[str, float]],
    target: str,
    write_enabled: bool,
) -> None:
    existing: dict[str, Any] = {}
    if path.exists():
        existing = _load_baseline(path)
    existing_endpoints = existing.get("endpoints") if isinstance(existing.get("endpoints"), dict) else {}
    endpoints: dict[str, dict[str, Any]] = {
        key: dict(value) for key, value in existing_endpoints.items()
    }
    for key, observed in stats.items():
        is_write_endpoint = _is_write_endpoint(key, endpoints.get(key))
        if write_enabled and not is_write_endpoint and key in endpoints:
            # Preserve the read-only baseline while recording write-path
            # measurements from the same mixed Locust run.
            continue
        merged = dict(endpoints.get(key, {}))
        merged.update(observed)
        if "p95_budget_ms" not in merged:
            merged["p95_budget_ms"] = None
        if "failure_rate_budget_pct" not in merged:
            merged["failure_rate_budget_pct"] = existing.get("global_failure_rate_budget_pct", 2.0)
        if write_enabled and is_write_endpoint and "regression_tolerance_pct" not in merged:
            # Write drills intentionally run at low volume because they
            # create real Lakebase/audit rows and may invoke Genie. Their
            # p95 is useful as an observed baseline, but the pass/fail gate
            # should be the explicit endpoint budget plus zero/low failure
            # rate, not a tight percent delta on 1-10 samples.
            merged["regression_tolerance_pct"] = 100.0
        merged["profile"] = (
            "write-opt-in"
            if write_enabled and is_write_endpoint
            else merged.get("profile", "read")
        )
        endpoints[key] = merged
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "target": target,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "profile": "mixed-read-write" if write_enabled else "read-only",
        "global_failure_rate_budget_pct": existing.get("global_failure_rate_budget_pct", 2.0),
        "regression_tolerance_pct": existing.get("regression_tolerance_pct", 25.0),
        "notes": existing.get("notes"),
        "endpoints": endpoints,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stats_csv", type=Path)
    parser.add_argument("--baseline", type=Path, default=Path("tools/load_test/baseline.json"))
    parser.add_argument("--target", default="unknown")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--write-enabled", action="store_true")
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)

    stats = read_locust_stats(args.stats_csv)
    if args.write_baseline:
        write_baseline(
            path=args.baseline,
            stats=stats,
            target=args.target,
            write_enabled=args.write_enabled,
        )
        print(f"wrote load baseline: {args.baseline}")
        return 0

    if not args.baseline.exists():
        print(f"warning: load baseline missing: {args.baseline}", file=sys.stderr)
        return 0

    regressions = compare(
        stats,
        _load_baseline(args.baseline),
        write_enabled=args.write_enabled,
    )
    if regressions:
        print()
        print("baseline comparison:")
        for item in regressions:
            print(f"  WARN {item}")
        return 1 if args.fail_on_regression else 0

    print()
    print("baseline comparison: no p95/failure-rate regressions against committed baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
