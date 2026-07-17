#!/usr/bin/env python3
"""Converge the App identity's Lakebase synced-catalog access boundary.

The deployer-authenticated client inventories the entire registered Lakebase
catalog and removes direct App access from every non-system schema and table.
Runtime convergence restores only catalog/schema use plus table-level read
access to the exact reviewed synced-table allowlist.

Every mutation is followed by a bounded ``system.information_schema`` audit
covering the App service principal and its workspace-visible nested groups.
The audit fails closed on inherited residue, ownership, malformed results, or
results too large to prove completely in one response.
"""

from __future__ import annotations

import argparse
import re
from typing import Literal

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout
from tools.databricks.m2m_access_policy import resolve_effective_groups

Mode = Literal["quiesce", "runtime"]

_LOWER_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,254}$")
_LEGACY_SCHEMAS = ("public", "mip_app")
_RESERVED_SYNC_SCHEMAS = {*_LEGACY_SCHEMAS, "information_schema"}
_ROW_LIMIT = 1001
_WAIT_TIMEOUT = "50s"
_KNOWN_CATALOG_PRIVILEGES = {"USE CATALOG", "BROWSE"}
_RUNTIME_SCHEMA_PRIVILEGES = {"USE SCHEMA"}


def _validate_lower_identifier(label: str, value: str) -> str:
    text = value.strip()
    if not _LOWER_IDENTIFIER_RE.fullmatch(text):
        raise ValueError(
            f"{label} must be a lowercase unquoted identifier (maximum 255 characters)"
        )
    return text


def _validate_sync_tables(value: str) -> tuple[str, ...]:
    raw_names = value.split(",")
    names = tuple(_validate_lower_identifier("sync table", name) for name in raw_names)
    if not names or len(set(names)) != len(names):
        raise ValueError("sync_tables must be a unique comma-separated table allowlist")
    return names


def _require_text(label: str, value: str) -> str:
    text = value.strip()
    if not text or any(ord(character) < 32 for character in text):
        raise ValueError(f"{label} must be non-empty and contain no control characters")
    return text


def _quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _literal_list(values: tuple[str, ...]) -> str:
    if not values:
        raise RuntimeError("Cannot build an empty SQL principal or schema boundary")
    return ", ".join(_quote_literal(value) for value in values)


def _state(response: object) -> str:
    status = getattr(response, "status", None)
    state = getattr(status, "state", "")
    raw = getattr(state, "value", state)
    return str(raw or "").split(".")[-1].strip().upper()


def _execute_sql(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    statement: str,
    label: str,
) -> object:
    response = workspace.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout=_WAIT_TIMEOUT,
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    state = _state(response)
    if state != "SUCCEEDED":
        error = getattr(getattr(response, "status", None), "error", None)
        raise RuntimeError(f"{label} failed with state={state or 'UNKNOWN'}: {error}")
    return response


def _bounded_rows(
    response: object,
    *,
    label: str,
    width: int,
    limit: int = _ROW_LIMIT,
) -> list[list[object] | tuple[object, ...]]:
    result = getattr(response, "result", None)
    if result is None:
        raise RuntimeError(f"{label} returned no SQL result")
    if bool(getattr(result, "truncated", False)):
        raise RuntimeError(f"{label} returned a truncated SQL result")
    if any(
        getattr(result, field, None) not in (None, "", [])
        for field in ("next_chunk_index", "next_chunk_internal_link", "external_links")
    ):
        raise RuntimeError(f"{label} returned a chunked SQL result")

    raw_rows = getattr(result, "data_array", None)
    if raw_rows is None:
        rows: list[list[object] | tuple[object, ...]] = []
    elif isinstance(raw_rows, list):
        rows = raw_rows
    else:
        raise RuntimeError(f"{label} returned an invalid row container")
    if len(rows) >= limit:
        raise RuntimeError(f"{label} saturated its fail-closed row limit")
    if any(not isinstance(row, list | tuple) or len(row) != width for row in rows):
        raise RuntimeError(f"{label} returned an invalid row shape")

    manifest = getattr(response, "manifest", None)
    if bool(getattr(manifest, "truncated", False)):
        raise RuntimeError(f"{label} returned a truncated SQL manifest")
    total_chunks = getattr(manifest, "total_chunk_count", None)
    if total_chunks is not None:
        try:
            parsed_chunks = int(total_chunks)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label} returned an invalid total chunk count") from exc
        if parsed_chunks < 0 or parsed_chunks > 1:
            raise RuntimeError(f"{label} returned an incomplete chunked SQL result")
    total_rows = getattr(manifest, "total_row_count", None)
    if total_rows is not None:
        try:
            parsed_total = int(total_rows)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"{label} returned an invalid total row count") from exc
        if parsed_total < 0 or parsed_total != len(rows) or parsed_total >= limit:
            raise RuntimeError(f"{label} returned an incomplete SQL result")
    return rows


