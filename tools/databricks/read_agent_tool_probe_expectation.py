#!/usr/bin/env python3
"""Read the independent governed count expected from the live tool probe."""

from __future__ import annotations

import argparse
import re

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STATE = re.compile(r"[A-Z]{2}")
_SEGMENT_CODE = re.compile(r"[a-z][a-z0-9_]*")


def read_expected_count(
    workspace: object,
    *,
    warehouse_id: str,
    catalog: str,
    state: str,
    segment_code: str = "itm",
) -> int:
    if not _IDENTIFIER.fullmatch(catalog):
        raise ValueError("catalog must be a simple Unity Catalog identifier")
    normalized_state = state.strip().upper()
    if not _STATE.fullmatch(normalized_state):
        raise ValueError("state must be a two-letter code")
    normalized_segment = segment_code.strip().lower()
    if not _SEGMENT_CODE.fullmatch(normalized_segment):
        raise ValueError("segment_code must be a lowercase governed segment code")
    response = workspace.statement_execution.execute_statement(  # type: ignore[attr-defined]
        statement=(
            f"SELECT `{catalog}`.`gold`.`fn_build_cohort`("  # noqa: S608 - validated identifier
            f"array('{normalized_segment}'), 'any', "
            f"array('{normalized_state}')) AS cohort_count"
        ),
        warehouse_id=warehouse_id,
        wait_timeout="50s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    status = getattr(response, "status", None)
    raw_state = getattr(getattr(status, "state", None), "value", getattr(status, "state", ""))
    if str(raw_state or "").split(".")[-1].upper() != "SUCCEEDED":
        raise RuntimeError("independent fn_build_cohort expectation query failed")
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("independent fn_build_cohort expectation returned an invalid shape")
    try:
        count = int(rows[0][0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("independent fn_build_cohort expectation was not an integer") from exc
    if count < 0:
        raise RuntimeError("independent fn_build_cohort expectation cannot be negative")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--state", default="CA")
    parser.add_argument("--segment-code", default="itm")
    args = parser.parse_args(argv)
    print(
        read_expected_count(
            WorkspaceClient(),
            warehouse_id=args.warehouse_id,
            catalog=args.catalog,
            state=args.state,
            segment_code=args.segment_code,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
