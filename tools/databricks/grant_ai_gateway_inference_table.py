#!/usr/bin/env python3
"""Grant the app service principal least-privilege AI Gateway log access.

AI Gateway inference logging is configured with a Unity Catalog table prefix
such as ``mip.audit.mip_agent_gateway_llama``. Databricks materializes one or
more concrete tables with that prefix. The running app only needs SELECT on
those concrete prefixed tables so it can verify capability-probe log rows; it
does not need schema-wide SELECT on ``mip.audit``.
"""

from __future__ import annotations

import argparse
import re
import time
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MIP_GATEWAY_PREFIX_RE = re.compile(r"^mip_agent_gateway_[A-Za-z0-9_]{3,}$")
_LIKE_ESCAPE = "\\"


def _validate_identifier(label: str, value: str) -> str:
    text = value.strip()
    if not _IDENTIFIER_RE.fullmatch(text):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return text


def _quote_identifier(value: str) -> str:
    _validate_identifier("identifier", value)
    return f"`{value}`"


def _quote_principal(value: str) -> str:
    text = value.strip()
    if not text or "`" in text:
        raise ValueError("Principal must be non-empty and must not contain backticks.")
    return f"`{text}`"


def _split_relation_prefix(relation: str) -> tuple[str, str, str]:
    parts = [part.strip() for part in relation.split(".")]
    if len(parts) != 3 or any(not part for part in parts):
        raise ValueError(f"Expected catalog.schema.table_prefix, got {relation!r}.")
    catalog, schema, prefix = (
        _validate_identifier("catalog", parts[0]),
        _validate_identifier("schema", parts[1]),
        _validate_identifier("table prefix", parts[2]),
    )
    if not _MIP_GATEWAY_PREFIX_RE.fullmatch(prefix):
        raise ValueError(
            "AI Gateway inference table prefix must be an app-owned "
            "`mip_agent_gateway_<name>` prefix."
        )
    return catalog, schema, prefix


def _state(response: object) -> str:
    status = getattr(response, "status", None)
    state = getattr(status, "state", "")
    raw = getattr(state, "value", state)
    return str(raw or "").split(".")[-1].upper()


def _execute_sql(workspace: WorkspaceClient, *, warehouse_id: str, statement: str) -> object:
    response = workspace.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    state = _state(response)
    if state != "SUCCEEDED":
        error = getattr(getattr(response, "status", None), "error", None)
        raise RuntimeError(f"SQL statement did not succeed ({state}): {statement}\n{error}")
    return response


def _table_names(response: object, *, prefix: str) -> list[str]:
    result = getattr(response, "result", None)
    rows = getattr(result, "data_array", None) or []
    names: list[str] = []
    for row in rows:
        if not row:
            continue
        table_name = str(row[0]).strip()
        if not table_name.startswith(prefix):
            continue
        names.append(_validate_identifier("table", table_name))
    return names


def _escape_like_literal(value: str) -> str:
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


def _list_prefixed_tables(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
    schema: str,
    prefix: str,
) -> list[str]:
    escaped_prefix = _escape_like_literal(prefix)
    response = _execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        SELECT table_name
        FROM system.information_schema.tables
        WHERE table_catalog = '{catalog}'
          AND table_schema = '{schema}'
          AND (table_name = '{prefix}' OR table_name LIKE '{escaped_prefix}%' ESCAPE '\\\\')
        ORDER BY table_name
        """,
    )
    return _table_names(response, prefix=prefix)


def _bootstrap_gateway_table(workspace: WorkspaceClient, *, endpoint: str) -> None:
    workspace.serving_endpoints.query(
        endpoint,
        messages=[
            ChatMessage(
                role=ChatMessageRole.USER,
                content="Capability grant bootstrap. Reply with one short acknowledgement.",
            )
        ],
        max_tokens=16,
        temperature=0.0,
        client_request_id=f"mip-grant-bootstrap-{uuid4()}",
    )


def grant_gateway_table_access(
    *,
    warehouse_id: str,
    relation_prefix: str,
    principal: str,
    endpoint: str | None = None,
    timeout_s: float = 90.0,
    interval_s: float = 5.0,
) -> list[str]:
    catalog, schema, prefix = _split_relation_prefix(relation_prefix)
    principal_sql = _quote_principal(principal)
    workspace = WorkspaceClient()
    schema_sql = f"{_quote_identifier(catalog)}.{_quote_identifier(schema)}"
    _execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"GRANT USE SCHEMA ON SCHEMA {schema_sql} TO {principal_sql}",
    )

    tables = _list_prefixed_tables(
        workspace,
        warehouse_id=warehouse_id,
        catalog=catalog,
        schema=schema,
        prefix=prefix,
    )
    if not tables and endpoint:
        _bootstrap_gateway_table(workspace, endpoint=endpoint)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            time.sleep(interval_s)
            tables = _list_prefixed_tables(
                workspace,
                warehouse_id=warehouse_id,
                catalog=catalog,
                schema=schema,
                prefix=prefix,
            )
            if tables:
                break

    if not tables:
        raise RuntimeError(
            f"No AI Gateway inference tables matching {relation_prefix!r} were visible."
        )

    granted: list[str] = []
    for table in tables:
        relation_sql = f"{schema_sql}.{_quote_identifier(table)}"
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"GRANT SELECT ON TABLE {relation_sql} TO {principal_sql}",
        )
        granted.append(f"{catalog}.{schema}.{table}")
    return granted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--relation-prefix", required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--endpoint")
    return parser


def main() -> int:
    args = _parser().parse_args()
    granted = grant_gateway_table_access(
        warehouse_id=args.warehouse_id,
        relation_prefix=args.relation_prefix,
        principal=args.principal,
        endpoint=args.endpoint,
    )
    for relation in granted:
        print(f"granted SELECT on {relation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
