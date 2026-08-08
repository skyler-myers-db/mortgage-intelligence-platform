"""Fail the deploy when gold was scored against a stale market rate.

2026-08-07 audit C1: gold.borrower_360 carried the 2026-07-06 FRED rate while
silver.market_rates_weekly's ``is_latest`` row was 2026-08-03 — every spread
was 20 bps overstated and In-the-Money was 16.2% overcounted. The deploy
script orders FRED -> silver -> gold correctly, but a standalone FRED run (or
any future re-ordering) silently re-creates the skew. This check makes the
skew loud: it compares the rate gold actually scored with against the rate
the platform currently publishes, and exits non-zero on mismatch.
"""

from __future__ import annotations

import argparse
import sys

from databricks.sdk import WorkspaceClient

_ALIGNMENT_SQL = """
SELECT
  (SELECT MAX(market_rate_fraction) FROM {catalog}.gold.borrower_360) AS gold_rate,
  (SELECT MAX(rate_fraction) FROM {catalog}.silver.market_rates_weekly
    WHERE is_latest = TRUE) AS silver_rate
""".strip()

_TOLERANCE = 1e-9


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="mip")
    args = parser.parse_args()

    client = WorkspaceClient()
    response = client.statement_execution.execute_statement(
        warehouse_id=args.warehouse_id,
        statement=_ALIGNMENT_SQL.format(catalog=args.catalog),
        wait_timeout="50s",
    )
    state = response.status.state.value if response.status and response.status.state else "UNKNOWN"
    rows = (response.result.data_array or []) if response.result else []
    if state != "SUCCEEDED" or not rows:
        print(f"[market-rate-alignment] statement {state}; no row returned", file=sys.stderr)
        return 2
    gold_raw, silver_raw = rows[0][0], rows[0][1]
    if gold_raw is None or silver_raw is None:
        print(
            "[market-rate-alignment] missing rate "
            f"(gold={gold_raw!r}, silver={silver_raw!r})",
            file=sys.stderr,
        )
        return 2
    gold_rate, silver_rate = float(gold_raw), float(silver_raw)
    if abs(gold_rate - silver_rate) > _TOLERANCE:
        print(
            "[market-rate-alignment] STALE: gold scored at "
            f"{gold_rate:.4f} but silver is_latest is {silver_rate:.4f}. "
            "Re-run the gold refresh (mip_refresh_scores) after the FRED "
            "ingest so spreads and In-the-Money use the current rate.",
            file=sys.stderr,
        )
        return 2
    print(f"[market-rate-alignment] aligned at {gold_rate:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
