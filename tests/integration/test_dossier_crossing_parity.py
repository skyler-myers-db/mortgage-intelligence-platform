"""Live parity for "Crossed the line" and the competitor-lien funnel measure (W5c).

Against real Unity Catalog:

1. funnel: the latest _ALL / _ALL snapshot that recorded
   ``competitor_lien_borrowers`` (and was taken after the current gold refresh)
   equals ``SUM(is_competitor_lien)`` over gold.borrower_360;
2. ``first_itm_week IS NOT NULL`` implies ``in_the_money``;
3. an eligible (active FIX first lien, bounded rate inside the clamp) borrower
   in the money implies ``first_itm_week IS NOT NULL``;
4. ``COUNT(first_itm_week IS NOT NULL)`` equals gold.rate_window_weekly's
   ``itm_count`` at ``is_latest`` (one population, two tables);
5. ``first_itm_week`` is never before the origination week;
6. ``first_pos_rate_type`` is FIX, ARM or NULL;
7. 200 sampled FIX rows: a Python recompute of the crossing from
   gold.rate_window_weekly with ``scoring.rate_spread_bps`` /
   ``scoring.in_the_money`` (the BROUND mirror) equals the SQL value.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` /
``DATABRICKS_WAREHOUSE_ID``. Stdlib-only HTTP; read-only bounded SELECTs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any

import pytest

from backend.services.databricks_sql_helpers import qualify
from backend.services.scoring import in_the_money, rate_spread_bps

pytestmark = pytest.mark.integration

_B360 = qualify("gold", "borrower_360")
_DOSSIER = qualify("gold", "borrower_dossier")
_FUNNEL = qualify("gold", "funnel_snapshot_daily")
_WINDOW = qualify("gold", "rate_window_weekly")
_LIEN = qualify("silver", "lien_current")
_BOUNDED = qualify("gold", "fn_bounded_mortgage_rate")
_SAMPLE = 200


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
            "wait_timeout": "50s",
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
        with urllib.request.urlopen(req, timeout=60) as resp:
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
            "live crossing parity requires workspace credentials"
        )
    return creds


def _eligible(b: str = "b", lc: str = "lc") -> str:
    return (
        f"{b}.current_rate > 0 AND UPPER(TRIM({lc}.first_pos_rate_type)) = 'FIX' "
        f"AND {_BOUNDED}({lc}.first_pos_rate) > 0.01 AND {_BOUNDED}({lc}.first_pos_rate) < 0.15"
    )


def _count(warehouse: tuple[str, str, str], statement: str) -> int:
    rows = _run_sql(warehouse, statement)
    return int(rows[0][0] or 0) if rows else 0


def test_the_funnel_competitor_lien_count_matches_the_headline(warehouse: tuple[str, str, str]) -> None:
    rows = _run_sql(
        warehouse,
        f"SELECT f.competitor_lien_borrowers, f.snapshot_at >= (SELECT MAX(refreshed_at) FROM {_B360}) "
        f"FROM {_FUNNEL} AS f WHERE f.state = '_ALL' AND f.segment_code = '_ALL' "
        "AND f.competitor_lien_borrowers IS NOT NULL ORDER BY f.snapshot_date DESC LIMIT 1",
    )
    if not rows:
        pytest.skip("no funnel snapshot has recorded competitor_lien_borrowers yet")
    recorded, current = rows[0]
    if str(current).lower() != "true":
        pytest.skip("the latest competitor-lien snapshot predates the current gold refresh")
    headline = _count(warehouse, f"SELECT SUM(CASE WHEN is_competitor_lien THEN 1 ELSE 0 END) FROM {_B360}")
    assert int(recorded) == headline


def test_a_crossing_week_implies_in_the_money(warehouse: tuple[str, str, str]) -> None:
    assert _count(
        warehouse,
        f"SELECT COUNT(*) FROM {_DOSSIER} WHERE first_itm_week IS NOT NULL AND NOT in_the_money",
    ) == 0


def test_an_eligible_borrower_in_the_money_has_a_crossing_week(warehouse: tuple[str, str, str]) -> None:
    assert _count(
        warehouse,
        f"SELECT COUNT(*) FROM {_DOSSIER} AS d JOIN {_B360} AS b ON b.clip = d.clip "
        f"JOIN {_LIEN} AS lc ON lc.clip = d.clip "
        f"WHERE {_eligible()} AND b.in_the_money AND d.first_itm_week IS NULL",
    ) == 0


def test_the_crossing_population_is_the_rate_window_latest_count(warehouse: tuple[str, str, str]) -> None:
    crossing = _count(warehouse, f"SELECT COUNT(*) FROM {_DOSSIER} WHERE first_itm_week IS NOT NULL")
    latest = _count(warehouse, f"SELECT itm_count FROM {_WINDOW} WHERE is_latest LIMIT 1")
    assert crossing == latest


def test_the_crossing_is_never_before_origination_and_the_type_is_closed(warehouse: tuple[str, str, str]) -> None:
    assert _count(
        warehouse,
        f"SELECT COUNT(*) FROM {_DOSSIER} WHERE first_itm_week IS NOT NULL AND first_pos_date IS NOT NULL "
        "AND first_itm_week < CAST(DATE_TRUNC('WEEK', first_pos_date) AS DATE)",
    ) == 0
    assert _count(
        warehouse,
        f"SELECT COUNT(*) FROM {_DOSSIER} WHERE first_pos_rate_type IS NOT NULL "
        "AND first_pos_rate_type NOT IN ('FIX', 'ARM')",
    ) == 0


def _monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def test_a_python_recompute_of_the_crossing_matches_sql(warehouse: tuple[str, str, str]) -> None:
    weeks = [
        (date.fromisoformat(str(week)[:10]), float(rate), str(latest).lower() == "true")
        for week, rate, latest in _run_sql(
            warehouse,
            f"SELECT CAST(observation_week AS STRING), market_rate_fraction, is_latest FROM {_WINDOW} "
            "WHERE market_rate_fraction IS NOT NULL ORDER BY observation_week",
        )
    ]
    assert weeks, "gold.rate_window_weekly is empty"
    sample = _run_sql(
        warehouse,
        f"SELECT {_BOUNDED}(lc.first_pos_rate), b.equity_pct, b.min_spread_bps_applied, "
        "b.min_equity_pct_applied, CAST(d.first_pos_date AS STRING), CAST(d.first_itm_week AS STRING) "
        f"FROM {_DOSSIER} AS d JOIN {_B360} AS b ON b.clip = d.clip JOIN {_LIEN} AS lc ON lc.clip = d.clip "
        f"WHERE {_eligible()} ORDER BY xxhash64(d.clip) LIMIT {_SAMPLE}",
    )
    assert sample, "no eligible FIX borrower in the dossier"
    for note, equity, min_spread, min_equity, first_pos_date, first_itm_week in sample:
        verdicts = [
            in_the_money(rate_spread_bps(float(note), rate), int(equity), int(min_spread), int(min_equity))
            for _week, rate, _latest in weeks
        ]
        latest_index = next((i for i, (_w, _r, latest) in enumerate(weeks) if latest), len(weeks) - 1)
        expected: date | None = None
        if verdicts[latest_index]:
            start = 0
            for index in range(latest_index + 1):
                if not verdicts[index]:
                    start = index + 1
            expected = weeks[start][0]
            if first_pos_date:
                expected = max(expected, _monday(date.fromisoformat(first_pos_date[:10])))
        actual = date.fromisoformat(first_itm_week[:10]) if first_itm_week else None
        assert actual == expected, (note, equity, first_pos_date)
