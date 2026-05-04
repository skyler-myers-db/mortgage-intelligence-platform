"""Integration regression guard for the slice13 Wave-2 silver ZIP fix.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` / ``DATABRICKS_WAREHOUSE_ID``.

Context
-------
Data contract §2.1 / §2.2 declare ``situs_zip_code`` as a 5-digit STRING in
both ``mip.silver.property_master`` and ``mip.silver.lien_current``. The share
frequently emits 9-digit ZIP+4 (with or without dash) in the same column. The
previous silver transforms passed the raw value through, so ~89% of rows had
9-digit ZIPs that disagreed with the gold-side ``SUBSTR(..., 1, 5)`` cleanup.

Wave-2 pushes the truncation to silver (``SUBSTR(REGEXP_REPLACE(..., '[^0-9]',
''), 1, 5)``) so silver is authoritative and gold no longer needs the
defensive SUBSTR.

Assertion
---------
``MAX(LENGTH(situs_zip_code)) <= 5`` on both silver tables.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass(frozen=True)
class WarehouseCreds:
    host: str
    token: str = field(repr=False)
    warehouse_id: str


def _creds() -> WarehouseCreds | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return WarehouseCreds(host=host.rstrip("/"), token=token, warehouse_id=warehouse_id)


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
def warehouse() -> WarehouseCreds:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "SQL integration test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable."
        )
    return creds


@pytest.mark.parametrize(
    "silver_table",
    ["mip.silver.property_master", "mip.silver.lien_current"],
)
def test_silver_zip_is_five_digits_or_fewer(
    warehouse: WarehouseCreds,
    silver_table: str,
) -> None:
    """If silver ever emits a 6+ char ZIP, gold's geography surfaces drift.

    The data contract is explicit: silver is 5-digit. Any row with
    ``LENGTH(situs_zip_code) > 5`` indicates the slice13 Wave-2 truncation
    regressed in the silver transformation for this table.
    """
    rows = _run_sql_rows(
        warehouse.host,
        warehouse.token,
        warehouse.warehouse_id,
        f"SELECT COALESCE(MAX(LENGTH(situs_zip_code)), 0) AS max_len "
        f"FROM {silver_table}",
    )
    assert rows, f"no row returned from {silver_table} zip-length probe"
    max_len = int(rows[0][0])
    assert max_len <= 5, (
        f"{silver_table}.situs_zip_code MAX(LENGTH) = {max_len}, expected <= 5. "
        "slice13 Wave-2 silver-side truncation has regressed."
    )
