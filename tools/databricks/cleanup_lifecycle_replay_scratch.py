#!/usr/bin/env python3
"""Drop only the deterministic lifecycle replay tables for one GitHub run."""

from __future__ import annotations

import argparse
import re

from databricks.sdk import WorkspaceClient
from tools.databricks.ensure_campaign_treatment_table import execute_sql
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUFFIX_RE = re.compile(r"^gha_[0-9]+$")
_TABLE_RE = re.compile(r"^lifecycle_replay_(?:target|borrower)_gha_[0-9]+$")
_TABLE_KINDS = ("target", "borrower")


def _catalog(value: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Invalid catalog identifier: {value!r}")
    return normalized


def _suffix(value: str) -> str:
    normalized = value.strip()
    if not _SUFFIX_RE.fullmatch(normalized):
        raise ValueError(f"Invalid lifecycle replay suffix {value!r}; expected gha_[0-9]+")
    return normalized


def _result_rows(response: object) -> list[list[object]]:
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
        raise RuntimeError("Lifecycle replay cleanup query returned invalid rows")
    return rows


def cleanup_lifecycle_replay_scratch(
    *,
    warehouse_id: str,
    catalog: str,
    suffix: str,
    stale_older_than_hours: int | None = None,
    workspace: WorkspaceClient | None = None,
) -> None:
    """Drop and prove absence of the exact audit-schema tables for one run."""
    warehouse = warehouse_id.strip()
    if not warehouse:
        raise ValueError("warehouse_id must be non-empty")
    catalog_name = _catalog(catalog)
    suffix_name = _suffix(suffix)
    if stale_older_than_hours is not None and not 1 <= stale_older_than_hours <= 168:
        raise ValueError("stale_older_than_hours must be between 1 and 168")
    table_names = tuple(f"lifecycle_replay_{kind}_{suffix_name}" for kind in _TABLE_KINDS)
    # These are the only two relations this helper can construct. Schema and
    # table stem are constants; catalog and run suffix are strictly validated.
    client = workspace or deployment_workspace_client()
    targets = set(table_names)
    stale_statement = ""
    if stale_older_than_hours is not None:
        stale_statement = (
            "SELECT table_name FROM system.information_schema.tables "
            f"WHERE table_catalog = '{catalog_name}' AND table_schema = 'audit' "
            "AND table_name RLIKE '^lifecycle_replay_(target|borrower)_gha_[0-9]+$' "
            f"AND created < CURRENT_TIMESTAMP() - INTERVAL {stale_older_than_hours} HOURS"
        )
        stale_response = execute_sql(
            client,
            warehouse_id=warehouse,
            statement=stale_statement,
        )
        for row in _result_rows(stale_response):
            if len(row) != 1 or not _TABLE_RE.fullmatch(str(row[0])):
                raise RuntimeError(
                    f"Lifecycle replay stale query returned an unsafe row: {row!r}"
                )
            targets.add(str(row[0]))
    ordered_targets = (*table_names, *sorted(targets.difference(table_names)))
    for table_name in ordered_targets:
        relation = f"`{catalog_name}`.`audit`.`{table_name}`"
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"DROP TABLE IF EXISTS {relation}",
        )

    expected_names = ", ".join(f"'{name}'" for name in table_names)
    response = execute_sql(
        client,
        warehouse_id=warehouse,
        statement=(
            "SELECT table_name FROM system.information_schema.tables "
            f"WHERE table_catalog = '{catalog_name}' AND table_schema = 'audit' "
            f"AND table_name IN ({expected_names})"
        ),
    )
    rows = _result_rows(response)
    if rows:
        raise RuntimeError(
            "Lifecycle replay scratch cleanup postflight was not empty: " f"{rows!r}"
        )
    if stale_statement:
        stale_response = execute_sql(
            client,
            warehouse_id=warehouse,
            statement=stale_statement,
        )
        stale_rows = _result_rows(stale_response)
        if stale_rows:
            raise RuntimeError(
                "Lifecycle replay stale cleanup postflight was not empty: "
                f"{stale_rows!r}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--stale-older-than-hours", type=int)
    args = parser.parse_args()
    cleanup_lifecycle_replay_scratch(
        warehouse_id=args.warehouse_id,
        catalog=args.catalog,
        suffix=args.suffix,
        stale_older_than_hours=args.stale_older_than_hours,
    )
    print("Verified deterministic lifecycle replay scratch tables are absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
