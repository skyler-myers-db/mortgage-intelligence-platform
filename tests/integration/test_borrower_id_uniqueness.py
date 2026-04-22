"""Integration regression guard for the slice13 Wave-2 borrower_id widening.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` / ``DATABRICKS_WAREHOUSE_ID``
in the same pattern as ``test_sql_queries.py`` -- missing creds produce a SKIP
with a clear message (never xfail), so CI without workspace credentials stays
green and a developer with creds gets the real check on every ``pytest -q``.

Context
-------
Prior gold formula
``CONCAT('B-', LPAD(ABS(XXHASH64(clip)) % 99999 + 10000, 5, '0'))`` collapsed
~5.16M CLIPs into ~90K synthetic ids (avg 57 collisions per id, worst 688),
so ``SELECT ... WHERE borrower_id = :id LIMIT 1`` would return a
non-deterministic CLIP when the user clicked a row in the Lead Queue and the
Borrower 360 route fetched the underlying record.

Wave-2 widens to base36(ABS(XXHASH64(clip))) padded to 13 chars:
``CONCAT('B-', LPAD(CONV(CAST(ABS(XXHASH64(clip)) AS STRING), 10, 36), 13, '0'))``.
36^13 is ~1.7e20 slots for 5.16M rows -- collision probability negligible.

Assertion
---------
``COUNT(*) - COUNT(DISTINCT borrower_id)`` over ``mip.gold.borrower_360`` is 0.
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


def test_borrower_id_is_unique_over_borrower_360(
    warehouse: tuple[str, str, str],
) -> None:
    """borrower_id is the PK the router keys on -- collisions are unshippable.

    Asserts ``COUNT(*) == COUNT(DISTINCT borrower_id)`` so a CLIP -> borrower_id
    inverse lookup is deterministic. If this fails the router's
    ``WHERE borrower_id = :id LIMIT 1`` selects a non-deterministic row per
    request, and the Borrower 360 page silently shows different borrowers to
    different users for the same clicked id.
    """
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        "SELECT COUNT(*) - COUNT(DISTINCT borrower_id) AS collisions "
        "FROM mip.gold.borrower_360",
    )
    assert rows, "no row returned from mip.gold.borrower_360 collision probe"
    collisions = int(rows[0][0])
    assert collisions == 0, (
        f"mip.gold.borrower_360 has {collisions} borrower_id collisions -- "
        "slice13 Wave-2 regression. The gold formula must be base36(xxhash64)."
    )


def test_borrower_id_matches_expected_format(
    warehouse: tuple[str, str, str],
) -> None:
    """Shape guard: the new format is ``B-`` plus 13 base36 characters.

    A stale DDL/table from before Wave-2 would still have the 5-digit format
    and slip past the uniqueness test only if the population happens to be
    small enough to not collide -- this assertion catches that case.
    """
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        "SELECT COUNT(*) AS bad "
        "FROM mip.gold.borrower_360 "
        "WHERE NOT (borrower_id RLIKE '^B-[0-9A-Z]{13}$')",
    )
    assert rows, "no row returned from borrower_id format probe"
    bad = int(rows[0][0])
    assert bad == 0, (
        f"{bad} rows in mip.gold.borrower_360 have a borrower_id that does "
        "not match the slice13 Wave-2 format B-[13-char base36]. Rerun the "
        "silver -> gold refresh."
    )
