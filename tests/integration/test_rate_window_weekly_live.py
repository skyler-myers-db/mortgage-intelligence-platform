"""Live parity for mip.gold.rate_window_weekly (dataviz-08 "why now").

The table's latest week must agree with the scoring path it claims to reuse:

1. Book parity -- ``book_lien_count`` equals the fixed-rate active-lien
   subset of ``mip.gold.borrower_360`` behind the same gates the CTAS applies
   (``current_rate > 0``, ``first_pos_rate_type = 'FIX'``, bounded rate
   strictly inside the 1%..15% clamp).
2. Rule parity -- at the latest week, ``itm_count`` equals the number of
   those liens whose ``borrower_360.in_the_money`` is TRUE. Both sides run
   ``fn_in_the_money(fn_rate_spread(...))`` with the same thresholds, so a
   forked rule in either CTAS shows up here as a count mismatch. The check
   only applies when borrower_360 was scored at the same print the latest
   week carries (the FRED ingest can land between the two refreshes); it
   skips with the two rates otherwise.
3. Shape -- exactly one ``is_latest`` week, no NULL print, and the same book
   columns on every week.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` /
``DATABRICKS_WAREHOUSE_ID`` (identical gate to the sibling live tests).
Stdlib-only HTTP; read-only bounded SELECTs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_BOOK_GATES = (
    "FROM mip.gold.borrower_360 AS b "
    "JOIN mip.silver.lien_current AS lc ON lc.clip = b.clip "
    "WHERE b.current_rate > 0 "
    "AND UPPER(TRIM(lc.first_pos_rate_type)) = 'FIX' "
    "AND mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) > 0.01 "
    "AND mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) < 0.15"
)


def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


def _run_sql(warehouse: tuple[str, str, str], statement: str) -> list[list[Any]]:
    host, token, warehouse_id = warehouse
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
        f"{host}/api/2.0/sql/statements/",
        data=payload,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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
            "live rate_window_weekly checks require workspace credentials"
        )
    return creds


@pytest.fixture(scope="module")
def latest_week(warehouse: tuple[str, str, str]) -> dict[str, Any]:
    rows = _run_sql(
        warehouse,
        "SELECT CAST(market_rate_fraction AS DOUBLE), CAST(itm_count AS BIGINT), "
        "CAST(book_lien_count AS BIGINT) "
        "FROM mip.gold.rate_window_weekly "
        "WHERE series_id = 'MORTGAGE30US' AND is_latest",
    )
    if not rows:
        pytest.fail(
            "mip.gold.rate_window_weekly has no latest MORTGAGE30US week -- run the "
            "mip_refresh_scores job (ctas_rate_window_weekly) before the live checks"
        )
    rate, itm, book = rows[0]
    return {"rate": float(rate), "itm_count": int(itm), "book_lien_count": int(book)}


def test_series_shape_is_one_latest_week_with_no_missing_print(warehouse: tuple[str, str, str]) -> None:
    rows = _run_sql(
        warehouse,
        "SELECT CAST(COUNT(*) AS BIGINT), CAST(COUNT_IF(is_latest) AS BIGINT), "
        "CAST(COUNT_IF(market_rate_pct IS NULL) AS BIGINT), "
        "CAST(COUNT(DISTINCT book_lien_count) AS BIGINT), "
        "CAST(COUNT(DISTINCT book_median_rate_pct) AS BIGINT) "
        "FROM mip.gold.rate_window_weekly WHERE series_id = 'MORTGAGE30US'",
    )
    weeks, latest, null_prints, book_counts, medians = (int(v) for v in rows[0])
    assert weeks > 1, "the rate window charts the FRED history, not one week"
    assert latest == 1, f"exactly one current print expected, found {latest}"
    assert null_prints == 0, "a week without a print must be dropped, not charted"
    assert book_counts == 1 and medians <= 1, "one book (as of the refresh) applied to every week"


def test_book_count_matches_the_borrower_360_fixed_rate_subset(
    warehouse: tuple[str, str, str], latest_week: dict[str, Any]
) -> None:
    rows = _run_sql(warehouse, f"SELECT CAST(COUNT(*) AS BIGINT) {_BOOK_GATES}")
    assert int(rows[0][0]) == latest_week["book_lien_count"]


def test_latest_itm_count_matches_borrower_360_in_the_money(
    warehouse: tuple[str, str, str], latest_week: dict[str, Any]
) -> None:
    rows = _run_sql(
        warehouse,
        "SELECT CAST(COUNT_IF(b.in_the_money) AS BIGINT), "
        "CAST(MIN(b.market_rate_fraction) AS DOUBLE), CAST(MAX(b.market_rate_fraction) AS DOUBLE) "
        f"{_BOOK_GATES}",
    )
    itm, scored_min, scored_max = rows[0]
    if scored_min is None:
        pytest.skip("the fixed-rate book is empty; nothing to compare")
    scored_min, scored_max = float(scored_min), float(scored_max)
    if abs(scored_min - latest_week["rate"]) > 1e-9 or abs(scored_max - latest_week["rate"]) > 1e-9:
        pytest.skip(
            "borrower_360 was scored at a different print "
            f"({scored_min}..{scored_max}) than the latest rate-window week ({latest_week['rate']}); "
            "re-run mip_refresh_scores so both refresh against the same FRED week"
        )
    assert int(itm) == latest_week["itm_count"]
