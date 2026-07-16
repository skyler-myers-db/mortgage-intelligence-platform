#!/usr/bin/env python3
"""Converge and verify the governed campaign-treatment Delta table contract.

Databricks SQL does not accept inline CHECK constraints in the CREATE TABLE
form used by the catalog bootstrap job. It does support Delta CHECK
constraints through ALTER TABLE ADD CONSTRAINT, and exposes their definitions
as ``delta.constraints.<name>`` table properties. This deploy step adds only
missing constraints, rejects conflicting definitions without dropping them,
and verifies the append-only retention contract before app grants proceed.
"""

from __future__ import annotations

import argparse
import re
import time
from collections.abc import Sequence

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.sql import (
    ExecuteStatementRequestOnWaitTimeout,
    StatementParameterListItem,
)
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE = ("audit", "campaign_treatment_snapshot")
_CONSTRAINTS = {
    "campaign_treatment_row_kind_chk": "row_kind IN ('manifest', 'member')",
    "campaign_treatment_assignment_chk": (
        "assignment IS NULL OR assignment IN ('treatment', 'holdout')"
    ),
}
_TABLE_PROPERTIES = {
    "delta.appendonly": "true",
    "delta.logretentionduration": "interval 2555 days",
    "delta.deletedfileretentionduration": "interval 2555 days",
}
_ACTIVE_STATES = {"PENDING", "RUNNING"}
_STATEMENT_TIMEOUT_S = 1_200.0
_POLL_INTERVAL_S = 2.0


def _validate_identifier(label: str, value: str) -> str:
    text = value.strip()
    if not _IDENTIFIER_RE.fullmatch(text):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return text


def _quoted(value: str) -> str:
    return f"`{_validate_identifier('SQL', value)}`"


def _state(response: object) -> str:
    status = getattr(response, "status", None)
    state = getattr(status, "state", "")
    raw = getattr(state, "value", state)
    return str(raw or "").split(".")[-1].upper()


def execute_sql(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    statement: str,
    timeout_s: float = _STATEMENT_TIMEOUT_S,
    poll_interval_s: float = _POLL_INTERVAL_S,
    parameters: Sequence[StatementParameterListItem] | None = None,
) -> object:
    if timeout_s <= 0 or poll_interval_s < 0:
        raise ValueError("SQL polling bounds must be positive")
    deadline = time.monotonic() + timeout_s
    if parameters is None:
        response = workspace.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=warehouse_id,
            wait_timeout="50s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
        )
    else:
        response = workspace.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=warehouse_id,
            wait_timeout="50s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
            parameters=list(parameters),
        )
    state = _state(response)
    while state in _ACTIVE_STATES:
        statement_id = str(getattr(response, "statement_id", "") or "").strip()
        if not statement_id:
            raise RuntimeError(
                f"SQL statement returned {state} without a statement identifier: {statement}"
            )
        if time.monotonic() >= deadline:
            workspace.statement_execution.cancel_execution(statement_id)
            raise RuntimeError(
                f"SQL statement exceeded the bounded {timeout_s:g}s deadline and was canceled: "
                f"{statement}"
            )
        if poll_interval_s:
            time.sleep(poll_interval_s)
        response = workspace.statement_execution.get_statement(statement_id)
        state = _state(response)
    if state != "SUCCEEDED":
        error = getattr(getattr(response, "status", None), "error", None)
        raise RuntimeError(f"SQL statement did not succeed ({state}): {statement}\n{error}")
    return response


def _rows(response: object) -> Sequence[Sequence[object]]:
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
        raise RuntimeError("SHOW TBLPROPERTIES returned an invalid result")
    return rows


def _table_properties(
    workspace: WorkspaceClient, *, warehouse_id: str, relation: str
) -> dict[str, str]:
    response = execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"SHOW TBLPROPERTIES {relation}",
    )
    properties: dict[str, str] = {}
    for row in _rows(response):
        if len(row) < 2:
            continue
        properties[str(row[0]).strip().lower()] = str(row[1]).strip()
    return properties


