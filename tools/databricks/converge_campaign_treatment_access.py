#!/usr/bin/env python3
"""Converge exact app access to the governed campaign-treatment Delta table.

Object presence and effective authority are read through authoritative Unity
Catalog APIs. Quiesce removes writes before bundle promotion; runtime restores
only exact table-scoped SELECT and MODIFY after constraints converge.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable, Sequence
from typing import Literal

from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.sql import StatementParameterListItem
from tools.databricks.ensure_campaign_treatment_table import execute_sql
from tools.databricks.m2m_access_policy import (
    assert_non_admin_service_principal,
    resolve_effective_groups,
)
from tools.databricks.uc_owner_policy import (
    ApprovedOwnerPolicy,
    TargetServicePrincipal,
    account_client_from_env,
    parse_approved_owner_principals,
)
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE = "campaign_treatment_snapshot"
_SCHEMA = "audit"
_SAFE_METASTORE_PRIVILEGES = {"USE_MARKETPLACE_ASSETS"}
Mode = Literal["quiesce", "runtime"]
_TEMPORARY_PROBE_SECRET_LIFETIME = "300s"


def _validate_identifier(label: str, value: str) -> str:
    text = value.strip()
    if not _IDENTIFIER_RE.fullmatch(text):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return text


def _quoted_identifier(value: str) -> str:
    return f"`{_validate_identifier('SQL', value)}`"


def _quoted_principal(value: str) -> str:
    principal = value.strip()
    if not principal or "`" in principal:
        raise ValueError("principal must be non-empty and contain no backticks")
    return f"`{principal}`"


def _target_group_membership_probe(
    account: AccountClient,
    account_sp_id: str,
    application_id: str,
    owner_group: str,
    *,
    warehouse_id: str,
    workspace_host: str,
    workspace_factory: Callable[..., WorkspaceClient] = WorkspaceClient,
) -> bool:
    """Evaluate effective group membership as the target App identity.

    Account SCIM cannot prove a negative membership result when Automatic
    Identity Management is enabled. Mint a bounded target-SP credential,
    evaluate Databricks' own effective-membership function, and revoke the
    credential before returning. Cleanup failure is always fatal.
    """

    host = workspace_host.strip()
    if not host:
        raise RuntimeError("Workspace host is required for target identity proof")
    principal_id = account_sp_id.strip()
    if not principal_id:
        raise RuntimeError("Account service-principal id is required for identity proof")
    secret_id = ""
    probe_error: BaseException | None = None
    target_is_member = False
    try:
        created = account.service_principal_secrets.create(
            principal_id,
            lifetime=_TEMPORARY_PROBE_SECRET_LIFETIME,
        )
        secret_id = str(getattr(created, "id", "") or "").strip()
        secret = str(getattr(created, "secret", "") or "").strip()
        if not secret_id or not secret:
            raise RuntimeError(
                "Temporary target identity credential did not return id and secret"
            )
        target_workspace = workspace_factory(
            host=host,
            client_id=application_id,
            client_secret=secret,
            auth_type="oauth-m2m",
        )
        response = execute_sql(
            target_workspace,
            warehouse_id=warehouse_id,
            statement="SELECT is_account_group_member(:owner_group)",
            parameters=[
                StatementParameterListItem(
                    name="owner_group",
                    type="STRING",
                    value=owner_group,
                )
            ],
        )
        rows = getattr(getattr(response, "result", None), "data_array", None)
        if (
            not isinstance(rows, Sequence)
            or isinstance(rows, str | bytes)
            or len(rows) != 1
            or not isinstance(rows[0], Sequence)
            or isinstance(rows[0], str | bytes)
            or len(rows[0]) != 1
        ):
            raise RuntimeError("Target identity membership proof returned invalid rows")
        raw_value = rows[0][0]
        if isinstance(raw_value, bool):
            target_is_member = raw_value
        elif str(raw_value).strip().casefold() in {"true", "false"}:
            target_is_member = str(raw_value).strip().casefold() == "true"
        else:
            raise RuntimeError("Target identity membership proof was not boolean")
    except BaseException as exc:
        probe_error = exc
    finally:
        if secret_id:
            try:
                account.service_principal_secrets.delete(principal_id, secret_id)
            except BaseException as cleanup_error:
                raise RuntimeError(
                    "Temporary target identity credential cleanup could not be proven"
                ) from cleanup_error
    if probe_error is not None:
        raise probe_error
    return target_is_member


def _get_or_none(getter: Callable[[str], object], name: str) -> object | None:
    try:
        return getter(name)
    except (NotFound, ResourceDoesNotExist):
        return None


def _object_presence(
    workspace: WorkspaceClient, *, catalog: str
) -> tuple[object | None, object | None, object | None]:
    catalog_object = _get_or_none(workspace.catalogs.get, catalog)
    if catalog_object is None:
        return None, None, None
    schema_name = f"{catalog}.{_SCHEMA}"
    schema_object = _get_or_none(workspace.schemas.get, schema_name)
    if schema_object is None:
        return catalog_object, None, None
    table_object = _get_or_none(workspace.tables.get, f"{schema_name}.{_TABLE}")
    return catalog_object, schema_object, table_object


def _identity_context(
    workspace: WorkspaceClient, principal: str
) -> TargetServicePrincipal:
    escaped = principal.replace('"', '\\"')
    matches = list(
        workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one service principal for application id {principal!r}, "
            f"found {len(matches)}"
        )
    match = matches[0]
    sp_id = str(getattr(match, "id", "") or "").strip()
    if not sp_id:
        raise RuntimeError("App service principal has no SCIM identifier")
    groups = resolve_effective_groups(workspace, sp_id=sp_id)
    assert_non_admin_service_principal(
        workspace,
        sp_id=sp_id,
        effective_groups=groups,
        identity_role="app-runtime",
    )
    return TargetServicePrincipal(application_id=principal, scim_id=sp_id)


def _privilege_name(privilege: object) -> str:
    raw = getattr(privilege, "privilege", privilege)
    value = getattr(raw, "value", raw)
    return str(value or "").split(".")[-1].strip().upper().replace(" ", "_")


def _effective_privileges(
    workspace: WorkspaceClient,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
) -> set[str]:
    privileges: set[str] = set()
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        response = workspace.grants.get_effective(
            securable_type,
            full_name,
            principal=principal,
            page_token=page_token,
            max_results=0,
        )
        for assignment in getattr(response, "privilege_assignments", None) or []:
            for privilege in getattr(assignment, "privileges", None) or []:
                name = _privilege_name(privilege)
                if not name:
                    raise RuntimeError("Effective grants API returned an empty privilege")
                privileges.add(name)
        next_token = str(getattr(response, "next_page_token", "") or "").strip()
        if not next_token:
            break
        if next_token in seen_tokens:
            raise RuntimeError("Effective grants API repeated a pagination token")
        seen_tokens.add(next_token)
        page_token = next_token
    return privileges


def _assert_effective_privileges(
    workspace: WorkspaceClient,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
    expected: set[str],
) -> None:
    actual = _effective_privileges(
        workspace,
        securable_type=securable_type,
        full_name=full_name,
        principal=principal,
    )
    if actual != expected:
        raise RuntimeError(
            f"Effective {securable_type} privileges are not exact for {full_name!r}: "
            f"expected {sorted(expected)}, observed {sorted(actual)}"
        )


def _assert_metastore_boundary(
    workspace: WorkspaceClient, *, principal: str, owner_policy: ApprovedOwnerPolicy
) -> None:
    assignment = workspace.metastores.current()
    metastore_id = str(getattr(assignment, "metastore_id", "") or "").strip()
    if not metastore_id:
        raise RuntimeError("Current workspace has no authoritative metastore identifier")
    metastore = workspace.metastores.get(metastore_id)
    owner_policy.assert_objects((metastore,))
    actual = _effective_privileges(
        workspace,
        securable_type="metastore",
        full_name=metastore_id,
        principal=principal,
    )
    forbidden = actual - _SAFE_METASTORE_PRIVILEGES
    if forbidden:
        raise RuntimeError(
            f"App service principal has forbidden metastore privileges: {sorted(forbidden)}"
        )


def _set_table_actions(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    relation: str,
    principal_sql: str,
    actions: list[str],
) -> None:
    execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM {principal_sql}",
    )
    execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"GRANT {', '.join(actions)} ON TABLE {relation} TO {principal_sql}",
    )


def _assert_existing_boundaries(
    workspace: WorkspaceClient,
    *,
    catalog: str,
    principal: str,
    owner_policy: ApprovedOwnerPolicy,
    table_actions: list[str] | None,
) -> tuple[object | None, object | None, object | None]:
    objects = _object_presence(workspace, catalog=catalog)
    catalog_object, schema_object, table_object = objects
    if catalog_object is None:
        return objects
    owner_policy.assert_objects(objects)
    _assert_effective_privileges(
        workspace,
        securable_type="catalog",
        full_name=catalog,
        principal=principal,
        expected={"USE_CATALOG"},
    )
    if schema_object is not None:
        _assert_effective_privileges(
            workspace,
            securable_type="schema",
            full_name=f"{catalog}.{_SCHEMA}",
            principal=principal,
            expected={"USE_SCHEMA"},
        )
    if table_object is not None and table_actions is not None:
        _assert_effective_privileges(
            workspace,
            securable_type="table",
            full_name=f"{catalog}.{_SCHEMA}.{_TABLE}",
            principal=principal,
            expected=set(table_actions),
        )
    return objects


def converge_campaign_treatment_access(
    *,
    warehouse_id: str,
    catalog: str,
    principal: str,
    mode: Mode,
    approved_owner_principals: set[str] | None = None,
    account_factory: Callable[[], AccountClient] | None = None,
    group_membership_probe: Callable[[AccountClient, str, str, str], bool]
    | None = None,
    workspace: WorkspaceClient | None = None,
) -> bool:
    warehouse = warehouse_id.strip()
    if not warehouse:
        raise ValueError("warehouse_id must be non-empty")
    if mode not in {"quiesce", "runtime"}:
        raise ValueError(f"Unsupported access convergence mode: {mode!r}")
    catalog_name = _validate_identifier("catalog", catalog)
    principal_name = principal.strip()
    principal_sql = _quoted_principal(principal_name)
    catalog_sql = _quoted_identifier(catalog_name)
    schema_sql = f"{catalog_sql}.{_quoted_identifier(_SCHEMA)}"
    relation = f"{schema_sql}.{_quoted_identifier(_TABLE)}"
    client = workspace or deployment_workspace_client()
    target = _identity_context(client, principal_name)
    workspace_host = str(
        getattr(getattr(client, "config", None), "host", "")
        or os.environ.get("DATABRICKS_HOST", "")
    ).strip()
    membership_probe = group_membership_probe or (
        lambda account, account_sp_id, application_id, owner_group: (
            _target_group_membership_probe(
                account,
                account_sp_id,
                application_id,
                owner_group,
                warehouse_id=warehouse,
                workspace_host=workspace_host,
            )
        )
    )
    owner_policy = ApprovedOwnerPolicy(
        workspace=client,
        target=target,
        configured_principals=approved_owner_principals or set(),
        account_factory=account_factory or account_client_from_env,
        group_membership_probe=membership_probe,
    )
    _assert_metastore_boundary(
        client, principal=principal_name, owner_policy=owner_policy
    )
    objects = _object_presence(client, catalog=catalog_name)
    catalog_object, schema_object, table_object = objects
    if catalog_object is None:
        return False

    if table_object is not None:
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM {principal_sql}",
        )
    if schema_object is not None:
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"REVOKE ALL PRIVILEGES ON SCHEMA {schema_sql} FROM {principal_sql}",
        )
    execute_sql(
        client,
        warehouse_id=warehouse,
        statement=f"REVOKE ALL PRIVILEGES ON CATALOG {catalog_sql} FROM {principal_sql}",
    )
    execute_sql(
        client,
        warehouse_id=warehouse,
        statement=f"GRANT USE CATALOG ON CATALOG {catalog_sql} TO {principal_sql}",
    )
    if schema_object is not None:
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"GRANT USE SCHEMA ON SCHEMA {schema_sql} TO {principal_sql}",
        )
    verified_objects = _assert_existing_boundaries(
        client,
        catalog=catalog_name,
        principal=principal_name,
        owner_policy=owner_policy,
        table_actions=None,
    )
    table_object = verified_objects[2]
    if table_object is None:
        if mode == "runtime":
            raise RuntimeError("Cannot grant runtime access before the treatment table exists")
        return False

    actions = ["SELECT"] if mode == "quiesce" else ["SELECT", "MODIFY"]
    try:
        _set_table_actions(
            client,
            warehouse_id=warehouse,
            relation=relation,
            principal_sql=principal_sql,
            actions=actions,
        )
        _assert_existing_boundaries(
            client,
            catalog=catalog_name,
            principal=principal_name,
            owner_policy=owner_policy,
            table_actions=actions,
        )
    except BaseException:
        if mode == "runtime":
            try:
                _set_table_actions(
                    client,
                    warehouse_id=warehouse,
                    relation=relation,
                    principal_sql=principal_sql,
                    actions=["SELECT"],
                )
                _assert_existing_boundaries(
                    client,
                    catalog=catalog_name,
                    principal=principal_name,
                    owner_policy=owner_policy,
                    table_actions=["SELECT"],
                )
            except BaseException as cleanup_error:
                raise RuntimeError(
                    "Runtime grant verification failed and compensating write "
                    "quiescence could not be proven"
                ) from cleanup_error
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--principal", required=True)
    parser.add_argument("--mode", choices=("quiesce", "runtime"), required=True)
    parser.add_argument(
        "--approved-owner-principal",
        action="append",
        default=[],
        help="Explicit trusted UC owner principal; repeat for multiple owners.",
    )
    args = parser.parse_args()
    approved_owners = parse_approved_owner_principals(
        os.environ.get("MIP_UC_APPROVED_OWNER_PRINCIPALS", "")
    )
    approved_owners.update(args.approved_owner_principal)
    existed = converge_campaign_treatment_access(
        warehouse_id=args.warehouse_id,
        catalog=args.catalog,
        principal=args.principal,
        mode=args.mode,
        approved_owner_principals=approved_owners,
    )
    if existed:
        print(f"Verified exact {args.mode} privileges on the campaign treatment table")
    else:
        print("Treatment table not yet present; verified no table write path exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
