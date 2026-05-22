"""SQL ↔ Python parity contract test for Module 0 scoring primitives.

This is the crown-jewel test for the gold layer (Slice 3). It locks every
row of every golden fixture against BOTH the Python scorer AND the Unity
Catalog SQL UDF, guaranteeing that the scoring surface (`gold.lead_scores`,
`gold.borrower_360`, `gold.lead_population`, `gold.segment_population`)
never drifts silently from the frozen primitives.

Four functions are checked:
    * ``fn_rate_spread``       -> ``backend.services.scoring.rate_spread_bps``
    * ``fn_in_the_money``      -> ``backend.services.scoring.in_the_money``
    * ``fn_lead_score``        -> ``backend.services.scoring.lead_score``
    * ``fn_next_best_offer``   -> ``backend.services.scoring.next_best_offer``

For each of 60+ golden cases across those four functions:
    1. Run the Python primitive in-process.
    2. Issue a SELECT against ``mip.gold.fn_*`` on the Databricks SQL
       warehouse via the Statement Execution API (stdlib-only -- `urllib` +
       `json`, no additional wheel dependencies).
    3. Assert byte-identical output across all inputs.

The test is GATED on three env vars: ``DATABRICKS_HOST`` /
``DATABRICKS_TOKEN`` / ``DATABRICKS_WAREHOUSE_ID``. When any is missing,
the test SKIPS with a clear message (NOT xfails) so:
    * CI without workspace credentials stays green.
    * A developer with the env vars set gets the parity check on every
      `pytest -q`.
    * A missing env var can never silently mask a parity regression --
      the skip message names the variable that was unset.

Rationale for a live-warehouse test here (vs. spinning up Databricks
Connect or OSS Spark): the UDFs live in Unity Catalog and are compiled
by Databricks Photon; the only way to verify BYTE-IDENTICAL output with
the shipped UDF is to call it on the warehouse. We gate the network
call behind env vars so the check is opt-in for the developer who has
creds, never a CI-flake source.

Non-negotiables:
    * Inputs come from the frozen golden JSONs (tests/fixtures/*.json).
      We do NOT invent synthetic tuples; all coverage already lives
      in the fixtures.
    * Any non-SKIP failure is a scoring-drift regression.
    * The test never mutates state; all queries are SELECTs.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from backend.services.scoring import (
    in_the_money as py_in_the_money,
)
from backend.services.scoring import (
    lead_score as py_lead_score,
)
from backend.services.scoring import (
    next_best_offer as py_next_best_offer,
)
from backend.services.scoring import (
    rate_spread_bps as py_rate_spread_bps,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class WarehouseCreds(NamedTuple):
    host: str
    token: str
    warehouse_id: str

    def __repr__(self) -> str:
        return (
            "WarehouseCreds("
            f"host={self.host!r}, token='<redacted>', warehouse_id={self.warehouse_id!r})"
        )


# ---------------------------------------------------------------------------
# Databricks SQL Statement Execution API client (stdlib only)
# ---------------------------------------------------------------------------


def _creds() -> WarehouseCreds | None:
    """Return (host, token, warehouse_id) when all three env vars are set.

    Returns ``None`` to signal SKIP. ``DATABRICKS_SERVER_HOSTNAME`` is
    accepted as an alias for ``DATABRICKS_HOST`` to match the
    databricks-sql-connector convention used elsewhere in the repo.
    """
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    # Normalize host to scheme+host (strip trailing slash; add https).
    if not host.startswith("http"):
        host = "https://" + host
    host = host.rstrip("/")
    return WarehouseCreds(host, token, warehouse_id)


def _run_sql(host: str, token: str, warehouse_id: str, statement: str) -> Any:
    """Execute a single SELECT against the warehouse; return the one scalar
    value in the first row / first column.

    Uses the `statements` endpoint with `wait_timeout=30s` in synchronous mode
    so the call returns the result inline. stdlib-only so the test has no
    extra wheel dependencies.
    """
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
    result = body.get("result", {})
    data_array = result.get("data_array") or []
    if not data_array or not data_array[0]:
        pytest.fail(f"warehouse statement returned no rows: {statement!r}")
    return data_array[0][0]


# ---------------------------------------------------------------------------
# SQL-literal coercion helpers (typed so we don't accidentally pass a string
# to a BOOLEAN UDF arg)
# ---------------------------------------------------------------------------


def _sql_int(v: int | None) -> str:
    return "NULL" if v is None else f"CAST({int(v)} AS INT)"


def _sql_double(v: float | None) -> str:
    return "NULL" if v is None else f"CAST({float(v)} AS DOUBLE)"


def _sql_bool(v: bool | None) -> str:
    if v is None:
        return "CAST(NULL AS BOOLEAN)"
    return "TRUE" if v else "FALSE"


# ---------------------------------------------------------------------------
# Golden fixture loaders
# ---------------------------------------------------------------------------


def _load_rate_spread_cases() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "rate_spread_golden.json").read_text())["cases"]


def _load_in_the_money_cases() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "in_the_money_golden.json").read_text())["cases"]


def _load_lead_score_cases() -> list[dict[str, Any]]:
    # lead_score_golden.json is a bare list, not a {"cases": [...]} wrapper.
    return json.loads((FIXTURES / "lead_score_golden.json").read_text())


def _load_next_best_offer_cases() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "next_best_offer_golden.json").read_text())["cases"]


# ---------------------------------------------------------------------------
# Warehouse gate — shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def warehouse() -> WarehouseCreds:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "SQL↔Python parity test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable. Fixture coverage continues "
            "to be verified against the Python scorer by tests/unit/."
        )
    return creds


# ---------------------------------------------------------------------------
# Parity tests — one parametrized test per UDF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _load_rate_spread_cases(), ids=lambda c: c["id"])
def test_rate_spread_parity(warehouse: tuple[str, str, str], case: dict[str, Any]) -> None:
    """``fn_rate_spread`` must match ``scoring.rate_spread_bps`` byte-for-byte."""
    host, token, wh = warehouse
    inputs = case["inputs"]
    py_result = py_rate_spread_bps(inputs["current_rate"], inputs["market_rate"])
    sql_result = _run_sql(
        host,
        token,
        wh,
        f"SELECT mip.gold.fn_rate_spread("
        f"{_sql_double(inputs['current_rate'])}, "
        f"{_sql_double(inputs['market_rate'])})",
    )
    assert int(sql_result) == int(py_result) == int(case["expected_bps"]), (
        f"{case['id']}: py={py_result} sql={sql_result} "
        f"expected={case['expected_bps']} — {case.get('note', '')}"
    )


@pytest.mark.parametrize("case", _load_in_the_money_cases(), ids=lambda c: c["id"])
def test_in_the_money_parity(warehouse: tuple[str, str, str], case: dict[str, Any]) -> None:
    """``fn_in_the_money`` must match ``scoring.in_the_money`` byte-for-byte."""
    host, token, wh = warehouse
    inputs = case["inputs"]
    py_result = py_in_the_money(
        inputs["rate_spread_bps"],
        inputs["equity_pct"],
        inputs["min_spread_bps"],
        inputs["min_equity_pct"],
    )
    sql_result = _run_sql(
        host,
        token,
        wh,
        f"SELECT mip.gold.fn_in_the_money("
        f"{_sql_int(inputs['rate_spread_bps'])}, "
        f"{_sql_int(inputs['equity_pct'])}, "
        f"{_sql_int(inputs['min_spread_bps'])}, "
        f"{_sql_int(inputs['min_equity_pct'])})",
    )
    # Databricks SQL returns BOOLEAN as Python bool through the Statement
    # Execution API, but JSON_ARRAY serializes it as the string "true"/"false".
    # Normalize both sides.
    sql_bool = str(sql_result).lower() in ("true", "1", "t")
    assert sql_bool == bool(py_result) == bool(case["expected_itm"]), (
        f"{case['id']}: py={py_result} sql={sql_result} "
        f"expected={case['expected_itm']} — {case.get('note', '')}"
    )


@pytest.mark.parametrize("case", _load_lead_score_cases(), ids=lambda c: c["id"])
def test_lead_score_parity(warehouse: tuple[str, str, str], case: dict[str, Any]) -> None:
    """``fn_lead_score`` must match ``scoring.lead_score`` byte-for-byte."""
    host, token, wh = warehouse
    inputs = case["inputs"]
    py_result = py_lead_score(
        inputs["economic_incentive"],
        inputs["intent_trigger"],
        inputs["fit"],
        inputs["relationship"],
        inputs["evidence"],
    )
    sql_result = _run_sql(
        host,
        token,
        wh,
        f"SELECT mip.gold.fn_lead_score("
        f"{_sql_int(inputs['economic_incentive'])}, "
        f"{_sql_int(inputs['intent_trigger'])}, "
        f"{_sql_int(inputs['fit'])}, "
        f"{_sql_int(inputs['relationship'])}, "
        f"{_sql_int(inputs['evidence'])})",
    )
    assert int(sql_result) == int(py_result) == int(case["expected_score"]), (
        f"{case['id']}: py={py_result} sql={sql_result} "
        f"expected={case['expected_score']} — {case.get('note', '')}"
    )


@pytest.mark.parametrize(
    "case", _load_next_best_offer_cases(), ids=lambda c: c["id"]
)
def test_next_best_offer_parity(
    warehouse: tuple[str, str, str], case: dict[str, Any]
) -> None:
    """``fn_next_best_offer`` must match ``scoring.next_best_offer`` byte-for-byte.

    This is the test that exercises every branch of the decision tree:
    listed / refi_plus_heloc / heloc / refi / cash_out / investor /
    retention / nurture, plus the boundary cases (case_09, case_10).
    """
    host, token, wh = warehouse
    inputs = case["inputs"]
    py_result = py_next_best_offer(
        inputs["rate_spread_bps"],
        inputs["equity_pct"],
        inputs["has_permit"],
        inputs["listed_for_sale"],
        inputs["is_investor"],
        inputs["is_current_customer"],
        inputs["is_competitor_lien"],
        inputs["min_spread_bps"],
        inputs["min_equity_pct"],
        inputs["heloc_equity_min_pct"],
        inputs["cashout_equity_min"],
        inputs["retention_min_spread"],
    )
    sql_result = _run_sql(
        host,
        token,
        wh,
        f"SELECT mip.gold.fn_next_best_offer("
        f"{_sql_int(inputs['rate_spread_bps'])}, "
        f"{_sql_int(inputs['equity_pct'])}, "
        f"{_sql_bool(inputs['has_permit'])}, "
        f"{_sql_bool(inputs['listed_for_sale'])}, "
        f"{_sql_bool(inputs['is_investor'])}, "
        f"{_sql_bool(inputs['is_current_customer'])}, "
        f"{_sql_bool(inputs['is_competitor_lien'])}, "
        f"{_sql_int(inputs['min_spread_bps'])}, "
        f"{_sql_int(inputs['min_equity_pct'])}, "
        f"{_sql_int(inputs['heloc_equity_min_pct'])}, "
        f"{_sql_int(inputs['cashout_equity_min'])}, "
        f"{_sql_int(inputs['retention_min_spread'])})",
    )
    assert str(sql_result) == str(py_result) == str(case["expected_offer"]), (
        f"{case['id']}: py={py_result} sql={sql_result} "
        f"expected={case['expected_offer']} — {case.get('note', '')}"
    )


# ---------------------------------------------------------------------------
# Guard: fixture files exist, counts non-zero
# ---------------------------------------------------------------------------


def test_all_fixtures_loaded() -> None:
    """Defensive: if a future edit empties a golden fixture, the parity
    parametrization would silently emit zero cases and the test pass. Pin
    the counts here so that failure is loud."""
    assert len(_load_rate_spread_cases())     >= 10
    assert len(_load_in_the_money_cases())    >= 10
    assert len(_load_lead_score_cases())      >= 10
    assert len(_load_next_best_offer_cases()) >= 10
