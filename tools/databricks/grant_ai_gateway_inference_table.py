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
import os
import re
import sys
import time
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.services.capability_serving_probes import query_serving_endpoint  # noqa: E402
from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout  # noqa: E402
from tools.databricks.m2m_access_policy import resolve_effective_groups  # noqa: E402

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


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
    details = workspace.serving_endpoints.get(endpoint)
    task = str(getattr(details, "task", None) or "")
    query_serving_endpoint(
        workspace,
        endpoint,
        task=task,
        prompt="Capability grant bootstrap. Reply with one short acknowledgement.",
        max_tokens=16,
        client_request_id=f"mip-grant-bootstrap-{uuid4()}",
    )


def _scalar_count(response: object) -> int:
    result = getattr(response, "result", None)
    rows = getattr(result, "data_array", None) or []
    if len(rows) != 1 or len(rows[0]) != 1:
        raise RuntimeError("grant postflight returned an invalid scalar result")
    try:
        return int(str(rows[0][0]))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("grant postflight returned a non-numeric count") from exc


def _table_name_list(tables: list[str]) -> str:
    if not tables:
        return "''"
    return ", ".join(_quote_literal(table) for table in tables)


def _effective_grantees(workspace: WorkspaceClient, principal: str) -> list[str]:
    escaped = principal.replace('"', '\\"')
    matches = list(
        workspace.service_principals.list(
            filter=f'applicationId eq "{escaped}"',
        )
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one service principal for application id {principal!r}, "
            f"found {len(matches)}"
        )
    sp_id = str(getattr(matches[0], "id", "") or "").strip()
    groups = resolve_effective_groups(workspace, sp_id=sp_id)
    return [principal, *sorted(groups.values())]


