#!/usr/bin/env python3
"""Ensure the minimal managed UC namespace required by the bundle pipeline.

Databricks Asset Bundles cannot create a pipeline whose target catalog/schema
does not already exist. The full Module 0 DDL remains a bundle job because it
also creates governed tables; this helper deliberately creates only the empty
catalog and ``silver`` schema needed to break that first-install cycle.
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable
from types import SimpleNamespace

from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.errors import (
    AlreadyExists,
    NotFound,
    ResourceAlreadyExists,
    ResourceConflict,
    ResourceDoesNotExist,
)
from tools.databricks.converge_campaign_treatment_access import (
    target_group_membership_probe,
)
from tools.databricks.uc_owner_policy import (
    ApprovedOwnerPolicy,
    TargetServicePrincipal,
    account_client_from_env,
    parse_approved_owner_principals,
)
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,254}$")
_MANAGED_CATALOG_TYPE = "MANAGED_CATALOG"
_CREATE_CONFLICTS = (
    AlreadyExists,
    ResourceAlreadyExists,
    ResourceConflict,
)
_NOT_FOUND = (NotFound, ResourceDoesNotExist)


def _validate_identifier(label: str, value: str) -> str:
    normalized = value.strip()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return normalized


def _canonical(value: object) -> str:
    return str(value or "").strip().casefold()


def _escaped_filter(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _enum_name(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


def _get_or_none(getter: Callable[[str], object], name: str) -> object | None:
    try:
        return getter(name)
    except _NOT_FOUND:
        return None


def _forbidden_targets(
    workspace: WorkspaceClient, principals: set[str]
) -> tuple[TargetServicePrincipal, ...]:
    targets: list[TargetServicePrincipal] = []
    for raw_principal in sorted(principals, key=str.casefold):
        principal = raw_principal.strip()
        if not principal:
            continue
        escaped = _escaped_filter(principal)
        matches = [
            item
            for item in workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
            if _canonical(getattr(item, "application_id", "")) == _canonical(principal)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Forbidden UC owner {principal!r} did not resolve to exactly one "
                "workspace service principal"
            )
        scim_id = str(getattr(matches[0], "id", "") or "").strip()
        if not scim_id:
            raise RuntimeError(f"Forbidden UC owner {principal!r} has no immutable SCIM id")
        targets.append(TargetServicePrincipal(application_id=principal, scim_id=scim_id))
    if not targets:
        raise RuntimeError("At least one forbidden App/M2M UC owner is required")
    return tuple(targets)


def _owner_policies(
    workspace: WorkspaceClient,
    *,
    configured_principals: set[str],
    forbidden_principals: set[str],
    account_factory: Callable[[], AccountClient],
    group_membership_probe: Callable[[AccountClient, str, str, str, str], bool] | None,
) -> tuple[ApprovedOwnerPolicy, ...]:
    workspace_host = str(
        getattr(getattr(workspace, "config", None), "host", "")
        or os.environ.get("MIP_DEPLOYER_DATABRICKS_HOST", "")
        or os.environ.get("DATABRICKS_HOST", "")
    ).strip()
    membership_probe = group_membership_probe or (
        lambda account, account_sp_id, application_id, owner_group_id, owner_group: (
            target_group_membership_probe(
                account,
                account_sp_id,
                application_id,
                owner_group_id,
                owner_group,
                workspace_host=workspace_host,
            )
        )
    )
    policies = tuple(
        ApprovedOwnerPolicy(
            workspace=workspace,
            target=target,
            configured_principals=set(configured_principals),
            account_factory=account_factory,
            group_membership_probe=membership_probe,
        )
        for target in _forbidden_targets(workspace, forbidden_principals)
    )
    # Force exact SCIM resolution of the current deployer before the first
    # namespace mutation. ApprovedOwnerPolicy otherwise resolves an owner only
    # when an object already exists, which is too late on a fresh create.
    current_name = str(workspace.current_user.me().user_name or "").strip()
    for policy in policies:
        policy.assert_objects((SimpleNamespace(owner=current_name),))
    return policies


def _verify_owners(policies: tuple[ApprovedOwnerPolicy, ...], item: object) -> None:
    for policy in policies:
        policy.assert_objects((item,))


def _verify_catalog(
    catalog: object,
    *,
    expected: str,
    metastore_id: str,
    owner_policies: tuple[ApprovedOwnerPolicy, ...],
) -> None:
    if str(getattr(catalog, "name", "") or "").strip() != expected:
        raise RuntimeError("Unity Catalog API returned a different catalog name")
    full_name = str(getattr(catalog, "full_name", "") or "").strip()
    if full_name != expected:
        raise RuntimeError("Unity Catalog API returned a different catalog full name")
    if _enum_name(getattr(catalog, "catalog_type", None)) != _MANAGED_CATALOG_TYPE:
        raise RuntimeError("Pipeline namespace must use a managed Unity Catalog catalog")
    catalog_metastore = str(getattr(catalog, "metastore_id", "") or "").strip()
    if not catalog_metastore or catalog_metastore != metastore_id:
        raise RuntimeError("Pipeline catalog is outside the current workspace metastore")
    _verify_owners(owner_policies, catalog)


def _verify_schema(
    schema: object,
    *,
    catalog: str,
    expected: str,
    metastore_id: str,
    owner_policies: tuple[ApprovedOwnerPolicy, ...],
) -> None:
    full_name = f"{catalog}.{expected}"
    if str(getattr(schema, "name", "") or "").strip() != expected:
        raise RuntimeError("Unity Catalog API returned a different schema name")
    if str(getattr(schema, "catalog_name", "") or "").strip() != catalog:
        raise RuntimeError("Pipeline schema belongs to a different catalog")
    if str(getattr(schema, "full_name", "") or "").strip() != full_name:
        raise RuntimeError("Unity Catalog API returned a different schema full name")
    if _enum_name(getattr(schema, "catalog_type", None)) != _MANAGED_CATALOG_TYPE:
        raise RuntimeError("Pipeline schema must belong to a managed catalog")
    schema_metastore = str(getattr(schema, "metastore_id", "") or "").strip()
    if not schema_metastore or schema_metastore != metastore_id:
        raise RuntimeError("Pipeline schema is outside the current workspace metastore")
    _verify_owners(owner_policies, schema)


def ensure_pipeline_namespace(
    *,
    catalog: str,
    schema: str = "silver",
    approved_owner_principals: set[str] | None = None,
    forbidden_owner_principals: set[str] | None = None,
    account_factory: Callable[[], AccountClient] = account_client_from_env,
    group_membership_probe: Callable[[AccountClient, str, str, str, str], bool] | None = None,
    workspace: WorkspaceClient | None = None,
) -> tuple[bool, bool]:
    """Create and verify only the empty namespace needed by the DAB pipeline."""

    catalog_name = _validate_identifier("catalog", catalog)
    schema_name = _validate_identifier("schema", schema)
    client = workspace or deployment_workspace_client()
    current_metastore = client.metastores.current()
    metastore_id = str(getattr(current_metastore, "metastore_id", "") or "").strip()
    if not metastore_id:
        raise RuntimeError("Workspace has no authoritative current metastore id")
    owner_policies = _owner_policies(
        client,
        configured_principals=approved_owner_principals or set(),
        forbidden_principals=forbidden_owner_principals or set(),
        account_factory=account_factory,
        group_membership_probe=group_membership_probe,
    )

    catalog_object = _get_or_none(client.catalogs.get, catalog_name)
    catalog_created = False
    if catalog_object is None:
        try:
            client.catalogs.create(
                catalog_name,
                comment="Mortgage Intelligence Platform - Module 0 catalog.",
            )
        except _CREATE_CONFLICTS:
            # A concurrent creator is acceptable only if authoritative readback
            # proves the exact governed object below.
            catalog_created = False
        else:
            catalog_created = True
        catalog_object = _get_or_none(client.catalogs.get, catalog_name)
        if catalog_object is None:
            raise RuntimeError("Pipeline catalog creation could not be verified")
    _verify_catalog(
        catalog_object,
        expected=catalog_name,
        metastore_id=metastore_id,
        owner_policies=owner_policies,
    )

    full_schema_name = f"{catalog_name}.{schema_name}"
    schema_object = _get_or_none(client.schemas.get, full_schema_name)
    schema_created = False
    if schema_object is None:
        try:
            client.schemas.create(
                schema_name,
                catalog_name,
                comment="1:1 typed source-lift tables for Module 0.",
            )
        except _CREATE_CONFLICTS:
            schema_created = False
        else:
            schema_created = True
        schema_object = _get_or_none(client.schemas.get, full_schema_name)
        if schema_object is None:
            raise RuntimeError("Pipeline schema creation could not be verified")
    _verify_schema(
        schema_object,
        catalog=catalog_name,
        expected=schema_name,
        metastore_id=metastore_id,
        owner_policies=owner_policies,
    )
    return catalog_created, schema_created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--schema", default="silver")
    parser.add_argument(
        "--approved-owner-principal",
        action="append",
        default=[],
        help="Explicit trusted UC owner principal; repeat for multiple owners.",
    )
    parser.add_argument(
        "--forbidden-owner-principal",
        action="append",
        default=[],
        help="App/M2M service-principal application id forbidden from ownership.",
    )
    args = parser.parse_args()
    approved = parse_approved_owner_principals(
        os.environ.get("MIP_UC_APPROVED_OWNER_PRINCIPALS", "")
    )
    approved.update(args.approved_owner_principal)
    forbidden = {
        os.environ.get(name, "").strip()
        for name in (
            "DATABRICKS_CLIENT_ID",
            "DATABRICKS_OPERATOR2_CLIENT_ID",
            "DATABRICKS_ADMIN_CLIENT_ID",
            "DATABRICKS_VERIFIER_CLIENT_ID",
            "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        )
    }
    forbidden.update(args.forbidden_owner_principal)
    forbidden.discard("")
    catalog_created, schema_created = ensure_pipeline_namespace(
        catalog=args.catalog,
        schema=args.schema,
        approved_owner_principals=approved,
        forbidden_owner_principals=forbidden,
    )
    print(
        "Verified managed pipeline namespace "
        f"{args.catalog}.{args.schema} "
        f"(catalog={'created' if catalog_created else 'existing'}, "
        f"schema={'created' if schema_created else 'existing'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
