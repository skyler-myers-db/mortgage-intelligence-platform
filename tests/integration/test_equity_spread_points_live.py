"""S7 live checks for mip.gold.equity_spread_points: parity + warm p95.

Two acceptance gates against the real warehouse:

1. Dot/dossier parity -- sampled rows from ``mip.gold.equity_spread_points``
   must carry the SAME equity_pct / rate_spread_bps / opportunity_score /
   state as ``mip.gold.borrower_dossier`` for the same masked borrower_id.
   ``borrower_dossier`` is what ``/api/borrowers/{id}`` (Borrower 360)
   serves, so a scatter dot and the Borrower 360 page it deep-links to can
   never disagree. score_band must also match the canonical
   ``mip.gold.fn_score_band`` applied to the dossier's score.

2. Warm read latency -- the exact overview (density-bin GROUP BY) and zoom
   (viewport point page + honest count) statement shapes the analytics
   repository issues must hold a warm p95 under the product budget of 1.5s.
   One untimed warm-up run per statement absorbs warehouse cold start; the
   p95 over the timed runs is compared against ``MIP_SCATTER_P95_BUDGET_S``
   (default 1.5 -- override upward only for known-slow shared CI).

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` /
``DATABRICKS_WAREHOUSE_ID`` (identical gate to the sibling live tests).
Stdlib-only HTTP; read-only bounded SELECTs.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from backend.services.economics_scatter import (
    EQUITY_BIN_PCT,
    EQUITY_DOMAIN_MAX,
    MAX_SCATTER_POINT_ROWS,
    SPREAD_BIN_BPS,
    SPREAD_DOMAIN_MAX,
    equity_bin_pct,
    spread_bin_bps,
)

# ---------------------------------------------------------------------------
# Credentials gate + Statement Execution API wrapper (mirrors
# test_borrower_dossier_parity.py so no extra wheel is required).
# ---------------------------------------------------------------------------

P95_BUDGET_S = float(os.environ.get("MIP_SCATTER_P95_BUDGET_S", "1.5"))
_TIMED_RUNS = 6  # per statement; p95 of 6 == max of the 6 timed runs.


def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


def _run_sql(
    host: str,
    token: str,
    warehouse_id: str,
    statement: str,
) -> list[list[Any]]:
    url = f"{host}/api/2.0/sql/statements/"
    payload = json.dumps(
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "30s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover -- network issue
        pytest.skip(f"warehouse unreachable: {exc}")
    status = body.get("status", {}).get("state")
    if status != "SUCCEEDED":
        err = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(f"warehouse statement failed: state={status!r} err={err!r}")
    return body.get("result", {}).get("data_array") or []


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "DATABRICKS_HOST / DATABRICKS_TOKEN / DATABRICKS_WAREHOUSE_ID not set; "
            "live equity_spread_points checks require workspace credentials"
        )
    return creds


# ---------------------------------------------------------------------------
# The exact statement shapes the analytics repository issues (unfiltered
# case), inlined here so the perf numbers measure the production read path,
# not a synthetic simplification.
# ---------------------------------------------------------------------------

_BINS_SQL = (
    "SELECT p.equity_bin_pct, p.spread_bin_bps, CAST(COUNT(*) AS INT) AS borrower_count, "
    "CAST(ROUND(AVG(p.opportunity_score)) AS INT) AS mean_opportunity_score, "
    "CAST(SUM(CASE WHEN p.in_the_money THEN 1 ELSE 0 END) AS INT) AS in_the_money_borrowers, "
    "MAX(p.refreshed_at) AS refreshed_at "
    "FROM mip.gold.equity_spread_points AS p "
    "GROUP BY p.equity_bin_pct, p.spread_bin_bps "
    "ORDER BY p.equity_bin_pct, p.spread_bin_bps"
)

_ZOOM_SQL = (
    "SELECT p.borrower_id, p.display_name, p.primary_segment_code, p.state, "
    "p.equity_pct, p.rate_spread_bps, p.opportunity_score, p.score_band, p.in_the_money "
    "FROM mip.gold.equity_spread_points AS p "
    "WHERE p.equity_pct BETWEEN {eq_min} AND {eq_max} "
    "AND p.rate_spread_bps BETWEEN {sp_min} AND {sp_max} "
    "ORDER BY p.opportunity_score DESC, p.borrower_id "
    f"LIMIT {MAX_SCATTER_POINT_ROWS}"
)

_ZOOM_COUNT_SQL = (
    "SELECT CAST(COUNT(*) AS BIGINT) AS total_matching, MAX(p.refreshed_at) AS refreshed_at "
    "FROM mip.gold.equity_spread_points AS p "
    "WHERE p.equity_pct BETWEEN {eq_min} AND {eq_max} "
    "AND p.rate_spread_bps BETWEEN {sp_min} AND {sp_max}"
)


def _densest_bin(warehouse: tuple[str, str, str]) -> tuple[int, int, int]:
    host, token, wid = warehouse
    rows = _run_sql(
        host,
        token,
        wid,
        _BINS_SQL + " ",  # trailing space keeps this a distinct statement-cache key from the timed run
    )
    if not rows:
        pytest.fail(
            "mip.gold.equity_spread_points is empty -- run the mip_refresh_scores "
            "job (ctas_equity_spread_points) before the live scatter checks"
        )
    densest = max(rows, key=lambda r: int(r[2]))
    return int(densest[0]), int(densest[1]), int(densest[2])


def _zoom_bounds(equity_bin: int, spread_bin: int) -> dict[str, int]:
    return {
        "eq_min": equity_bin,
        "eq_max": min(equity_bin + EQUITY_BIN_PCT - 1, EQUITY_DOMAIN_MAX),
        "sp_min": spread_bin,
        "sp_max": min(spread_bin + SPREAD_BIN_BPS - 1, SPREAD_DOMAIN_MAX),
    }


# ---------------------------------------------------------------------------
# 1. Dot values match the Borrower 360 read model for the same borrower_id.
# ---------------------------------------------------------------------------


def test_sampled_scatter_dots_match_borrower_360_read_model(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    sample = _run_sql(
        host,
        token,
        wid,
        # Deterministic spread across the score range: top under each band
        # edge would be nicer, but a simple hash-ordered sample keeps the
        # statement cheap and stable enough across refreshes.
        "SELECT p.borrower_id, p.equity_pct, p.rate_spread_bps, p.opportunity_score, "
        "p.score_band, p.state "
        "FROM mip.gold.equity_spread_points AS p "
        "ORDER BY xxhash64(p.borrower_id) "
        "LIMIT 12",
    )
    if not sample:
        pytest.fail("mip.gold.equity_spread_points returned no sample rows")

    mismatches: list[str] = []
    for borrower_id, equity, spread, score, band, state in sample:
        safe_id = str(borrower_id).replace("'", "''")
        dossier = _run_sql(
            host,
            token,
            wid,
            "SELECT d.equity_pct, d.rate_spread_bps, d.opportunity_score, d.state, "
            "mip.gold.fn_score_band(d.opportunity_score) "
            f"FROM mip.gold.borrower_dossier AS d WHERE d.borrower_id = '{safe_id}'",
        )
        if not dossier:
            mismatches.append(f"{borrower_id}: missing from borrower_dossier")
            continue
        d_equity, d_spread, d_score, d_state, d_band = dossier[0]
        got = (int(equity), int(spread), int(score), str(state), str(band))
        want = (int(d_equity), int(d_spread), int(d_score), str(d_state), str(d_band))
        if got != want:
            mismatches.append(f"{borrower_id}: scatter={got} borrower_360={want}")
        # Bin coordinates must be the pinned FLOOR formulas of the row values.
        bins = _run_sql(
            host,
            token,
            wid,
            "SELECT equity_bin_pct, spread_bin_bps FROM mip.gold.equity_spread_points "
            f"WHERE borrower_id = '{safe_id}'",
        )
        eq_bin, sp_bin = int(bins[0][0]), int(bins[0][1])
        if eq_bin != equity_bin_pct(int(equity)) or sp_bin != spread_bin_bps(int(spread)):
            mismatches.append(
                f"{borrower_id}: stored bins ({eq_bin},{sp_bin}) != pinned formula "
                f"({equity_bin_pct(int(equity))},{spread_bin_bps(int(spread))})"
            )

    assert mismatches == [], "scatter/Borrower-360 drift:\n" + "\n".join(mismatches)


def test_zoomed_bin_total_matches_overview_bin_count(
    warehouse: tuple[str, str, str],
) -> None:
    """The honest M for a zoomed bin viewport must equal that bin's overview
    count -- the integer-inclusive window contract the UI relies on."""
    host, token, wid = warehouse
    equity_bin, spread_bin, bin_count = _densest_bin(warehouse)
    bounds = _zoom_bounds(equity_bin, spread_bin)
    total = _run_sql(host, token, wid, _ZOOM_COUNT_SQL.format(**bounds))
    assert int(total[0][0]) == bin_count, (
        f"zoom viewport count {total[0][0]} != overview bin count {bin_count} "
        f"for bin ({equity_bin},{spread_bin})"
    )


# ---------------------------------------------------------------------------
# 2. Warm p95 under budget for overview + zoom.
# ---------------------------------------------------------------------------


def _warm_p95(host: str, token: str, wid: str, statement: str) -> float:
    _run_sql(host, token, wid, statement)  # warm-up, untimed
    samples: list[float] = []
    for _ in range(_TIMED_RUNS):
        start = time.perf_counter()
        _run_sql(host, token, wid, statement)
        samples.append(time.perf_counter() - start)
    samples.sort()
    index = max(0, math.ceil(0.95 * len(samples)) - 1)
    return samples[index]


def test_warm_p95_overview_and_zoom_under_budget(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    equity_bin, spread_bin, _ = _densest_bin(warehouse)
    bounds = _zoom_bounds(equity_bin, spread_bin)

    overview_p95 = _warm_p95(host, token, wid, _BINS_SQL)
    zoom_p95 = _warm_p95(host, token, wid, _ZOOM_SQL.format(**bounds))
    count_p95 = _warm_p95(host, token, wid, _ZOOM_COUNT_SQL.format(**bounds))

    print(
        f"\nequity_spread_points warm p95 (n={_TIMED_RUNS} each): "
        f"overview={overview_p95:.3f}s zoom={zoom_p95:.3f}s count={count_p95:.3f}s "
        f"budget={P95_BUDGET_S:.2f}s"
    )
    failures = [
        f"{name} p95 {value:.3f}s > {P95_BUDGET_S:.2f}s budget"
        for name, value in (
            ("overview bins", overview_p95),
            ("zoom points", zoom_p95),
            ("zoom count", count_p95),
        )
        if value > P95_BUDGET_S
    ]
    assert failures == [], "; ".join(failures)
