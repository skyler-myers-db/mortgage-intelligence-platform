"""Live drift check: every lineage-manifest object must exist in UC.

Gated on ``DATABRICKS_HOST`` / ``DATABRICKS_TOKEN`` /
``DATABRICKS_WAREHOUSE_ID`` in the same pattern as
``tests/integration/test_sql_queries.py`` — missing creds SKIP with a
clear message so credential-less CI stays green while a developer with
creds gets the real check on every ``pytest -q``.

Why this test exists: the EvidenceDrawer Lineage tab renders the
repo-committed manifest (``backend/resources/lineage_manifest.json``)
as governed product truth. If a table/view/function named there is
renamed or dropped in Unity Catalog, the UI would be citing lineage
that no longer resolves — this test turns that drift into a red build.

Existence probes use metadata statements (``DESCRIBE TABLE`` /
``DESCRIBE FUNCTION``) rather than information_schema joins because
they work uniformly across the local catalog AND the Delta-Shared
``cotality_mortgage_data`` catalog, and they never scan table data.
"""

from __future__ import annotations

import json
import os
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
    return os.environ.get("MIP_DEFAULT_CATALOG", "mip")


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
