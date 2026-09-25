"""Live parity for mip.gold.rate_sensitivity_rollup (Rate Lever, wow-stage-1).

The grid claims to be the canonical in-the-money rule re-run at par + step
over the map's addressable population. Against real Unity Catalog:

1. Population -- ``addressable_borrowers`` equals ``COUNT(*)`` of
   ``gold.borrower_360`` per state (the lien LEFT JOIN neither drops nor
   duplicates a borrower), on every step.
2. Step-0 parity -- ``in_the_money_borrowers`` at step 0 equals
   ``SUM(borrower_360.in_the_money)`` per state (gated rows kept with a NULL
   note, base par = borrower_360.market_rate_fraction).
3. Shape -- the grid equals ``RATE_SCENARIO_STEPS_BPS`` for every state, and
   in-the-money is non-increasing as par rises.
4. Rule parity -- the golden cases run through the deployed UC functions give
   the golden verdicts, and 500 sampled borrowers re-derived per step through
   ``SCENARIO_ITM_SQL`` agree with ``rate_scenario.scenario_in_the_money``.
5. Live contactable -- the endpoint's statement reports, at step 0, exactly
   ``COUNT(in_the_money AND eligible)`` per state.
6. Note book -- ``gold.rate_sensitivity_book`` is exactly the non-NULL
   ``NOTE_RATE_GATE_SQL`` set over the rollup's own lien join (both
   directions, one row per CLIP), and its per-state count is the grid's
   ``rate_movable_borrowers``. The endpoint reads the book, never silver.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` /
``DATABRICKS_WAREHOUSE_ID`` (the sibling live tests' gate). Stdlib-only HTTP;
read-only bounded SELECTs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.rate_scenario import (
    NOTE_RATE_GATE_SQL,
    RATE_SCENARIO_STEPS_BPS,
    SCENARIO_ITM_SQL,
    scenario_in_the_money,
)
from backend.services.repositories.databricks_rate_sensitivity import RATE_SENSITIVITY_SQL

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "rate_scenario_golden.json"
_GRID = qualify("gold", "rate_sensitivity_rollup")
_BOOK = qualify("gold", "rate_sensitivity_book")
_B360 = qualify("gold", "borrower_360")
_LIEN = qualify("silver", "lien_current")
_SAMPLE_SIZE = 500


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


def _int(value: Any) -> int:
    return int(float(value)) if value is not None else 0


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "DATABRICKS_HOST / DATABRICKS_TOKEN / DATABRICKS_WAREHOUSE_ID not set; "
            "live rate_sensitivity_rollup checks require workspace credentials"
        )
    return creds


@pytest.fixture(scope="module")
def grid(warehouse: tuple[str, str, str]) -> dict[str, dict[int, dict[str, int]]]:
    rows = _run_sql(
        warehouse,
        "SELECT state, CAST(step_bps AS INT), CAST(addressable_borrowers AS BIGINT), "
        "CAST(rate_movable_borrowers AS BIGINT), CAST(in_the_money_borrowers AS BIGINT) "
        f"FROM {_GRID}",
    )
    if not rows:
        pytest.fail(
            f"{_GRID} is empty -- run the mip_refresh_scores job "
            "(ctas_rate_sensitivity_rollup) before the live checks"
        )
    out: dict[str, dict[int, dict[str, int]]] = {}
    for state, step, addressable, movable, itm in rows:
        out.setdefault(str(state), {})[_int(step)] = {
            "addressable": _int(addressable),
            "rate_movable": _int(movable),
            "in_the_money": _int(itm),
        }
    return out


def test_every_state_carries_exactly_the_python_grid(grid: dict[str, dict[int, dict[str, int]]]) -> None:
    for state, by_step in grid.items():
        assert tuple(sorted(by_step)) == RATE_SCENARIO_STEPS_BPS, state


def test_addressable_and_step_zero_match_borrower_360(
    warehouse: tuple[str, str, str], grid: dict[str, dict[int, dict[str, int]]]
) -> None:
    rows = _run_sql(
        warehouse,
        "SELECT state, CAST(COUNT(*) AS BIGINT), CAST(COUNT_IF(in_the_money) AS BIGINT) "
        f"FROM {_B360} WHERE state IS NOT NULL GROUP BY state",
    )
    live = {str(state): (_int(count), _int(itm)) for state, count, itm in rows}
    assert set(live) == set(grid)
    for state, (count, itm) in live.items():
        for step, cell in grid[state].items():
            assert cell["addressable"] == count, (state, step)
            assert 0 <= cell["rate_movable"] <= count, (state, step)
        assert grid[state][0]["in_the_money"] == itm, state


def test_in_the_money_never_rises_with_par(grid: dict[str, dict[int, dict[str, int]]]) -> None:
    for state, by_step in grid.items():
        series = [by_step[step]["in_the_money"] for step in RATE_SCENARIO_STEPS_BPS]
        assert series == sorted(series, reverse=True), (state, series)


def test_golden_cases_hold_through_the_uc_functions(warehouse: tuple[str, str, str]) -> None:
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]

    def dbl(value: float | None) -> str:
        return "CAST(NULL AS DOUBLE)" if value is None else f"CAST({value!r} AS DOUBLE)"

    values = ", ".join(
        f"('{case['id']}', {dbl(case['note_rate_fraction'])}, {dbl(case['market_rate_fraction'])}, "
        f"{int(case['step_bps'])}, {int(case['equity_pct'])}, {int(case['min_spread_bps'])}, "
        f"{int(case['min_equity_pct'])})"
        for case in cases
    )
    rows = _run_sql(
        warehouse,
        f"SELECT id, {SCENARIO_ITM_SQL} FROM VALUES {values} AS t(id, note_rate_fraction, "
        "market_rate_fraction, step_bps, equity_pct, min_spread_bps_applied, min_equity_pct_applied)",
    )
    verdicts = {str(case_id): str(flag).lower() == "true" for case_id, flag in rows}
    for case in cases:
        assert verdicts[case["id"]] is case["expected_in_the_money"], case["id"]


def test_sampled_borrowers_agree_with_the_python_mirror_at_every_step(
    warehouse: tuple[str, str, str],
) -> None:
    steps = ", ".join(str(step) for step in RATE_SCENARIO_STEPS_BPS)
    rows = _run_sql(
        warehouse,
        "WITH sample AS ( "
        f"  SELECT b.clip, {NOTE_RATE_GATE_SQL} AS note_rate_fraction, b.equity_pct, "
        "    b.min_spread_bps_applied, b.min_equity_pct_applied, b.market_rate_fraction "
        f"  FROM {_B360} AS b LEFT JOIN {_LIEN} AS lc ON lc.clip = b.clip "
        "  WHERE b.state IS NOT NULL "
        f"  ORDER BY XXHASH64(b.clip) LIMIT {_SAMPLE_SIZE} "
        f"), grid AS (SELECT EXPLODE(ARRAY({steps})) AS step_bps) "
        "SELECT note_rate_fraction, market_rate_fraction, step_bps, equity_pct, "
        f"  min_spread_bps_applied, min_equity_pct_applied, {SCENARIO_ITM_SQL} "
        "FROM sample CROSS JOIN grid",
    )
    assert len(rows) == _SAMPLE_SIZE * len(RATE_SCENARIO_STEPS_BPS)
    mismatches = []
    for note, market, step, equity, min_spread, min_equity, flag in rows:
        expected = scenario_in_the_money(
            _opt_float(note),
            _opt_float(market),
            _int(step),
            None if equity is None else _int(equity),
            None if min_spread is None else _int(min_spread),
            None if min_equity is None else _int(min_equity),
        )
        if expected is not (str(flag).lower() == "true"):
            mismatches.append((note, market, step, equity, min_spread, min_equity, flag))
    assert not mismatches, mismatches[:10]


def test_live_step_zero_contactable_is_the_eligible_in_the_money_count(
    warehouse: tuple[str, str, str],
) -> None:
    columns = (
        "state", "step_bps", "scenario_market_rate_pct", "addressable_borrowers",
        "rate_movable_borrowers", "in_the_money_borrowers", "min_spread_bps_applied",
        "min_equity_pct_applied", "book_as_of", "refreshed_at", "contactable_in_the_money",
    )
    endpoint_rows = [dict(zip(columns, row, strict=True)) for row in _run_sql(warehouse, RATE_SENSITIVITY_SQL)]
    reported = {
        str(row["state"]): _int(row["contactable_in_the_money"])
        for row in endpoint_rows
        if _int(row["step_bps"]) == 0
    }
    rows = _run_sql(
        warehouse,
        f"SELECT state, CAST(COUNT_IF(in_the_money) AS BIGINT) FROM {_B360} "
        f"WHERE state IS NOT NULL AND {eligible_sql_predicate()} GROUP BY state",
    )
    live = {str(state): _int(count) for state, count in rows}
    for state, count in reported.items():
        assert count == live.get(state, 0), state


def test_note_book_is_exactly_the_gated_note_set(warehouse: tuple[str, str, str]) -> None:
    gated = (
        f"SELECT b.clip, {NOTE_RATE_GATE_SQL} AS note_rate_fraction "
        f"FROM {_B360} AS b LEFT JOIN {_LIEN} AS lc ON lc.clip = b.clip"
    )
    rows = _run_sql(
        warehouse,
        "WITH gated AS ( "
        f"  SELECT clip, note_rate_fraction FROM ({gated}) AS g WHERE note_rate_fraction IS NOT NULL "
        f"), book AS (SELECT clip, note_rate_fraction FROM {_BOOK}) "
        "SELECT "
        "  (SELECT CAST(COUNT(*) AS BIGINT) FROM (SELECT * FROM book EXCEPT ALL SELECT * FROM gated) AS x), "
        "  (SELECT CAST(COUNT(*) AS BIGINT) FROM (SELECT * FROM gated EXCEPT ALL SELECT * FROM book) AS y), "
        "  (SELECT CAST(COUNT(*) - COUNT(DISTINCT clip) AS BIGINT) FROM book), "
        "  (SELECT CAST(COUNT(*) AS BIGINT) FROM book)",
    )
    extra, missing, duplicate_clips, total = (_int(value) for value in rows[0])
    assert total > 0, f"{_BOOK} is empty -- run mip_refresh_scores (ctas_rate_sensitivity_book)"
    assert (extra, missing, duplicate_clips) == (0, 0, 0)


def test_note_book_count_is_the_grids_rate_movable(
    warehouse: tuple[str, str, str], grid: dict[str, dict[int, dict[str, int]]]
) -> None:
    rows = _run_sql(
        warehouse,
        f"SELECT b.state, CAST(COUNT(*) AS BIGINT) FROM {_BOOK} AS nb "
        f"JOIN {_B360} AS b ON b.clip = nb.clip WHERE b.state IS NOT NULL GROUP BY b.state",
    )
    movable = {str(state): _int(count) for state, count in rows}
    for state, by_step in grid.items():
        assert movable.get(state, 0) == by_step[0]["rate_movable"], state


def test_endpoint_statement_reads_the_book_not_silver() -> None:
    assert _BOOK in RATE_SENSITIVITY_SQL
    assert ".silver." not in RATE_SENSITIVITY_SQL
