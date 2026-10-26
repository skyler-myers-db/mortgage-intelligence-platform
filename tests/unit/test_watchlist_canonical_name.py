"""A watchlist's default name is canonical: sorted states and segment codes.

W5d growth-watchlists cut 4a. The backend's run-ledger Save
(``default_watchlist_name``) and the frontend's workflow and custom Saves
(``canonicalWatchlistName``) read the same case table,
``tests/fixtures/watchlist_name_cases.json``, so one scope picked in any order
names one watchlist on every save path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.services.growth_agent_run_watchlist import default_watchlist_name

CASES_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "watchlist_name_cases.json"
CASES: list[dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _run(case: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if case["segment_codes"] is not None:
        filters = {"segment_codes": case["segment_codes"], "segment_mode": case["segment_mode"]}
    return {
        "workflow_id": case["workflow_id"],
        "workflow_title": case["title"],
        "criteria": {"states": case["states"], "lead_queue_filters": filters},
    }


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_default_watchlist_name_matches_the_shared_case(case: dict[str, Any]) -> None:
    assert default_watchlist_name(_run(case)) == case["expected"]


def test_the_case_table_covers_order_independence() -> None:
    by_id = {case["id"]: case for case in CASES}
    one, other = by_id["custom-any-unsorted"], by_id["custom-same-scope-other-order"]
    assert one["states"] != other["states"] and one["segment_codes"] != other["segment_codes"]
    assert default_watchlist_name(_run(one)) == default_watchlist_name(_run(other))
