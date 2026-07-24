#!/usr/bin/env python3
"""Exercise the AI Gateway verifier identity's effective denial boundary.

Workspace SCIM is not authoritative when Databricks automatic identity
management hides nested account membership. This release gate therefore runs
read-only control-plane and metadata probes as the verifier identity. It never
invokes a non-target model, executes SQL on a non-target warehouse, or mutates
permissions.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable
from functools import partial
from typing import Any

import requests

from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout
from tools.databricks.authenticated_app_denial import (
    verify_authenticated_app_denial,
)
from tools.databricks.authorization_denial import is_authorization_denied

_RELATION_RE = re.compile(
    r"^(?P<catalog>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)$"
)
def _is_denied(exc: BaseException) -> bool:
    return is_authorization_denied(exc)


def _expect_denied(label: str, operation: Callable[[], object]) -> None:
    try:
        operation()
    except Exception as exc:  # noqa: BLE001 - exact denial classification below
        if _is_denied(exc):
            return
        raise RuntimeError(f"{label} was inconclusive: {type(exc).__name__}: {exc}") from exc
    raise RuntimeError(f"{label} unexpectedly succeeded")


def _state(response: object) -> str:
    status = getattr(response, "status", None)
    value = getattr(getattr(status, "state", None), "value", getattr(status, "state", ""))
    return str(value or "").split(".")[-1].upper()


def _response_error(response: object) -> str:
    error = getattr(getattr(response, "status", None), "error", None)
    return str(error or "")


def _require_sql_success(
    workspace: Any,
    *,
    warehouse_id: str,
    statement: str,
    label: str,
) -> object:
    response = workspace.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
    )
    state = _state(response)
    if state != "SUCCEEDED":
        raise RuntimeError(f"{label} failed with state={state}: {_response_error(response)}")
    return response


def _relation_parts(relation: str) -> tuple[str, str, str]:
    match = _RELATION_RE.fullmatch(relation.strip())
    if match is None:
        raise ValueError(f"expected catalog.schema.table, got {relation!r}")
    return match.group("catalog"), match.group("schema"), match.group("table")


def _visible_data_relations(response: object) -> set[str]:
    """Return non-system relations visible through effective UC privileges."""
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if len(rows) >= 1001:
        raise RuntimeError("UC visibility probe saturated its fail-closed relation limit")
    relations: set[str] = set()
    for row in rows:
        if len(row) != 3:
            raise RuntimeError("UC visibility probe returned an invalid row shape")
        parts = tuple(str(value or "").strip() for value in row)
        if any(not part for part in parts):
            raise RuntimeError("UC visibility probe returned an empty identifier")
        relation = ".".join(parts)
        _relation_parts(relation)
        relations.add(relation)
    return relations


def _target_relations(relations: set[str], *, relation_prefix: str) -> set[str]:
    catalog, schema, prefix = _relation_parts(relation_prefix)
    qualified_prefix = f"{catalog}.{schema}.{prefix}"
    return {relation for relation in relations if relation.startswith(qualified_prefix)}


def _quoted_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _bounded_rows(
    response: object,
    *,
    label: str,
    width: int,
    limit: int = 1001,
) -> list[list[object]]:
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    if len(rows) >= limit:
        raise RuntimeError(f"{label} saturated its fail-closed row limit")
    if any(len(row) != width for row in rows):
        raise RuntimeError(f"{label} returned an invalid row shape")
    return rows


def _grant_actions(response: object, *, label: str) -> set[str]:
    rows = getattr(getattr(response, "result", None), "data_array", None) or []
    actions: set[str] = set()
    for row in rows:
        if len(row) < 2:
            raise RuntimeError(f"{label} returned an invalid row shape")
        action = str(row[1] or "").strip().upper()
        if not action:
            raise RuntimeError(f"{label} returned an empty privilege")
        actions.add(action)
    if not actions:
        raise RuntimeError(f"{label} returned no effective privileges")
    return actions


def _sql_bool(value: object, *, label: str) -> bool:
    normalized = str(value or "").strip().casefold()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise RuntimeError(f"{label} returned an invalid boolean")


def _verify_exact_uc_scope(
    workspace: Any,
    *,
    warehouse_id: str,
    relation_prefix: str,
    expected_application_id: str,
) -> None:
    catalog, schema, _prefix = _relation_parts(relation_prefix)
    principal = _quoted_identifier(expected_application_id)
    catalog_grants = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"SHOW GRANTS {principal} ON CATALOG {_quoted_identifier(catalog)}",
        label="effective UC catalog grants",
    )
    catalog_actions = _grant_actions(catalog_grants, label="effective UC catalog grants")
    if "USE CATALOG" not in catalog_actions or not catalog_actions <= {
        "USE CATALOG",
        "BROWSE",
    }:
        raise RuntimeError(
            "verifier has unexpected effective catalog privileges: "
            + ", ".join(sorted(catalog_actions))
        )

    schema_grants = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            f"SHOW GRANTS {principal} ON SCHEMA "
            f"{_quoted_identifier(catalog)}.{_quoted_identifier(schema)}"
        ),
        label="effective UC schema grants",
    )
    schema_actions = _grant_actions(schema_grants, label="effective UC schema grants")
    if schema_actions != {"USE SCHEMA"}:
        raise RuntimeError(
            "verifier has unexpected effective schema privileges: "
            + ", ".join(sorted(schema_actions))
        )

    owners = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            "SELECT c.catalog_owner, s.schema_owner, "
            "is_account_group_member(c.catalog_owner), "
            "is_account_group_member(s.schema_owner) "
            "FROM system.information_schema.catalogs AS c "
            "JOIN system.information_schema.schemata AS s "
            "ON s.catalog_name = c.catalog_name "
            f"WHERE c.catalog_name = '{catalog}' AND s.schema_name = '{schema}'"
        ),
        label="effective UC ownership",
    )
    owner_rows = getattr(getattr(owners, "result", None), "data_array", None) or []
    if len(owner_rows) != 1 or len(owner_rows[0]) != 4:
        raise RuntimeError("effective UC ownership returned an invalid row shape")
    catalog_owner, schema_owner, catalog_group_member, schema_group_member = owner_rows[0]
    owned_directly = expected_application_id in {
        str(catalog_owner or "").strip(),
        str(schema_owner or "").strip(),
    }
    owned_through_group = _sql_bool(
        catalog_group_member,
        label="catalog owner membership",
    ) or _sql_bool(schema_group_member, label="schema owner membership")
    if owned_directly or owned_through_group:
        raise RuntimeError("verifier is an effective owner of the target UC catalog or schema")

    _verify_global_uc_container_scope(
        workspace,
        warehouse_id=warehouse_id,
        relation_prefix=relation_prefix,
        expected_application_id=expected_application_id,
    )


def _verify_global_uc_container_scope(
    workspace: Any,
    *,
    warehouse_id: str,
    relation_prefix: str,
    expected_application_id: str,
) -> None:
    """Reject metastore or empty-container authority hidden from table scans."""

    target_catalog, target_schema, _prefix = _relation_parts(relation_prefix)
    principal = _sql_string(expected_application_id)

    metastore_grants = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            "SELECT metastore_id, privilege_type, grantee, "
            "is_account_group_member(grantee) "
            "FROM system.information_schema.metastore_privileges "
            f"WHERE grantee = {principal} OR is_account_group_member(grantee) "
            "ORDER BY metastore_id, privilege_type, grantee LIMIT 1001"
        ),
        label="effective UC metastore grants",
    )
    metastore_rows = _bounded_rows(
        metastore_grants,
        label="effective UC metastore grants",
        width=4,
    )
    if metastore_rows:
        for metastore_id, privilege, grantee, through_group in metastore_rows:
            if not all(
                str(value or "").strip()
                for value in (metastore_id, privilege, grantee)
            ):
                raise RuntimeError("effective UC metastore grants returned an empty value")
            is_group_grant = _sql_bool(
                through_group,
                label="effective UC metastore group membership",
            )
            if str(grantee).strip() != expected_application_id and not is_group_grant:
                raise RuntimeError("effective UC metastore grants returned an unrelated grantee")
        actions = sorted({str(row[1] or "").strip().upper() for row in metastore_rows})
        raise RuntimeError(
            "verifier has unexpected effective metastore privileges: " + ", ".join(actions)
        )

    catalog_grants = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            "SELECT catalog_name, privilege_type, grantee, "
            "is_account_group_member(grantee) "
            "FROM system.information_schema.catalog_privileges "
            f"WHERE grantee = {principal} OR is_account_group_member(grantee) "
            "ORDER BY catalog_name, privilege_type, grantee LIMIT 1001"
        ),
        label="effective UC global catalog grants",
    )
    for catalog_name, privilege, grantee, through_group in _bounded_rows(
        catalog_grants,
        label="effective UC global catalog grants",
        width=4,
    ):
        catalog = str(catalog_name or "").strip()
        action = str(privilege or "").strip().upper()
        principal_name = str(grantee or "").strip()
        if not catalog or not action or not principal_name:
            raise RuntimeError("effective UC global catalog grants returned an empty value")
        is_group_grant = _sql_bool(
            through_group,
            label="effective UC catalog group membership",
        )
        if principal_name != expected_application_id and not is_group_grant:
            raise RuntimeError("effective UC global catalog grants returned an unrelated grantee")
        if catalog != target_catalog:
            raise RuntimeError(
                f"verifier has an effective privilege on non-target catalog {catalog}: {action}"
            )
        if action not in {"USE CATALOG", "BROWSE"}:
            raise RuntimeError(
                f"verifier has unexpected effective target catalog privilege: {action}"
            )

    schema_grants = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            "SELECT catalog_name, schema_name, privilege_type, grantee, "
            "is_account_group_member(grantee) "
            "FROM system.information_schema.schema_privileges "
            f"WHERE grantee = {principal} OR is_account_group_member(grantee) "
            "ORDER BY catalog_name, schema_name, privilege_type, grantee LIMIT 1001"
        ),
        label="effective UC global schema grants",
    )
    for catalog_name, schema_name, privilege, grantee, through_group in _bounded_rows(
        schema_grants,
        label="effective UC global schema grants",
        width=5,
    ):
        catalog = str(catalog_name or "").strip()
        schema = str(schema_name or "").strip()
        action = str(privilege or "").strip().upper()
        principal_name = str(grantee or "").strip()
        if not catalog or not schema or not action or not principal_name:
            raise RuntimeError("effective UC global schema grants returned an empty value")
        is_group_grant = _sql_bool(
            through_group,
            label="effective UC schema group membership",
        )
        if principal_name != expected_application_id and not is_group_grant:
            raise RuntimeError("effective UC global schema grants returned an unrelated grantee")
        if (catalog, schema) != (target_catalog, target_schema):
            raise RuntimeError(
                "verifier has an effective privilege on non-target schema "
                f"{catalog}.{schema}: {action}"
            )
        if action != "USE SCHEMA":
            raise RuntimeError(
                f"verifier has unexpected effective target schema privilege: {action}"
            )

    ownership = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            "SELECT object_kind, catalog_name, schema_name, owner_name, owner_group_member FROM ("
            "SELECT 'CATALOG' AS object_kind, catalog_name, CAST(NULL AS STRING) AS schema_name, "
            "catalog_owner AS owner_name, is_account_group_member(catalog_owner) AS owner_group_member "
            "FROM system.information_schema.catalogs WHERE catalog_name <> 'system' UNION ALL "
            "SELECT 'SCHEMA' AS object_kind, catalog_name, schema_name, "
            "schema_owner AS owner_name, is_account_group_member(schema_owner) AS owner_group_member "
            "FROM system.information_schema.schemata WHERE catalog_name <> 'system'"
            ") ORDER BY object_kind, catalog_name, schema_name LIMIT 1001"
        ),
        label="effective UC global ownership",
    )
    for object_kind, catalog_name, schema_name, owner_name, group_member in _bounded_rows(
        ownership,
        label="effective UC global ownership",
        width=5,
    ):
        kind = str(object_kind or "").strip().upper()
        catalog = str(catalog_name or "").strip()
        schema = str(schema_name or "").strip()
        owner = str(owner_name or "").strip()
        if kind not in {"CATALOG", "SCHEMA"} or not catalog or not owner:
            raise RuntimeError("effective UC global ownership returned an invalid container")
        if (kind == "SCHEMA" and not schema) or (kind == "CATALOG" and schema):
            raise RuntimeError("effective UC global ownership returned an invalid container")
        owned_through_group = _sql_bool(
            group_member,
            label="global UC owner membership",
        )
        if owner == expected_application_id or owned_through_group:
            object_name = catalog
            if kind == "SCHEMA":
                object_name += "." + schema
            raise RuntimeError(f"verifier is an effective owner of UC container {object_name}")


def _verify_exact_uc_table_scope(
    workspace: Any,
    *,
    warehouse_id: str,
    expected_application_id: str,
    target_relations: set[str],
) -> None:
    """Require SELECT—and only SELECT—on every visible target relation."""

    principal = _sql_string(expected_application_id)
    grants = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            "SELECT table_catalog, table_schema, table_name, privilege_type, grantee, "
            "is_account_group_member(grantee) "
            "FROM system.information_schema.table_privileges "
            f"WHERE grantee = {principal} OR is_account_group_member(grantee) "
            "ORDER BY table_catalog, table_schema, table_name, privilege_type, grantee "
            "LIMIT 1001"
        ),
        label="effective UC table grants",
    )
    selected_relations: set[str] = set()
    for catalog_name, schema_name, table_name, privilege, grantee, through_group in _bounded_rows(
        grants,
        label="effective UC table grants",
        width=6,
    ):
        relation = ".".join(
            str(value or "").strip()
            for value in (catalog_name, schema_name, table_name)
        )
        action = str(privilege or "").strip().upper()
        principal_name = str(grantee or "").strip()
        try:
            _relation_parts(relation)
        except ValueError as exc:
            raise RuntimeError("effective UC table grants returned an invalid relation") from exc
        if not action or not principal_name:
            raise RuntimeError("effective UC table grants returned an empty value")
        is_group_grant = _sql_bool(
            through_group,
            label="effective UC table group membership",
        )
        if principal_name != expected_application_id and not is_group_grant:
            raise RuntimeError("effective UC table grants returned an unrelated grantee")
        if relation not in target_relations:
            raise RuntimeError(
                f"verifier has an effective privilege on non-target table {relation}: {action}"
            )
        if action != "SELECT":
            raise RuntimeError(
                f"verifier has unexpected effective target table privilege: {action}"
            )
        selected_relations.add(relation)
    missing = sorted(target_relations - selected_relations)
    if missing:
        raise RuntimeError(
            "verifier is missing an explicit effective SELECT privilege on target tables: "
            + ", ".join(missing)
        )


def _verify_app_http_denial(
    workspace: Any,
    *,
    expected_application_id: str,
    app_url: str,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    verify_authenticated_app_denial(
        workspace,
        expected_application_id=expected_application_id,
        app_url=app_url,
        label="verifier Databricks App HTTP denial probe",
        http_get=http_get,
    )


def verify_boundary(
    *,
    workspace: Any,
    account: Any,
    expected_application_id: str,
    app_name: str,
    app_url: str,
    protected_service_principal_id: str,
    warehouse_id: str,
    relation_prefix: str,
    endpoint: str,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    """Run read-only positive and negative probes under verifier credentials."""
    me = workspace.current_user.me()
    authenticated_ids = {
        str(getattr(me, field, "") or "").strip()
        for field in ("application_id", "user_name")
    }
    if expected_application_id not in authenticated_ids:
        raise RuntimeError(
            "authenticated verifier identity does not match the configured application id"
        )

    _expect_denied(
        "account administrator service-principal listing probe",
        lambda: list(account.service_principals.list(count=1)),
    )
    _expect_denied(
        "workspace App permission-administration probe",
        lambda: workspace.apps.get_permissions(app_name),
    )
    assignment = workspace.metastores.current()
    metastore_id = str(getattr(assignment, "metastore_id", "") or "").strip()
    if not metastore_id:
        raise RuntimeError("current workspace metastore assignment has no immutable id")
    _expect_denied(
        "metastore administrator GET probe",
        lambda: workspace.metastores.get(metastore_id),
    )
    _verify_app_http_denial(
        workspace,
        expected_application_id=expected_application_id,
        app_url=app_url,
        http_get=http_get,
    )

    protected_id = protected_service_principal_id.strip()
    if not protected_id:
        raise RuntimeError("protected service principal immutable id is required")
    _expect_denied(
        "service-principal manager secret-listing probe",
        lambda: list(
            workspace.service_principal_secrets_proxy.list(
                protected_id,
                page_size=1,
            )
        ),
    )

    relations_response = _require_sql_success(
        workspace,
        warehouse_id=warehouse_id,
        statement=(
            "SELECT table_catalog, table_schema, table_name "
            "FROM system.information_schema.tables "
            "WHERE table_catalog <> 'system' "
            "ORDER BY table_catalog, table_schema, table_name LIMIT 1001"
        ),
        label="effective UC relation visibility",
    )
    visible_relations = _visible_data_relations(relations_response)
    targets = _target_relations(visible_relations, relation_prefix=relation_prefix)
    if not targets:
        raise RuntimeError("no target inference tables were visible to the verifier")
    unexpected_relations = sorted(visible_relations - targets)
    if unexpected_relations:
        raise RuntimeError(
            "verifier can see non-target UC relations: "
            + ", ".join(unexpected_relations[:10])
        )
    _verify_exact_uc_scope(
        workspace,
        warehouse_id=warehouse_id,
        relation_prefix=relation_prefix,
        expected_application_id=expected_application_id,
    )
    _verify_exact_uc_table_scope(
        workspace,
        warehouse_id=warehouse_id,
        expected_application_id=expected_application_id,
        target_relations=targets,
    )

    endpoint_names = {
        str(getattr(candidate, "name", "") or "").strip()
        for candidate in workspace.serving_endpoints.list()
    }
    if "" in endpoint_names:
        raise RuntimeError("workspace returned a serving endpoint without a name")
    if endpoint not in endpoint_names:
        raise RuntimeError("target serving endpoint was not visible to the verifier")
    target_endpoint = workspace.serving_endpoints.get(endpoint)
    target_endpoint_id = str(getattr(target_endpoint, "id", "") or "").strip()
    if not target_endpoint_id:
        raise RuntimeError("target serving endpoint has no immutable id")
    _expect_denied(
        "target serving endpoint permission-administration probe",
        partial(workspace.serving_endpoints.get_permissions, target_endpoint_id),
    )
    for other_endpoint in sorted(endpoint_names - {endpoint}):
        _expect_denied(
            f"non-target serving endpoint metadata {other_endpoint}",
            partial(workspace.serving_endpoints.get, other_endpoint),
        )

    warehouse_ids = {
        str(getattr(candidate, "id", "") or "").strip()
        for candidate in workspace.warehouses.list()
    }
    if "" in warehouse_ids:
        raise RuntimeError("workspace returned a SQL warehouse without an immutable id")
    if warehouse_id not in warehouse_ids:
        raise RuntimeError("target SQL warehouse was not visible to the verifier")
    if len(warehouse_ids) > 100:
        raise RuntimeError("refusing an unbounded non-target warehouse metadata probe")
    workspace.warehouses.get(warehouse_id)
    _expect_denied(
        "target SQL warehouse permission-administration probe",
        partial(workspace.warehouses.get_permissions, warehouse_id),
    )
    for other_warehouse in sorted(warehouse_ids - {warehouse_id}):
        _expect_denied(
            f"non-target warehouse metadata {other_warehouse}",
            partial(workspace.warehouses.get, other_warehouse),
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-application-id", required=True)
    parser.add_argument("--account-host", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--protected-service-principal-id", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--relation-prefix", required=True)
    parser.add_argument("--endpoint", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("verifier DATABRICKS_CLIENT_ID/SECRET are required")
    workspace = WorkspaceClient()
    account = AccountClient(
        host=args.account_host,
        account_id=args.account_id,
        client_id=client_id,
        client_secret=client_secret,
        auth_type="oauth-m2m",
    )
    verify_boundary(
        workspace=workspace,
        account=account,
        expected_application_id=args.expected_application_id,
        app_name=args.app_name,
        app_url=args.app_url,
        protected_service_principal_id=args.protected_service_principal_id,
        warehouse_id=args.warehouse_id,
        relation_prefix=args.relation_prefix,
        endpoint=args.endpoint,
    )
    print("verifier effective authorization boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