def grant_gateway_table_access(
    *,
    warehouse_id: str,
    relation_prefix: str,
    principal: str,
    endpoint: str | None = None,
    timeout_s: float = 1_200.0,
    interval_s: float = 15.0,
) -> list[str]:
    if not 0 <= timeout_s <= 3_600:
        raise ValueError("timeout_s must be between 0 and 3600 seconds")
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    catalog, schema, prefix = _split_relation_prefix(relation_prefix)
    principal_sql = _quote_principal(principal)
    workspace = WorkspaceClient()
    catalog_sql = _quote_identifier(catalog)
    schema_sql = f"{_quote_identifier(catalog)}.{_quote_identifier(schema)}"
    _execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"GRANT USE CATALOG ON CATALOG {catalog_sql} TO {principal_sql}",
    )
    # Remove historical broad grants first. Re-grant only USE SCHEMA and exact
    # concrete target tables below; ownership/inherited residue fails postflight.
    _execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"REVOKE ALL PRIVILEGES ON SCHEMA {schema_sql} FROM {principal_sql}",
    )
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

    all_gateway_tables = _list_prefixed_tables(
        workspace,
        warehouse_id=warehouse_id,
        catalog=catalog,
        schema=schema,
        prefix="mip_agent_gateway_",
    )
    for table in all_gateway_tables:
        relation_sql = f"{schema_sql}.{_quote_identifier(table)}"
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"REVOKE ALL PRIVILEGES ON TABLE {relation_sql} FROM {principal_sql}",
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

    effective_grantees = _table_name_list(_effective_grantees(workspace, principal.strip()))
    catalog_forbidden = _scalar_count(
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"""
            /* mip_gateway_postflight_catalog_forbidden */
            SELECT COUNT(*)
            FROM system.information_schema.catalog_privileges
            WHERE grantee IN ({effective_grantees})
              AND catalog_name = {_quote_literal(catalog)}
              AND UPPER(privilege_type) NOT IN ('USE CATALOG', 'BROWSE')
            """,
        )
    )
    schema_forbidden = _scalar_count(
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"""
            /* mip_gateway_postflight_schema_forbidden */
            SELECT COUNT(*)
            FROM system.information_schema.schema_privileges
            WHERE grantee IN ({effective_grantees})
              AND catalog_name = {_quote_literal(catalog)}
              AND schema_name = {_quote_literal(schema)}
              AND UPPER(privilege_type) <> 'USE SCHEMA'
            """,
        )
    )
    owner_forbidden = _scalar_count(
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"""
            /* mip_gateway_postflight_owner_forbidden */
            SELECT COUNT(*)
            FROM (
                SELECT catalog_owner AS object_owner
                FROM system.information_schema.catalogs
                WHERE catalog_name = {_quote_literal(catalog)}
                UNION ALL
                SELECT schema_owner AS object_owner
                FROM system.information_schema.schemata
                WHERE catalog_name = {_quote_literal(catalog)}
                  AND schema_name = {_quote_literal(schema)}
                UNION ALL
                SELECT table_owner AS object_owner
                FROM system.information_schema.tables
                WHERE table_catalog = {_quote_literal(catalog)}
                  AND table_schema = {_quote_literal(schema)}
                  AND table_name IN ({_table_name_list(all_gateway_tables)})
            ) AS gateway_object_owners
            WHERE object_owner IN ({effective_grantees})
            """,
        )
    )
    target_names = _table_name_list(tables)
    target_select = _scalar_count(
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"""
            /* mip_gateway_postflight_target_select */
            SELECT COUNT(DISTINCT table_name)
            FROM system.information_schema.table_privileges
            WHERE grantee IN ({effective_grantees})
              AND table_catalog = {_quote_literal(catalog)}
              AND table_schema = {_quote_literal(schema)}
              AND table_name IN ({target_names})
              AND UPPER(privilege_type) = 'SELECT'
            """,
        )
    )
    target_forbidden = _scalar_count(
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"""
            /* mip_gateway_postflight_target_forbidden */
            SELECT COUNT(*)
            FROM system.information_schema.table_privileges
            WHERE grantee IN ({effective_grantees})
              AND table_catalog = {_quote_literal(catalog)}
              AND table_schema = {_quote_literal(schema)}
              AND table_name IN ({target_names})
              AND UPPER(privilege_type) <> 'SELECT'
            """,
        )
    )
    obsolete = sorted(set(all_gateway_tables) - set(tables))
    obsolete_privileges = 0
    if obsolete:
        obsolete_privileges = _scalar_count(
            _execute_sql(
                workspace,
                warehouse_id=warehouse_id,
                statement=f"""
                /* mip_gateway_postflight_obsolete */
                SELECT COUNT(*)
                FROM system.information_schema.table_privileges
                WHERE grantee IN ({effective_grantees})
                  AND table_catalog = {_quote_literal(catalog)}
                  AND table_schema = {_quote_literal(schema)}
                  AND table_name IN ({_table_name_list(obsolete)})
                """,
            )
        )
    if (
        catalog_forbidden != 0
        or schema_forbidden != 0
        or owner_forbidden != 0
        or target_select != len(tables)
        or target_forbidden != 0
        or obsolete_privileges != 0
    ):
        raise RuntimeError(
            "AI Gateway grant postflight failed: exact target SELECT or obsolete/broad "
            "privilege absence was not proven"
        )
    return granted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--relation-prefix", required=True)
    parser.add_argument("--principal", required=True)
    parser.add_argument("--endpoint")
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=float(os.environ.get("MIP_AI_GATEWAY_GRANT_TIMEOUT_S", "1200")),
        help="Wait for asynchronous inference-table delivery (maximum 3600 seconds).",
    )
    parser.add_argument(
        "--interval-s",
        type=float,
        default=float(os.environ.get("MIP_AI_GATEWAY_GRANT_INTERVAL_S", "15")),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not 0 <= args.timeout_s <= 3_600:
        raise ValueError("--timeout-s must be between 0 and 3600 seconds")
    if args.interval_s <= 0:
        raise ValueError("--interval-s must be positive")
    granted = grant_gateway_table_access(
        warehouse_id=args.warehouse_id,
        relation_prefix=args.relation_prefix,
        principal=args.principal,
        endpoint=args.endpoint,
        timeout_s=args.timeout_s,
        interval_s=args.interval_s,
    )
    for relation in granted:
        print(f"granted SELECT on {relation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
