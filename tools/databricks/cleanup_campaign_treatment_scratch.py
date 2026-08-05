#!/usr/bin/env python3
"""Drop deterministic current-run and safely aged treatment scratch tables."""

from __future__ import annotations

import argparse
import re

from databricks.sdk import WorkspaceClient
from tools.databricks.ensure_campaign_treatment_table import execute_sql
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_STALE_TABLE_RE = re.compile(r"^campaign_treatment_cap_smoke_gha_[0-9]+$")


def _identifier(label: str, value: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return normalized


def _result_rows(response: object) -> list[list[object]]:
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
        raise RuntimeError("Scratch cleanup query returned invalid rows")
    return rows


def cleanup_campaign_treatment_scratch(
    *,
    warehouse_id: str,
    catalog: str,
    suffix: str,
    stale_older_than_hours: int | None = None,
    workspace: WorkspaceClient | None = None,
) -> None:
    warehouse = warehouse_id.strip()
    if not warehouse:
        raise ValueError("warehouse_id must be non-empty")
    catalog_name = _identifier("catalog", catalog)
    suffix_name = _identifier("suffix", suffix)
    if stale_older_than_hours is not None and not 1 <= stale_older_than_hours <= 168:
        raise ValueError("stale_older_than_hours must be between 1 and 168")
    table_name = f"campaign_treatment_cap_smoke_{suffix_name}"
    client = workspace or deployment_workspace_client()
    targets = {table_name}
    stale_statement = ""
    if stale_older_than_hours is not None:
        stale_statement = (
            "SELECT table_name FROM system.information_schema.tables "
            f"WHERE table_catalog = '{catalog_name}' AND table_schema = 'audit' "
            "AND table_name RLIKE '^campaign_treatment_cap_smoke_gha_[0-9]+$' "
            f"AND created < CURRENT_TIMESTAMP() - INTERVAL {stale_older_than_hours} HOURS"
        )
        stale_response = execute_sql(
            client,
            warehouse_id=warehouse,
            statement=stale_statement,
        )
        for row in _result_rows(stale_response):
            if len(row) != 1 or not _STALE_TABLE_RE.fullmatch(str(row[0])):
                raise RuntimeError(f"Stale scratch query returned an unsafe row: {row!r}")
            targets.add(str(row[0]))
    for target in sorted(targets):
        relation = ".".join(f"`{part}`" for part in (catalog_name, "audit", target))
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"DROP TABLE IF EXISTS {relation}",
        )
    exact_response = execute_sql(
        client,
        warehouse_id=warehouse,
        statement=(
            "SELECT COUNT(*) FROM system.information_schema.tables "
            f"WHERE table_catalog = '{catalog_name}' AND table_schema = 'audit' "
            f"AND table_name = '{table_name}'"
        ),
    )
    exact_rows = _result_rows(exact_response)
    if exact_rows not in ([['0']], [[0]]):
        raise RuntimeError(f"Scratch cleanup postflight was not zero: {exact_rows!r}")
    if stale_statement:
        stale_response = execute_sql(
            client,
            warehouse_id=warehouse,
            statement=stale_statement,
        )
        stale_rows = _result_rows(stale_response)
        if stale_rows:
            raise RuntimeError(
                f"Stale scratch cleanup postflight was not empty: {stale_rows!r}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--stale-older-than-hours", type=int)
    args = parser.parse_args()
    cleanup_campaign_treatment_scratch(
        warehouse_id=args.warehouse_id,
        catalog=args.catalog,
        suffix=args.suffix,
        stale_older_than_hours=args.stale_older_than_hours,
    )
    print("Verified current and safely aged campaign treatment scratch tables are absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