def _query_rows(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    statement: str,
    label: str,
    width: int,
) -> list[list[object] | tuple[object, ...]]:
    return _bounded_rows(
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=statement,
            label=label,
        ),
        label=label,
        width=width,
    )


def _normalize_privilege(value: object, *, label: str) -> str:
    action = " ".join(str(value or "").strip().upper().replace("_", " ").split())
    if not action:
        raise RuntimeError(f"{label} returned an empty privilege")
    return action


def _identity_grantees(
    workspace: WorkspaceClient,
    *,
    application_id: str,
    scim_id: str,
) -> tuple[str, ...]:
    try:
        principal = workspace.service_principals.get(scim_id)
    except Exception as exc:  # noqa: BLE001 - preserve an actionable identity boundary
        raise RuntimeError("Could not hydrate the App service principal by SCIM id") from exc
    hydrated_id = str(getattr(principal, "id", "") or "").strip()
    hydrated_application_id = str(
        getattr(principal, "application_id", None)
        or getattr(principal, "applicationId", None)
        or ""
    ).strip()
    if hydrated_id != scim_id or hydrated_application_id != application_id:
        raise RuntimeError("App service-principal application and SCIM identifiers do not match")

    effective_groups = resolve_effective_groups(workspace, sp_id=scim_id)
    group_names = tuple(sorted(str(name or "").strip() for name in effective_groups.values()))
    if any(not name or any(ord(character) < 32 for character in name) for name in group_names):
        raise RuntimeError("Effective App service-principal group has an invalid display name")
    grantees = (application_id, *group_names)
    if len({name.casefold() for name in grantees}) != len(grantees):
        raise RuntimeError("Effective App principal and group names are not uniquely addressable")
    return grantees


def _catalog_exists(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
) -> bool:
    rows = _query_rows(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        /* mip_sync_catalog_presence */
        SELECT catalog_name
        FROM system.information_schema.catalogs
        WHERE catalog_name = {_quote_literal(catalog)}
        ORDER BY catalog_name
        LIMIT {_ROW_LIMIT}
        """,
        label="Lakebase sync catalog presence",
        width=1,
    )
    if len(rows) > 1:
        raise RuntimeError("Lakebase sync catalog presence returned duplicate rows")
    if not rows:
        return False
    observed = str(rows[0][0] or "").strip()
    if observed != catalog:
        raise RuntimeError("Lakebase sync catalog presence returned an unrelated catalog")
    return True


def _existing_schemas(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
) -> set[str]:
    rows = _query_rows(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        /* mip_sync_schema_presence */
        SELECT schema_name
        FROM system.information_schema.schemata
        WHERE catalog_name = {_quote_literal(catalog)}
        ORDER BY schema_name
        LIMIT {_ROW_LIMIT}
        """,
        label="Lakebase sync schema presence",
        width=1,
    )
    observed: set[str] = set()
    seen: set[str] = set()
    for row in rows:
        schema = str(row[0] or "").strip()
        try:
            schema_name = _validate_lower_identifier("Lakebase schema", schema)
        except ValueError as exc:
            raise RuntimeError("Lakebase sync schema presence returned an invalid schema") from exc
        if schema_name in seen:
            raise RuntimeError("Lakebase sync schema presence returned duplicate rows")
        seen.add(schema_name)
        # Unity Catalog owns this metadata schema. It is inventoried explicitly
        # but is never a mutable application-data boundary.
        if schema_name != "information_schema":
            observed.add(schema_name)
    return observed


