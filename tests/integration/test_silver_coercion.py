"""Integration regression guard for the slice13 Wave-2 silver coercion fix.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` / ``DATABRICKS_WAREHOUSE_ID``.

Context
-------
Share column ``owner_1_corporate_indicator`` is STRING 'Y'/'N', not BIGINT
(1/0) as first probed. ``CAST('Y' AS BOOLEAN)`` silently returns NULL in Spark,
so the previous ``CAST(COALESCE(owner_1_corporate_indicator, 0) AS BOOLEAN)``
collapsed every row's ``owner_is_corporate`` to NULL.

Wave-2 coerces explicitly:
``UPPER(TRIM(COALESCE(CAST(owner_1_corporate_indicator AS STRING), ''))) = 'Y'``

Assertion
---------
At least some rows in ``mip.silver.property_master`` must surface
``owner_is_corporate = TRUE`` -- the Module 0 evaluation share has plenty of
corporate-owned property, so an all-FALSE population is prima-facie wrong.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest


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


def _run_sql_rows(
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
    except urllib.error.URLError as exc:  # pragma: no cover -- network
        pytest.skip(f"warehouse unreachable: {exc}")
    status = body.get("status", {}).get("state")
    if status != "SUCCEEDED":
        err = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(f"warehouse statement failed: state={status!r} err={err!r}")
    result = body.get("result", {})
    return result.get("data_array") or []


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "SQL integration test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable."
        )
    return creds


def test_owner_is_corporate_has_true_rows(
    warehouse: tuple[str, str, str],
) -> None:
    """If ``CAST('Y' AS BOOLEAN)`` re-regresses, the TRUE count collapses to 0.

    This asserts at least one TRUE row exists. The Module 0 evaluation share contains
    a well-known non-trivial slice of LLC-owned investment property, so this
    is a safe lower bound that catches the cast regression immediately.
    """
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        "SELECT COUNT(*) AS n "
        "FROM mip.silver.property_master "
        "WHERE owner_is_corporate = TRUE",
    )
    assert rows, "no row returned from owner_is_corporate probe"
    n_true = int(rows[0][0])
    assert n_true > 0, (
        "mip.silver.property_master has 0 rows where owner_is_corporate = TRUE. "
        "This is almost certainly the slice13 Wave-2 Spark BOOLEAN-cast "
        "regression re-appearing: 'Y'/'N' share input with CAST AS BOOLEAN "
        "collapses to NULL. Fix: use UPPER(TRIM(...)) = 'Y'."
    )