def _canonical_expression(value: str) -> str:
    # Normalize keywords, identifiers, quoting, and formatting only outside
    # string literals. Delta's UTF8_BINARY collation makes literal case and
    # whitespace material, so changing quoted bytes would accept drift.
    tokens: list[str] = []
    in_literal = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            tokens.append(char)
            if in_literal and index + 1 < len(value) and value[index + 1] == "'":
                tokens.append("'")
                index += 2
                continue
            in_literal = not in_literal
        elif in_literal:
            tokens.append(char)
        elif char == "`" or char.isspace():
            pass
        else:
            tokens.append(char.lower())
        index += 1
    if in_literal:
        raise RuntimeError("Existing Delta constraint contains an unterminated literal")
    expression = "".join(tokens)
    while expression.startswith("(") and expression.endswith(")"):
        depth = 0
        closes_at_end = True
        in_literal = False
        index = 0
        while index < len(expression):
            char = expression[index]
            if char == "'":
                if in_literal and index + 1 < len(expression) and expression[index + 1] == "'":
                    index += 2
                    continue
                in_literal = not in_literal
            elif not in_literal and char == "(":
                depth += 1
            elif not in_literal and char == ")":
                depth -= 1
                if depth == 0 and index != len(expression) - 1:
                    closes_at_end = False
                    break
            index += 1
        if not closes_at_end or depth != 0:
            break
        expression = expression[1:-1]
    return expression


def _assert_contract(properties: dict[str, str], *, require_all: bool) -> set[str]:
    missing: set[str] = set()
    expected_constraint_keys = {
        f"delta.constraints.{name}" for name in _CONSTRAINTS
    }
    actual_constraint_keys = {
        key for key in properties if key.startswith("delta.constraints.")
    }
    unexpected = actual_constraint_keys - expected_constraint_keys
    if unexpected:
        raise RuntimeError(
            f"Unexpected Delta constraints on governed treatment table: {sorted(unexpected)}"
        )
    for name, expected in _CONSTRAINTS.items():
        key = f"delta.constraints.{name}"
        actual = properties.get(key)
        if actual is None:
            missing.add(name)
            continue
        if _canonical_expression(actual) != _canonical_expression(expected):
            raise RuntimeError(
                f"Existing Delta constraint {name!r} conflicts with the governed definition"
            )
    if require_all and missing:
        raise RuntimeError(f"Missing Delta constraints after convergence: {sorted(missing)}")
    for key, expected in _TABLE_PROPERTIES.items():
        actual = properties.get(key)
        if actual != expected:
            raise RuntimeError(
                f"Campaign treatment table property {key!r} is {actual!r}, "
                f"expected {expected!r}"
            )
    return missing


def ensure_campaign_treatment_table(
    *,
    warehouse_id: str,
    catalog: str,
    allow_absent: bool = False,
    workspace: WorkspaceClient | None = None,
) -> list[str] | None:
    warehouse = warehouse_id.strip()
    if not warehouse:
        raise ValueError("warehouse_id must be non-empty")
    catalog_name = _validate_identifier("catalog", catalog)
    relation = ".".join(_quoted(part) for part in (catalog_name, *_TABLE))
    client = workspace or deployment_workspace_client()
    if allow_absent:
        full_name = ".".join((catalog_name, *_TABLE))
        try:
            client.tables.get(full_name)
        except (NotFound, ResourceDoesNotExist):
            return None

    properties = _table_properties(client, warehouse_id=warehouse, relation=relation)
    missing = _assert_contract(properties, require_all=False)
    converged: list[str] = []
    for name, expression in _CONSTRAINTS.items():
        if name not in missing:
            continue
        statement = (
            f"ALTER TABLE {relation} ADD CONSTRAINT {_quoted(name)} CHECK ({expression})"
        )
        try:
            execute_sql(client, warehouse_id=warehouse, statement=statement)
        except RuntimeError:
            # A concurrent idempotent deploy may have won the ADD race. Re-read
            # and accept only the exact governed definition; all other failures
            # remain fatal and preserve any constraints already in place.
            current = _table_properties(client, warehouse_id=warehouse, relation=relation)
            if name in _assert_contract(current, require_all=False):
                raise
        converged.append(name)

    final = _table_properties(client, warehouse_id=warehouse, relation=relation)
    _assert_contract(final, require_all=True)
    return converged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument(
        "--allow-absent",
        action="store_true",
        help="Succeed without mutation when the authoritative UC table API reports absent.",
    )
    args = parser.parse_args()
    converged = ensure_campaign_treatment_table(
        warehouse_id=args.warehouse_id,
        catalog=args.catalog,
        allow_absent=args.allow_absent,
    )
    if converged is None:
        print("Verified governed campaign treatment table is absent")
    elif converged:
        print(f"Converged and verified Delta constraints: {', '.join(converged)}")
        print("Verified append-only and exact seven-year Delta retention properties")
    else:
        print("Verified existing Delta constraints without mutation")
        print("Verified append-only and exact seven-year Delta retention properties")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