def _existing_tables(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
    existing_schemas: set[str],
) -> list[tuple[str, str]]:
    if not existing_schemas:
        return []
    rows = _query_rows(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        /* mip_sync_table_presence */
        SELECT table_schema, table_name
        FROM system.information_schema.tables
        WHERE table_catalog = {_quote_literal(catalog)}
          AND table_schema <> 'information_schema'
        ORDER BY table_schema, table_name
        LIMIT {_ROW_LIMIT}
        """,
        label="Lakebase sync table presence",
        width=2,
    )
    tables: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        schema = str(row[0] or "").strip()
        table = str(row[1] or "").strip()
        if schema not in existing_schemas:
            raise RuntimeError("Lakebase sync table presence returned an unrelated schema")
        try:
            table_name = _validate_lower_identifier("Lakebase synced table", table)
        except ValueError as exc:
            raise RuntimeError("Lakebase sync table presence returned an invalid table") from exc
        relation = (schema, table_name)
        if relation in seen:
            raise RuntimeError("Lakebase sync table presence returned duplicate rows")
        seen.add(relation)
        tables.append(relation)
    return tables


def _revoke_direct_access(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
    existing_schemas: set[str],
    tables: list[tuple[str, str]],
    application_id: str,
) -> None:
    catalog_sql = _quote_identifier(catalog)
    principal_sql = _quote_identifier(application_id)
    for schema, table in tables:
        relation_sql = f"{catalog_sql}.{_quote_identifier(schema)}.{_quote_identifier(table)}"
        for privileges in ("ALL PRIVILEGES", "MANAGE"):
            _execute_sql(
                workspace,
                warehouse_id=warehouse_id,
                statement=(f"REVOKE {privileges} ON TABLE {relation_sql} FROM {principal_sql}"),
                label=f"revoke direct App access on {catalog}.{schema}.{table}",
            )
    for schema in sorted(existing_schemas):
        schema_sql = f"{catalog_sql}.{_quote_identifier(schema)}"
        for privileges in (
            "ALL PRIVILEGES",
            "MANAGE, EXTERNAL USE SCHEMA",
        ):
            _execute_sql(
                workspace,
                warehouse_id=warehouse_id,
                statement=(f"REVOKE {privileges} ON SCHEMA {schema_sql} FROM {principal_sql}"),
                label=f"revoke direct App access on {catalog}.{schema}",
            )
    for privileges in (
        "ALL PRIVILEGES",
        "MANAGE",
    ):
        _execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=f"REVOKE {privileges} ON CATALOG {catalog_sql} FROM {principal_sql}",
            label=f"revoke direct App access on catalog {catalog}",
        )


def _postflight(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    catalog: str,
    sync_schema: str,
    sync_tables: tuple[str, ...],
    application_id: str,
    grantees: tuple[str, ...],
    mode: Mode,
) -> None:
    catalog_rows = _query_rows(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        /* mip_sync_postflight_catalog_privileges */
        SELECT catalog_name, privilege_type, grantee, inherited_from
        FROM system.information_schema.catalog_privileges
        WHERE catalog_name = {_quote_literal(catalog)}
          AND grantee IN ({_literal_list(grantees)})
        ORDER BY catalog_name, privilege_type, grantee
        LIMIT {_ROW_LIMIT}
        """,
        label="effective Lakebase sync catalog privileges",
        width=4,
    )
    direct_catalog_actions: set[str] = set()
    allowed_grantees = set(grantees)
    for catalog_name, privilege, grantee, inherited_from in catalog_rows:
        observed_catalog = str(catalog_name or "").strip()
        observed_grantee = str(grantee or "").strip()
        privilege_source = str(inherited_from or "").strip().upper()
        action = _normalize_privilege(privilege, label="Lakebase sync catalog privileges")
        if (
            observed_catalog != catalog
            or observed_grantee not in allowed_grantees
            or not privilege_source
        ):
            raise RuntimeError("Lakebase sync catalog privileges returned an unrelated grant")
        if action not in _KNOWN_CATALOG_PRIVILEGES:
            raise RuntimeError(f"Unexpected effective Lakebase sync catalog privilege: {action}")
        if observed_grantee != application_id:
            raise RuntimeError("App identity inherits a Lakebase catalog privilege through a group")
        if privilege_source != "NONE":
            raise RuntimeError("Lakebase catalog privilege is inherited from a broader object")
        direct_catalog_actions.add(action)
    expected_catalog_actions = {"USE CATALOG"} if mode == "runtime" else set()
    if direct_catalog_actions != expected_catalog_actions:
        raise RuntimeError(
            "Lakebase sync catalog privileges are not exact: "
            f"expected {sorted(expected_catalog_actions)}, "
            f"observed {sorted(direct_catalog_actions)}"
        )

    schema_rows = _query_rows(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        /* mip_sync_postflight_schema_privileges */
        SELECT catalog_name, schema_name, privilege_type, grantee, inherited_from
        FROM system.information_schema.schema_privileges
        WHERE catalog_name = {_quote_literal(catalog)}
          AND schema_name <> 'information_schema'
          AND grantee IN ({_literal_list(grantees)})
        ORDER BY catalog_name, schema_name, privilege_type, grantee
        LIMIT {_ROW_LIMIT}
        """,
        label="effective Lakebase sync schema privileges",
        width=5,
    )
    target_actions: set[str] = set()
    for catalog_name, schema_name, privilege, grantee, inherited_from in schema_rows:
        observed_catalog = str(catalog_name or "").strip()
        observed_schema = str(schema_name or "").strip()
        observed_grantee = str(grantee or "").strip()
        privilege_source = str(inherited_from or "").strip().upper()
        action = _normalize_privilege(privilege, label="Lakebase sync schema privileges")
        try:
            _validate_lower_identifier("Lakebase schema", observed_schema)
        except ValueError as exc:
            raise RuntimeError(
                "Lakebase sync schema privileges returned an invalid schema"
            ) from exc
        if (
            observed_catalog != catalog
            or observed_grantee not in allowed_grantees
            or not privilege_source
        ):
            raise RuntimeError("Lakebase sync schema privileges returned an unrelated grant")
        if mode == "quiesce":
            raise RuntimeError(
                f"App identity retains an effective quiesced schema privilege: "
                f"{catalog}.{observed_schema} {action}"
            )
        if observed_schema != sync_schema:
            raise RuntimeError("App identity retains access to an unrelated Lakebase schema")
        if observed_grantee != application_id:
            raise RuntimeError(
                "App identity inherits a target Lakebase schema privilege through a group"
            )
        if privilege_source != "NONE":
            raise RuntimeError(
                "Runtime target Lakebase schema privilege is inherited from a broader object"
            )
        target_actions.add(action)
    if mode == "runtime" and target_actions != _RUNTIME_SCHEMA_PRIVILEGES:
        raise RuntimeError(
            "Runtime Lakebase sync schema privileges are not exact: "
            f"expected {sorted(_RUNTIME_SCHEMA_PRIVILEGES)}, observed {sorted(target_actions)}"
        )

    table_rows = _query_rows(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        /* mip_sync_postflight_table_privileges */
        SELECT table_catalog, table_schema, table_name, privilege_type, grantee, inherited_from
        FROM system.information_schema.table_privileges
        WHERE table_catalog = {_quote_literal(catalog)}
          AND table_schema <> 'information_schema'
          AND grantee IN ({_literal_list(grantees)})
        ORDER BY table_catalog, table_schema, table_name, privilege_type, grantee
        LIMIT {_ROW_LIMIT}
        """,
        label="effective Lakebase sync table privileges",
        width=6,
    )
    target_table_actions: set[tuple[str, str]] = set()
    for catalog_name, schema_name, table_name, privilege, grantee, inherited_from in table_rows:
        observed_catalog = str(catalog_name or "").strip()
        observed_schema = str(schema_name or "").strip()
        observed_table = str(table_name or "").strip()
        observed_grantee = str(grantee or "").strip()
        privilege_source = str(inherited_from or "").strip().upper()
        action = _normalize_privilege(privilege, label="Lakebase sync table privileges")
        try:
            _validate_lower_identifier("Lakebase schema", observed_schema)
            _validate_lower_identifier("Lakebase synced table", observed_table)
        except ValueError as exc:
            raise RuntimeError(
                "Lakebase sync table privileges returned an invalid schema or table"
            ) from exc
        if (
            observed_catalog != catalog
            or observed_grantee not in allowed_grantees
            or not privilege_source
        ):
            raise RuntimeError("Lakebase sync table privileges returned an unrelated grant")
        if mode == "quiesce":
            raise RuntimeError(
                f"App identity retains an effective quiesced table privilege: "
                f"{catalog}.{observed_schema}.{observed_table} {action}"
            )
        if (
            observed_schema != sync_schema
            or observed_table not in sync_tables
            or action != "SELECT"
        ):
            raise RuntimeError(
                f"Unexpected effective target Lakebase table privilege: "
                f"{catalog}.{observed_schema}.{observed_table} {action}"
            )
        if observed_grantee != application_id:
            raise RuntimeError(
                "App identity inherits a target Lakebase table privilege through a group"
            )
        if privilege_source != "NONE":
            raise RuntimeError("Target Lakebase table SELECT is not a direct exact-table grant")
        target_table_actions.add((observed_table, action))
    expected_table_actions = {(table, "SELECT") for table in sync_tables}
    if mode == "runtime" and target_table_actions != expected_table_actions:
        raise RuntimeError(
            "Runtime Lakebase sync table privileges are not exact: "
            f"expected {sorted(expected_table_actions)}, observed {sorted(target_table_actions)}"
        )

    ownership_rows = _query_rows(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"""
        /* mip_sync_postflight_ownership */
        SELECT object_kind, catalog_name, schema_name, object_name, owner_name
        FROM (
          SELECT 'CATALOG' AS object_kind, catalog_name,
                 CAST(NULL AS STRING) AS schema_name,
                 CAST(NULL AS STRING) AS object_name,
                 catalog_owner AS owner_name
          FROM system.information_schema.catalogs
          WHERE catalog_name = {_quote_literal(catalog)}
          UNION ALL
          SELECT 'SCHEMA' AS object_kind, catalog_name, schema_name,
                 CAST(NULL AS STRING) AS object_name,
                 schema_owner AS owner_name
          FROM system.information_schema.schemata
          WHERE catalog_name = {_quote_literal(catalog)}
            AND schema_name <> 'information_schema'
          UNION ALL
          SELECT 'TABLE' AS object_kind, table_catalog AS catalog_name,
                 table_schema AS schema_name, table_name AS object_name,
                 table_owner AS owner_name
          FROM system.information_schema.tables
          WHERE table_catalog = {_quote_literal(catalog)}
            AND table_schema <> 'information_schema'
        ) AS lakebase_sync_owners
        WHERE owner_name IN ({_literal_list(grantees)})
        ORDER BY object_kind, catalog_name, schema_name, object_name, owner_name
        LIMIT {_ROW_LIMIT}
        """,
        label="effective Lakebase sync ownership",
        width=5,
    )
    for object_kind, catalog_name, schema_name, object_name, owner_name in ownership_rows:
        kind = str(object_kind or "").strip().upper()
        observed_catalog = str(catalog_name or "").strip()
        observed_schema = str(schema_name or "").strip()
        observed_object = str(object_name or "").strip()
        owner = str(owner_name or "").strip()
        if kind in {"SCHEMA", "TABLE"}:
            try:
                _validate_lower_identifier("Lakebase schema", observed_schema)
            except ValueError as exc:
                raise RuntimeError("Lakebase sync ownership returned an invalid schema") from exc
        if (
            kind not in {"CATALOG", "SCHEMA", "TABLE"}
            or observed_catalog != catalog
            or owner not in allowed_grantees
            or (kind == "CATALOG" and (observed_schema or observed_object))
            or (kind == "SCHEMA" and (not observed_schema or observed_object))
            or (kind == "TABLE" and (not observed_schema or not observed_object))
        ):
            raise RuntimeError("Lakebase sync ownership returned an invalid object")
        raise RuntimeError(
            f"App identity is an effective owner of Lakebase sync {kind.lower()} object"
        )


