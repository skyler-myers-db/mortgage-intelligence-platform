#!/usr/bin/env python3
"""wow-ai-5 spike: what would re-running a pinned Genie tile's rows cost the guards?

Prototype-first tooling (audit 2026-09-21 wow-ai-5). Living tiles are NOT
built: this tool only measures. ``--offline`` (deterministic, no network)
builds tile-sized synthetic row sets inline, runs the row path a refresh would
re-run fail-closed, and counts the guard work per refresh:

1. the SQL trust policy
   (repositories.databricks_genie_trust._trusted_sql_policy);
2. row redaction (databricks_genie_policy_helpers._redact_genie_rows);
3. the cell-surface output guard (genie_message_policy.genie_visible_text_unsafe
   on every visible cell, structured) and governed_row_literals.

It imports and calls those guards READ-ONLY: it never edits or monkeypatches a
guard module. Work is counted two ways: Python function calls into backend
modules (``sys.setprofile``) and characters handed to the scanners. One
representative answer's prose scan, on the same counters, is the narrative
baseline. Wall time is informational only and printed with the load average.

``--live`` (W5c, tools/genie_tiles_live.py) probes criteria 2-4 against a
deployed space with the SDK's read calls and a capped number of attachment
executes: ``python -m tools.genie_tiles_spike --live --profile P --space-id S
[--other-profile P2] [--max-executions 2] [--allow-wake] [--app-identity]
[--json out]``; exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE. See
docs/genie-tiles-spike.md. Output carries only ids, states, counts, sizes
and timings, never question text, SQL text or row values. Nothing in
backend/ or frontend/ imports this tool.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import FrameType
from typing import Any

from backend.services.genie_message_policy import genie_visible_text_unsafe, governed_row_literals
from backend.services.repositories.databricks_genie_policy_helpers import _redact_genie_rows
from backend.services.repositories.databricks_genie_trust import (
    _trusted_genie_asset_names,
    _trusted_sql_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = str(REPO_ROOT / "backend")
TILE_COUNT = 6
ROW_SIZES = (10, 50, 200)
MAX_COLUMNS = 12
STATES = ("TX", "FL", "CA", "AZ", "GA", "NC", "OH", "CO")
SEGMENTS = ("Prime Refi Candidates", "HELOC Equity Owners", "Listed for Sale", "Investor Portfolios")
OFFERS = ("Rate-and-term refinance", "HELOC", "Cash-out refinance", "Purchase pre-approval")
NARRATIVE = (
    "Texas leads the in-the-money population this week, with the largest share of borrowers whose "
    "current note rate sits well above today's par rate. Florida and Arizona follow, driven by "
    "loans originated in the 2023 rate peak. Equity is strongest in the Prime Refi Candidates "
    "segment, where the average combined loan-to-value is under sixty percent, which also makes "
    "those households natural HELOC conversations. Listed-for-sale activity is concentrated in "
    "Georgia and North Carolina metros, so purchase pre-approval offers should lead there. "
    "Investor portfolios with three or more properties remain a smaller but higher-balance group. "
    "Review the top counties by opportunity score before building the next campaign, and confirm "
    "every recommendation in the evidence drawer before approving outreach."
)
FOLLOW_UPS = (
    "Which counties in Texas have the most in-the-money borrowers?",
    "How does average equity compare across segments?",
    "Show the listed-for-sale trend by state for the last eight weeks.",
)


def _masked_id(index: int) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    digits = []
    value = index * 7919 + 104729
    for _ in range(13):
        digits.append(alphabet[value % 36])
        value //= 36
    return "B-" + "".join(digits)


def tile_rows(tile: int, rows: int) -> list[dict[str, Any]]:
    """Aggregate-shaped synthetic rows (deterministic), with masked-id and PII-shaped cells.

    Every tile carries a masked borrower id column and a PII-keyed contact
    column (redaction drops it); tile 5 also carries a phone-shaped value in a
    non-PII column, which the cell guard must flag.
    """

    out: list[dict[str, Any]] = []
    for i in range(rows):
        row: dict[str, Any] = {
            "state": STATES[(tile + i) % len(STATES)],
            "segment_label": SEGMENTS[(tile + i) % len(SEGMENTS)],
            "offer_label": OFFERS[(tile * 3 + i) % len(OFFERS)],
            "county_name": f"County {chr(65 + (i % 26))}",
            "borrower_count": 100 + (tile * 37 + i * 11) % 900,
            "avg_opportunity_score": round(55 + ((tile + i) % 40) * 0.9, 1),
            "avg_equity_pct": round(20 + ((tile * 5 + i) % 60) * 0.7, 2),
            "avg_rate_spread_bps": 25 + (tile * 13 + i * 7) % 150,
            "top_borrower_id": _masked_id(tile * 1000 + i),
            "contact_email": f"owner{i}@summit.example",
            "week_start": f"2026-09-{1 + (i % 28):02d}",
        }
        if tile == TILE_COUNT - 1:
            row["note"] = f"call back 512-555-{1000 + i:04d}"
        out.append(dict(list(row.items())[:MAX_COLUMNS]))
    return out


def tile_sql(tile: int, asset: str) -> str:
    return (
        f"SELECT state, segment_label, COUNT(*) AS borrower_count FROM {asset} "
        f"WHERE tile_bucket = {tile} GROUP BY state, segment_label"
    )


class WorkCounter:
    """Counts Python calls into backend modules while active (sys.setprofile)."""

    def __init__(self) -> None:
        self.calls = 0
        self.c_calls = 0

    def _profile(self, frame: FrameType, event: str, arg: object) -> None:
        if event == "call" and frame.f_code.co_filename.startswith(BACKEND_DIR):
            self.calls += 1
        elif event == "c_call" and frame.f_code.co_filename.startswith(BACKEND_DIR):
            self.c_calls += 1

    def run(self, work: Callable[[], Any]) -> Any:
        previous = sys.getprofile()
        sys.setprofile(self._profile)
        try:
            return work()
        finally:
            sys.setprofile(previous)


def refresh_row_path(sql: str, rows: list[dict[str, Any]], asset: str) -> dict[str, Any]:
    """The fail-closed path a refresh re-runs; returns the outcome, never values."""

    trusted = _trusted_sql_policy(sql, [asset])
    redacted = _redact_genie_rows(rows) or []
    flagged = 0
    for row in redacted:
        for key, value in row.items():
            for text in (str(key), value):
                if isinstance(text, str) and genie_visible_text_unsafe(
                    text, structured_value=True, governed_cell_values=frozenset()
                ):
                    flagged += 1
    literals = governed_row_literals(redacted)
    return {"sql_trusted": trusted, "rows": len(redacted), "flagged_cells": flagged, "literals": len(literals)}


def scanned_chars(sql: str, rows: list[dict[str, Any]]) -> int:
    redacted = _redact_genie_rows(rows) or []
    cells = sum(len(str(k)) + (len(v) if isinstance(v, str) else 0) for row in redacted for k, v in row.items())
    return len(sql) + cells


def measure_offline() -> dict[str, Any]:
    asset = sorted(_trusted_genie_asset_names())[0]
    sizes: dict[str, Any] = {}
    for size in ROW_SIZES:
        counter = WorkCounter()
        outcomes = []
        chars = 0
        started = time.perf_counter()
        for tile in range(TILE_COUNT):
            sql, rows = tile_sql(tile, asset), tile_rows(tile, size)
            outcomes.append(counter.run(partial(refresh_row_path, sql, rows, asset)))
            chars += scanned_chars(sql, rows)
        sizes[str(size)] = {
            "tiles": TILE_COUNT,
            "rows_per_tile": size,
            "guard_calls": counter.calls,
            "guard_c_calls": counter.c_calls,
            "chars_scanned": chars,
            "calls_per_row": round(counter.calls / (TILE_COUNT * size), 2),
            "tiles_trusted": sum(1 for o in outcomes if o["sql_trusted"]),
            "tiles_with_flagged_cells": sum(1 for o in outcomes if o["flagged_cells"]),
            "wall_ms_informational": round((time.perf_counter() - started) * 1000, 1),
        }
    prose = WorkCounter()
    started = time.perf_counter()
    prose.run(lambda: [genie_visible_text_unsafe(text) for text in (NARRATIVE, *FOLLOW_UPS)])
    baseline = {
        "guard_calls": prose.calls,
        "guard_c_calls": prose.c_calls,
        "chars_scanned": len(NARRATIVE) + sum(len(q) for q in FOLLOW_UPS),
        "wall_ms_informational": round((time.perf_counter() - started) * 1000, 1),
    }
    largest = sizes[str(max(ROW_SIZES))]
    smallest = sizes[str(min(ROW_SIZES))]
    linear = abs(largest["calls_per_row"] - smallest["calls_per_row"]) <= 0.25 * smallest["calls_per_row"]
    within = largest["guard_calls"] <= baseline["guard_calls"]
    return {
        "mode": "offline",
        "sizes": sizes,
        "narrative_baseline": baseline,
        "criterion_1": {
            "linear_in_rows": linear,
            "six_max_tiles_within_one_prose_scan": within,
            "ratio_six_max_tiles_to_prose": round(largest["guard_calls"] / max(1, baseline["guard_calls"]), 2),
            "verdict": "PASS" if linear and within else "FAIL",
        },
        "load_average": [round(x, 2) for x in os.getloadavg()],
    }


def main(argv: list[str] | None = None, *, client_factory: Callable[[str], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true", help="run the deterministic guard-cost measurement")
    mode.add_argument("--live", action="store_true", help="probe criteria 2-4 against a deployed Genie space")
    parser.add_argument("--profile", help="--live: the Databricks CLI profile to read with")
    parser.add_argument("--space-id", help="--live: the Genie space id")
    parser.add_argument("--other-profile", help="--live: a second identity for the isolation execute")
    parser.add_argument("--max-executions", type=int, default=2, help="--live: executes per age bucket (default 2)")
    parser.add_argument("--allow-wake", action="store_true", help="--live: execute even when the warehouse is not RUNNING")
    parser.add_argument("--app-identity", action="store_true", help="--live: the profile IS the App's service principal")
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args(argv)
    code = 0
    if args.offline:
        result = measure_offline()
    else:
        if not args.profile or not args.space_id:
            parser.error("--live needs --profile and --space-id")
        from tools.genie_tiles_live import measure_live, sdk_client_factory

        result, code = measure_live(
            client_factory or sdk_client_factory,
            profile=args.profile,
            space_id=args.space_id,
            other_profile=args.other_profile,
            max_executions=args.max_executions,
            allow_wake=args.allow_wake,
            app_identity=args.app_identity,
        )
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
