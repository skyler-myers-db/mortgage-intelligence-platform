"""Live parity for the five gold ``*_points`` columns (audit wow-stage-2, W5c).

gold.borrower_360 projects ``economic_incentive_points`` .. ``evidence_points``
from its own subscores CTE; lead_population and borrower_dossier carry them.
Against real Unity Catalog:

1. borrower_360: zero rows whose five non-NULL points, banker-rounded and
   clipped to 0..100, differ from ``opportunity_score``. Rows with any NULL
   point are counted and reported (a NULL sub-score is a NULL point, never 0).
2. lead_population: no NULL point and zero sum mismatches; 500 sampled rows
   equal borrower_360's points for the same CLIP.
3. borrower_dossier: 500 sampled rows equal borrower_360's points.
4. 500 sampled rows: each point equals its fn_lead_score weight times the
   gold.lead_scores sub-score (the live form of
   test_borrower_360_and_lead_scores_subscore_terms_stay_aligned).

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` /
``DATABRICKS_WAREHOUSE_ID`` (the sibling live tests' gate). Stdlib-only HTTP;
read-only bounded SELECTs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any

import pytest

from backend.services.databricks_sql_helpers import qualify
from backend.services.scoring import _LEAD_SCORE_WEIGHTS

pytestmark = pytest.mark.integration

_B360 = qualify("gold", "borrower_360")
_LP = qualify("gold", "lead_population")
_DOSSIER = qualify("gold", "borrower_dossier")
_SCORES = qualify("gold", "lead_scores")
_SAMPLE = 500
_SUBS = ("economic_incentive", "intent_trigger", "fit", "relationship", "evidence")
_POINTS = tuple(f"{sub}_points" for sub in _SUBS)


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
            "live score-points parity requires workspace credentials"
        )
    return creds


def _sum_expr(alias: str) -> str:
    return " + ".join(f"{alias}.{column}" for column in _POINTS)


def _all_present(alias: str) -> str:
    return " AND ".join(f"{alias}.{column} IS NOT NULL" for column in _POINTS)


def _any_null(alias: str) -> str:
    return " OR ".join(f"{alias}.{column} IS NULL" for column in _POINTS)


def _mismatch(alias: str) -> str:
    return (
        f"LEAST(100, GREATEST(0, CAST(BROUND({_sum_expr(alias)}) AS INT))) "
        f"<> {alias}.opportunity_score"
    )


def test_borrower_360_points_sum_to_the_opportunity_score(warehouse: tuple[str, str, str]) -> None:
    rows = _run_sql(
        warehouse,
        f"SELECT "
        f"SUM(CASE WHEN {_all_present('b')} AND {_mismatch('b')} THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {_any_null('b')} THEN 1 ELSE 0 END), "
        f"COUNT(*) FROM {_B360} AS b",
    )
    mismatches, with_null, total = (int(value or 0) for value in rows[0])
    print(f"borrower_360: {total} rows, {with_null} with a NULL point (reported, not failed)")
    assert total > 0
    assert mismatches == 0


def test_lead_population_points_are_complete_and_match_borrower_360(warehouse: tuple[str, str, str]) -> None:
    rows = _run_sql(
        warehouse,
        f"SELECT SUM(CASE WHEN {_any_null('lp')} THEN 1 ELSE 0 END), "
        f"SUM(CASE WHEN {_all_present('lp')} AND {_mismatch('lp')} THEN 1 ELSE 0 END) "
        f"FROM {_LP} AS lp",
    )
    nulls, mismatches = (int(value or 0) for value in rows[0])
    assert nulls == 0, "lead_population rows are score-qualified: every sub-score exists"
    assert mismatches == 0
    differ = " OR ".join(f"NOT (lp.{c} <=> b.{c})" for c in _POINTS)
    sampled = _run_sql(
        warehouse,
        f"SELECT COUNT(*), SUM(CASE WHEN {differ} THEN 1 ELSE 0 END) FROM ("
        f"SELECT * FROM {_LP} ORDER BY xxhash64(clip) LIMIT {_SAMPLE}) AS lp "
        f"JOIN {_B360} AS b ON b.clip = lp.clip",
    )
    compared, differing = (int(value or 0) for value in sampled[0])
    assert compared > 0
    assert differing == 0


def test_borrower_dossier_points_match_borrower_360(warehouse: tuple[str, str, str]) -> None:
    differ = " OR ".join(f"NOT (d.{c} <=> b.{c})" for c in _POINTS)
    sampled = _run_sql(
        warehouse,
        f"SELECT COUNT(*), SUM(CASE WHEN {differ} THEN 1 ELSE 0 END) FROM ("
        f"SELECT * FROM {_DOSSIER} ORDER BY xxhash64(clip) LIMIT {_SAMPLE}) AS d "
        f"JOIN {_B360} AS b ON b.clip = d.clip",
    )
    compared, differing = (int(value or 0) for value in sampled[0])
    assert compared > 0
    assert differing == 0


def test_each_point_is_its_weight_times_the_lead_scores_sub_score(warehouse: tuple[str, str, str]) -> None:
    columns = ", ".join(
        [f"b.{c}" for c in _POINTS] + [f"s.{sub}" for sub in _SUBS]
    )
    rows = _run_sql(
        warehouse,
        f"SELECT {columns} FROM (SELECT * FROM {_B360} ORDER BY xxhash64(clip) LIMIT {_SAMPLE}) AS b "
        f"JOIN {_SCORES} AS s ON s.clip = b.clip",
    )
    assert rows, "no sampled borrower has a lead_scores row"
    for row in rows:
        points, subs = row[:5], row[5:]
        for weight, point, sub in zip(_LEAD_SCORE_WEIGHTS, points, subs, strict=True):
            if sub is None:
                assert point is None
                continue
            assert Decimal(str(point)) == (weight * Decimal(str(sub))).quantize(Decimal("0.01"))
