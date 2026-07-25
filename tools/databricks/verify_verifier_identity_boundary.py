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
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests

from backend.services.capability_serving_probes import query_serving_endpoint_with_proof
from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout
from tools.databricks import identity_boundary_probes as boundary_probes
from tools.databricks.agent_runtime_access import _genie_spaces
from tools.databricks.audit_global_m2m_access import (
    assert_workspace_admin_inventory_identity,
)
from tools.databricks.authenticated_app_denial import (
    verify_authenticated_app_denial,
)
from tools.databricks.authorization_denial import is_authorization_denied
from tools.databricks.m2m_workspace_auth import (
    bind_exact_workspace_m2m_auth,
    reviewed_databricks_account_origin,
)
from tools.databricks.serving_endpoint_acl import is_platform_foundation_endpoint

_RELATION_RE = re.compile(
    r"^(?P<catalog>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<table>[A-Za-z_][A-Za-z0-9_]*)$"
)
_MAX_GLOBAL_INVENTORY = 1000
_DENIAL_PROMPT = "Confirm readiness without calling tools or including borrower data."


def _text(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


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


@dataclass(frozen=True)
class VerifierCustomerResourceDenialInventory:
    serving_endpoints: tuple[tuple[str, str, str, bool], ...]
    genie_space_ids: tuple[str, ...]


def _bounded_unique(values: list[str], *, label: str) -> tuple[str, ...]:
    if (
        len(values) > _MAX_GLOBAL_INVENTORY
        or any(not value for value in values)
        or len(values) != len(set(values))
    ):
        raise RuntimeError(f"{label} inventory is empty, duplicated, or unbounded")
    return tuple(sorted(values))


def collect_admin_customer_resource_denial_inventory(
    workspace: Any,
) -> VerifierCustomerResourceDenialInventory:
    """Capture customer serving targets, foundation metadata, and all Genie targets."""

    names = _bounded_unique(
        [_text(item, "name") for item in workspace.serving_endpoints.list()],
        label="serving endpoint",
    )
    endpoints: list[tuple[str, str, str, bool]] = []
    for name in names:
        details = workspace.serving_endpoints.get(name)
        foundation = is_platform_foundation_endpoint(details)
        endpoint_id = _text(details, "id")
        task = _text(details, "task")
        if not foundation and (_text(details, "name") != name or not endpoint_id or not task):
            raise RuntimeError(
                f"non-foundation serving endpoint {name!r} lacks identity or query protocol"
            )
        endpoints.append((name, endpoint_id, task, foundation))
    genie_ids = _bounded_unique(list(_genie_spaces(workspace)), label="Genie")
    return VerifierCustomerResourceDenialInventory(
        serving_endpoints=tuple(endpoints),
        genie_space_ids=genie_ids,
    )


def verify_customer_resource_denial_boundary(
    *,
    workspace: Any,
    inventory: VerifierCustomerResourceDenialInventory,
    expected_application_id: str,
) -> None:
    """Prove no customer-serving or Genie capability for the verifier credential.

    System foundation endpoints are metadata-classified only because their
    invocation protocol is not a customer serving securable.
    """

    me = workspace.current_user.me()
    authenticated = {
        value for value in (_text(me, "application_id"), _text(me, "user_name")) if value
    }
    if authenticated != {expected_application_id}:
        raise RuntimeError(
            "authenticated verifier identity does not match the configured application id"
        )
    for name, endpoint_id, task, foundation in inventory.serving_endpoints:
        if foundation:
            try:
                details = workspace.serving_endpoints.get(name)
            except Exception as exc:  # noqa: BLE001 - classify provider denial
                if _is_denied(exc):
                    continue
                raise RuntimeError(
                    f"foundation endpoint metadata {name} was inconclusive: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if not is_platform_foundation_endpoint(details):
                raise RuntimeError(
                    f"visible endpoint {name!r} is not a system.ai foundation endpoint"
                )
            continue
        _expect_denied(
            f"serving endpoint metadata {name}",
            partial(workspace.serving_endpoints.get, name),
        )
        _expect_denied(
            f"serving endpoint permission administration {name}",
            partial(workspace.serving_endpoints.get_permissions, endpoint_id),
        )
        _expect_denied(
            f"serving endpoint query capability {name}",
            partial(
                query_serving_endpoint_with_proof,
                workspace,
                name,
                task=task,
                prompt=_DENIAL_PROMPT,
                client_request_id=f"mip-verifier-denial-{uuid4().hex}",
                max_tokens=16,
            ),
        )
    for space_id in inventory.genie_space_ids:
        _expect_denied(
            f"Genie space metadata {space_id}",
            partial(workspace.genie.get_space, space_id),
        )
        _expect_denied(
            f"Genie permission administration {space_id}",
            partial(
                workspace.api_client.do,
                "GET",
                f"/api/2.0/permissions/genie/{quote(space_id, safe='')}",
            ),
        )


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
            if not all(str(value or "").strip() for value in (metastore_id, privilege, grantee)):
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
            str(value or "").strip() for value in (catalog_name, schema_name, table_name)
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
    admin_workspace: Any | None = None,
    app_name: str | None = None,
    allow_attested_app_401: bool = False,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    verify_authenticated_app_denial(
        workspace,
        expected_application_id=expected_application_id,
        app_url=app_url,
        label="verifier Databricks App HTTP denial probe",
        http_get=http_get,
        admin_workspace=admin_workspace,
        app_name=app_name,
        allow_attested_app_401=allow_attested_app_401,
    )


def verify_boundary(
    *,
    workspace: Any,
    account: Any,
    expected_application_id: str,
    account_id: str,
    managed_query_group_ids: tuple[str, ...],
    app_name: str,
    app_url: str,
    protected_service_principal_id: str,
    warehouse_id: str,
    relation_prefix: str,
    endpoint: str,
    preserved_endpoints: tuple[str, ...] = (),
    admin_workspace: Any | None = None,
    allow_attested_app_401: bool = False,
    http_get: Callable[..., Any] = requests.get,
) -> None:
    """Run read-only positive and negative probes under verifier credentials."""
    me = workspace.current_user.me()
    authenticated_ids = {
        str(getattr(me, field, "") or "").strip()
        for field in ("application_id", "user_name")
        if str(getattr(me, field, "") or "").strip()
    }
    if authenticated_ids != {expected_application_id}:
        raise RuntimeError(
            "authenticated verifier identity does not match the configured application id"
        )
    boundary_probes.verify_managed_query_group_administration_denied(
        workspace, account_id=account_id, group_ids=managed_query_group_ids
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
        admin_workspace=admin_workspace,
        app_name=app_name if admin_workspace is not None else None,
        allow_attested_app_401=allow_attested_app_401,
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
            "verifier can see non-target UC relations: " + ", ".join(unexpected_relations[:10])
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
    preserved = {name.strip() for name in preserved_endpoints if name.strip()}
    if len(preserved) != len(preserved_endpoints) or endpoint in preserved:
        raise ValueError("preserved serving endpoints must be distinct from the target")
    reviewed_endpoints = {endpoint, *preserved}
    missing_endpoints = reviewed_endpoints.difference(endpoint_names)
    if missing_endpoints:
        raise RuntimeError(
            "reviewed serving endpoint was not visible to the verifier: "
            + ", ".join(sorted(missing_endpoints))
        )
    for reviewed_endpoint in sorted(reviewed_endpoints):
        details = workspace.serving_endpoints.get(reviewed_endpoint)
        reviewed_endpoint_id = boundary_probes.exact_agent_responses_endpoint_id(
            details, endpoint=reviewed_endpoint
        )
        endpoint_role = "target" if reviewed_endpoint == endpoint else "preserved"
        _expect_denied(
            f"{endpoint_role} serving endpoint permission-administration probe "
            f"{reviewed_endpoint}",
            partial(workspace.serving_endpoints.get_permissions, reviewed_endpoint_id),
        )
        boundary_probes.prove_exact_gateway_responses_execution(
            workspace, endpoint=reviewed_endpoint
        )
    for other_endpoint in sorted(endpoint_names - reviewed_endpoints):
        _expect_denied(
            f"non-target serving endpoint metadata {other_endpoint}",
            partial(workspace.serving_endpoints.get, other_endpoint),
        )

    warehouse_ids = {
        str(getattr(candidate, "id", "") or "").strip() for candidate in workspace.warehouses.list()
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
    parser.add_argument("--expected-inventory-principal")
    parser.add_argument("--account-host")
    parser.add_argument("--account-id")
    parser.add_argument("--app-name")
    parser.add_argument("--app-url")
    parser.add_argument("--protected-service-principal-id")
    parser.add_argument("--warehouse-id")
    parser.add_argument("--relation-prefix")
    parser.add_argument("--endpoint")
    parser.add_argument("--preserve-endpoint", action="append", default=[])
    parser.add_argument("--customer-resource-denial", action="store_true")
    parser.add_argument("--allow-attested-app-401", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.customer_resource_denial and not args.expected_inventory_principal:
        raise SystemExit(
            "--customer-resource-denial requires --expected-inventory-principal"
        )
    required = (
        "account_host",
        "account_id",
        "app_name",
        "app_url",
        "protected_service_principal_id",
        "warehouse_id",
        "relation_prefix",
        "endpoint",
    )
    missing = [name for name in required if not getattr(args, name)]
    if missing and not args.customer_resource_denial:
        raise SystemExit(
            "positive boundary mode requires: "
            + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        )
    if args.customer_resource_denial and any(getattr(args, name) for name in required):
        raise SystemExit(
            "--customer-resource-denial rejects positive-boundary target arguments"
        )
    if args.customer_resource_denial and (
        args.preserve_endpoint or args.allow_attested_app_401
    ):
        raise SystemExit(
            "--customer-resource-denial rejects --preserve-endpoint and "
            "--allow-attested-app-401"
        )
    if not args.customer_resource_denial and not args.allow_attested_app_401:
        raise RuntimeError("verifier CLI requires the dual-authority App attestation mode")
    account_host = (
        reviewed_databricks_account_origin(
            args.account_host,
            label="verifier account host",
        )
        if not args.customer_resource_denial
        else ""
    )
    admin_workspace = WorkspaceClient()
    customer_inventory: VerifierCustomerResourceDenialInventory | None = None
    managed_query_group_ids: tuple[str, ...] = ()
    if args.customer_resource_denial:
        assert_workspace_admin_inventory_identity(
            admin_workspace,
            expected_principal=args.expected_inventory_principal,
        )
        customer_inventory = collect_admin_customer_resource_denial_inventory(admin_workspace)
    else:
        managed_query_group_ids = boundary_probes.collect_attached_managed_query_group_ids(
            admin_workspace, expected_application_id=args.expected_application_id
        )
    client_id, client_secret = bind_exact_workspace_m2m_auth(
        admin_workspace=admin_workspace,
        expected_application_id=args.expected_application_id,
        client_id_env="DATABRICKS_VERIFIER_CLIENT_ID",
        client_secret_env="DATABRICKS_VERIFIER_CLIENT_SECRET",
        label="verifier",
    )
    workspace = WorkspaceClient()
    if args.customer_resource_denial:
        assert customer_inventory is not None
        verify_customer_resource_denial_boundary(
            workspace=workspace,
            inventory=customer_inventory,
            expected_application_id=args.expected_application_id,
        )
        print(
            "verifier customer-created serving and Genie denial boundary: PASS "
            "(system foundation invocation not asserted)"
        )
        return 0
    account = AccountClient(
        host=account_host,
        account_id=args.account_id,
        client_id=client_id,
        client_secret=client_secret,
        auth_type="oauth-m2m",
    )
    verify_boundary(
        workspace=workspace,
        account=account,
        expected_application_id=args.expected_application_id,
        account_id=args.account_id,
        managed_query_group_ids=managed_query_group_ids,
        app_name=args.app_name,
        app_url=args.app_url,
        protected_service_principal_id=args.protected_service_principal_id,
        warehouse_id=args.warehouse_id,
        relation_prefix=args.relation_prefix,
        endpoint=args.endpoint,
        preserved_endpoints=tuple(args.preserve_endpoint),
        admin_workspace=admin_workspace,
        allow_attested_app_401=True,
    )
    print("verifier effective authorization boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
