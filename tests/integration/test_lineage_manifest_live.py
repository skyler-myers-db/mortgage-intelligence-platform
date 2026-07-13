"""Live lineage checks for the Evidence Drawer proof surface.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` /
``DATABRICKS_WAREHOUSE_ID`` in the same pattern as
``tests/integration/test_sql_queries.py`` — missing creds SKIP with a
clear message so credential-less CI stays green while a developer with
creds gets the real check on every ``pytest -q``.

Why these tests exist: the EvidenceDrawer Lineage tab renders the
repo-committed manifest (``backend/resources/lineage_manifest.json``)
as governed product truth. If a table/view/function named there is
renamed or dropped in Unity Catalog, the UI would be citing lineage
that no longer resolves. Object probes turn that drift into a red build.
The separate core-edge assertion proves that materialized product edges are
also present in ``system.access.table_lineage``; the manifest may include
parallel co-inputs and UC functions, so it is not treated as a fabricated
one-edge-per-arrow physical graph.

Existence probes use metadata statements (``DESCRIBE TABLE`` /
``DESCRIBE FUNCTION``) rather than information_schema joins because
they work uniformly across the local catalog AND the Delta-Shared
``cotality_mortgage_data`` catalog, and they never scan table data.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

import pytest

from backend.services.lineage_manifest import load_manifest_file


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


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "Lineage manifest live test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable."
        )
    return creds


def _execute(host: str, token: str, warehouse_id: str, statement: str) -> dict[str, Any]:
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
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover -- network
        pytest.skip(f"warehouse unreachable: {exc}")


def _default_catalog() -> str:
    catalog = os.environ.get("MIP_DEFAULT_CATALOG", "mip")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", catalog):
        raise ValueError("MIP_DEFAULT_CATALOG must be a valid Unity Catalog identifier")
    return catalog


def _manifest_objects() -> list[tuple[str, str, str]]:
    """Unique ``(object_type, fqn, cited_by)`` triples across families."""
    manifest = load_manifest_file()
    seen: dict[str, tuple[str, str, str]] = {}
    for family in manifest.families:
        for node in family.nodes:
            catalog = node.catalog or _default_catalog()
            fqn = f"{catalog}.{node.schema_name}.{node.object_name}"
            seen.setdefault(fqn, (node.object_type, fqn, family.id))
    return sorted(seen.values(), key=lambda item: item[1])


@pytest.mark.parametrize(
    ("object_type", "fqn", "cited_by"),
    _manifest_objects(),
    ids=[fqn for _, fqn, _ in _manifest_objects()],
)
def test_manifest_object_exists_in_unity_catalog(
    warehouse: tuple[str, str, str],
    object_type: str,
    fqn: str,
    cited_by: str,
) -> None:
    host, token, warehouse_id = warehouse
    verb = "FUNCTION" if object_type == "function" else "TABLE"
    quoted = ".".join(f"`{part}`" for part in fqn.split("."))
    body = _execute(host, token, warehouse_id, f"DESCRIBE {verb} {quoted}")
    state = body.get("status", {}).get("state")
    if state != "SUCCEEDED":
        message = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(
            f"lineage manifest drift: {object_type} {fqn} (cited by family "
            f"{cited_by!r}) failed DESCRIBE {verb}: state={state!r} err={message!r}"
        )
    assert body.get("result", {}).get("data_array"), (
        f"DESCRIBE {verb} {fqn} returned no rows"
    )


def test_core_materialized_edges_are_observed_in_unity_catalog(
    warehouse: tuple[str, str, str],
) -> None:
    """Prove the physical edges behind two primary app read surfaces.

    These CTAS edges are deliberately small and stable. Requiring every
    adjacent manifest node would be false precision because consecutive raw
    nodes can be parallel inputs and UC functions do not emit table-lineage
    edges of their own.
    """

    catalog = _default_catalog().lower()
    expected_edges = {
        (
            f"{catalog}.gold.borrower_360",
            f"{catalog}.gold.lead_population",
        ),
        (
            f"{catalog}.gold.borrower_360",
            f"{catalog}.gold.equity_spread_points",
        ),
    }
    edge_predicate = " OR ".join(
        "(lower(source_table_full_name) = "
        f"'{source}' AND lower(target_table_full_name) = '{target}')"
        for source, target in sorted(expected_edges)
    )
    statement = (
        "SELECT lower(source_table_full_name) AS source_table_full_name, "
        "lower(target_table_full_name) AS target_table_full_name, "
        "COUNT(*) AS event_count "
        "FROM system.access.table_lineage "
        "WHERE event_date >= current_date() - INTERVAL 90 DAYS AND ("
        f"{edge_predicate}) "
        "GROUP BY source_table_full_name, target_table_full_name"
    )
    host, token, warehouse_id = warehouse
    body = _execute(host, token, warehouse_id, statement)
    state = body.get("status", {}).get("state")
    if state != "SUCCEEDED":
        message = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(
            "observed Unity Catalog lineage query failed: "
            f"state={state!r} err={message!r}"
        )
    rows = body.get("result", {}).get("data_array") or []
    observed = {
        (str(row[0]).lower(), str(row[1]).lower())
        for row in rows
        if isinstance(row, list) and len(row) >= 3 and int(row[2] or 0) > 0
    }
    assert observed == expected_edges, (
        "core observed-lineage drift: "
        f"missing={sorted(expected_edges - observed)} "
        f"unexpected={sorted(observed - expected_edges)}"
    )
