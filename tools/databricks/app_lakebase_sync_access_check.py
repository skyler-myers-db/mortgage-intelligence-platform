#!/usr/bin/env python3
"""Read-only check of the App's Lakebase synced-catalog access boundary.

``converge_app_lakebase_sync_access --check`` lands here. It resolves the same
App grantees, inventories the same catalog, schemas and tables, and runs the
converger's own ``system.information_schema`` audit for the requested mode.
Instead of raising at the first violation, the audit appends every violation
as a symbolic line. Only bounded SELECT statements run; no GRANT or REVOKE.

Lines name objects by role (sync catalog, sync schema, reviewed synced table,
unreviewed schema or table) and count tables, so the report carries no
catalog, schema, table or principal identifier.
"""

from __future__ import annotations

import argparse

from databricks.sdk import WorkspaceClient
from tools.databricks.check_exit_codes import CHECK_DRIFT, CHECK_EXACT
from tools.databricks.converge_app_lakebase_sync_access import (
    _RESERVED_SYNC_SCHEMAS,
    Mode,
    _catalog_exists,
    _existing_schemas,
    _existing_tables,
    _identity_grantees,
    _postflight,
    _require_text,
    _validate_lower_identifier,
    _validate_sync_tables,
)

_PREFIX = "[mip-lakebase-sync-access]"


def check_app_lakebase_sync_access(
    *,
    warehouse_id: str,
    app_application_id: str,
    app_scim_id: str,
    sync_catalog: str,
    sync_schema: str,
    sync_tables: str,
    mode: Mode,
    workspace: WorkspaceClient | None = None,
) -> list[str]:
    """Return symbolic drift lines; an empty list means the boundary is exact."""

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
    grantees = _identity_grantees(client, application_id=application_id, scim_id=scim_id)
    if not _catalog_exists(client, warehouse_id=warehouse, catalog=catalog):
        return ["sync catalog: absent (runtime convergence refuses)"] if mode == "runtime" else []

    drift: list[str] = []
    existing_schemas = _existing_schemas(client, warehouse_id=warehouse, catalog=catalog)
    tables = _existing_tables(
        client,
        warehouse_id=warehouse,
        catalog=catalog,
        existing_schemas=existing_schemas,
    )
    if mode == "runtime":
        if schema not in existing_schemas:
            drift.append("sync schema: absent (runtime convergence refuses)")
        present = {table for table_schema, table in tables if table_schema == schema}
        missing = set(configured_tables) - present
        if missing:
            drift.append(
                f"reviewed synced tables: {len(missing)} of {len(configured_tables)} absent "
                "(runtime convergence refuses)"
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
        findings=drift,
    )
    return drift


def run_check(args: argparse.Namespace) -> int:
    drift = check_app_lakebase_sync_access(
        warehouse_id=args.warehouse_id,
        app_application_id=args.app_application_id,
        app_scim_id=args.app_scim_id,
        sync_catalog=args.sync_catalog,
        sync_schema=args.sync_schema,
        sync_tables=args.sync_tables,
        mode=args.mode,
    )
    if not drift:
        print(f"{_PREFIX} check passed mode={args.mode}: App sync access is exact")
        return CHECK_EXACT
    for line in drift:
        print(f"{_PREFIX} drift mode={args.mode}: {line}")
    return CHECK_DRIFT