def converge_app_lakebase_sync_access(
    *,
    warehouse_id: str,
    app_application_id: str,
    app_scim_id: str,
    sync_catalog: str,
    sync_schema: str,
    sync_tables: str,
    mode: Mode,
    workspace: WorkspaceClient | None = None,
) -> bool:
    """Converge and prove exact App access to the Lakebase sync schema.

    Returns ``True`` when the configured sync schema exists.  A missing target
    is safe during quiescence and returns ``False``; runtime convergence fails
    closed because it cannot prove a usable read boundary.
    """

    warehouse = _require_text("warehouse_id", warehouse_id)
    application_id = _require_text("app_application_id", app_application_id)
    scim_id = _require_text("app_scim_id", app_scim_id)
    catalog = _validate_lower_identifier("sync_catalog", sync_catalog)
    schema = _validate_lower_identifier("sync_schema", sync_schema)
    configured_tables = _validate_sync_tables(sync_tables)
    if schema in _RESERVED_SYNC_SCHEMAS:
        raise ValueError("sync_schema must not reuse a legacy or reserved Lakebase schema")
    if mode not in {"quiesce", "runtime"}:
        raise ValueError(f"Unsupported Lakebase sync access mode: {mode!r}")

    client = workspace or WorkspaceClient()
    grantees = _identity_grantees(
        client,
        application_id=application_id,
        scim_id=scim_id,
    )
    if not _catalog_exists(client, warehouse_id=warehouse, catalog=catalog):
        if mode == "runtime":
            raise RuntimeError(
                "Cannot grant runtime access before the Lakebase sync catalog exists"
            )
        return False

    existing_schemas = _existing_schemas(
        client,
        warehouse_id=warehouse,
        catalog=catalog,
    )
    target_exists = schema in existing_schemas
    if mode == "runtime" and not target_exists:
        raise RuntimeError("Cannot grant runtime access before the Lakebase sync schema exists")
    tables = _existing_tables(
        client,
        warehouse_id=warehouse,
        catalog=catalog,
        existing_schemas=existing_schemas,
    )
    if mode == "runtime":
        existing_target_tables = {table for table_schema, table in tables if table_schema == schema}
        missing_target_tables = set(configured_tables) - existing_target_tables
        if missing_target_tables:
            raise RuntimeError(
                "Cannot grant runtime access before every reviewed Lakebase synced table exists: "
                + ", ".join(sorted(missing_target_tables))
            )
    _revoke_direct_access(
        client,
        warehouse_id=warehouse,
        catalog=catalog,
        existing_schemas=existing_schemas,
        tables=tables,
        application_id=application_id,
    )

    if mode == "runtime":
        catalog_sql = _quote_identifier(catalog)
        schema_sql = f"{catalog_sql}.{_quote_identifier(schema)}"
        principal_sql = _quote_identifier(application_id)
        _execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"GRANT USE CATALOG ON CATALOG {catalog_sql} TO {principal_sql}",
            label="grant App use of Lakebase sync catalog",
        )
        _execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"GRANT USE SCHEMA ON SCHEMA {schema_sql} TO {principal_sql}",
            label="grant App use of the Lakebase sync schema",
        )
        for table in configured_tables:
            relation_sql = f"{schema_sql}.{_quote_identifier(table)}"
            _execute_sql(
                client,
                warehouse_id=warehouse,
                statement=f"GRANT SELECT ON TABLE {relation_sql} TO {principal_sql}",
                label=f"grant exact App read access to {catalog}.{schema}.{table}",
            )

    _postflight(
        client,
        warehouse_id=warehouse,
        catalog=catalog,
        sync_schema=schema,
        sync_tables=configured_tables,
        application_id=application_id,
        grantees=grantees,
        mode=mode,
    )
    return target_exists


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--app-application-id", required=True)
    parser.add_argument("--app-scim-id", required=True)
    parser.add_argument("--sync-catalog", required=True)
    parser.add_argument("--sync-schema", required=True)
    parser.add_argument("--sync-tables", required=True)
    parser.add_argument("--mode", choices=("quiesce", "runtime"), required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    exists = converge_app_lakebase_sync_access(
        warehouse_id=args.warehouse_id,
        app_application_id=args.app_application_id,
        app_scim_id=args.app_scim_id,
        sync_catalog=args.sync_catalog,
        sync_schema=args.sync_schema,
        sync_tables=args.sync_tables,
        mode=args.mode,
    )
    if exists:
        print(f"Verified exact App Lakebase sync access in {args.mode} mode")
    else:
        print("Lakebase sync target is absent; verified no App sync-schema access path exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
