#!/usr/bin/env python3
"""Converge exact app access to the governed campaign-treatment Delta table.

Object presence and effective authority are read through authoritative Unity
Catalog APIs. Quiesce removes writes before bundle promotion; runtime restores
only exact table-scoped SELECT and MODIFY after constraints converge.

Convergence is a diff, not REVOKE ALL followed by GRANT: it reads the App
principal's direct grants on each governed securable and first revokes, deepest
securable first, only what the reviewed set does not contain; only then does it
grant what the reviewed set lacks. A reviewed privilege the App already holds
is never revoked, so treatment approvals stay live while an already-converged
boundary is re-proven, and no run opens a treatment write path the revoke-all
converger would not have opened: quiesce never opens one, and runtime opens one
only at the table grant after the parent boundary is proven. ``--check``
evaluates the same diff read-only (``campaign_treatment_access_check``).
"""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.campaign_treatment_identity_probe import target_group_membership_probe
from tools.databricks.ensure_campaign_treatment_table import execute_sql
from tools.databricks.m2m_access_policy import (
    assert_non_admin_service_principal,
    resolve_effective_groups,
)
from tools.databricks.oauth_credential_boundary import (
    held_deployment_credential_assertion,
)
from tools.databricks.uc_owner_policy import (
    ApprovedOwnerPolicy,
    TargetServicePrincipal,
    account_client_from_env,
    parse_approved_owner_principals,
)
from tools.databricks.uc_target_identity import workspace_target_identity
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_TABLE = "campaign_treatment_snapshot"
_SCHEMA = "audit"
_SAFE_METASTORE_PRIVILEGES = {"USE_MARKETPLACE_ASSETS"}
Mode = Literal["quiesce", "runtime"]
_PRIVILEGE_RE = re.compile(r"^[A-Z]+(?:_[A-Z]+)*$")
_REVIEWED_ORDER = ("USE_CATALOG", "USE_SCHEMA", "SELECT", "MODIFY")
_ALL_PRIVILEGES = "ALL_PRIVILEGES"
# Unity Catalog documents that ALL PRIVILEGES excludes these and that REVOKE ALL
# PRIVILEGES leaves them in place. The revoke-all converger therefore never
# removed them and its exact postflight failed closed; the diff converger keeps
# that refusal instead of revoking them itself.
OUTSIDE_ALL_PRIVILEGES = frozenset(
    {"MANAGE", "READ_METADATA", "EXTERNAL_USE_LOCATION", "EXTERNAL_USE_SCHEMA"}
)


@dataclass(frozen=True)
class Securable:
    """One governed securable, named for the grants API, for SQL and for reports."""

    securable_type: str
    full_name: str
    object_sql: str
    label: str

    @property
    def sql_kind(self) -> str:
        return self.securable_type.upper()


def reviewed_privileges(securable_type: str, mode: Mode) -> frozenset[str]:
    """Return the exact direct privileges the App holds on a securable in ``mode``."""

    if securable_type == "catalog":
        return frozenset({"USE_CATALOG"})
    if securable_type == "schema":
        return frozenset({"USE_SCHEMA"})
    if securable_type == "table":
        return frozenset({"SELECT"} if mode == "quiesce" else {"SELECT", "MODIFY"})
    raise ValueError(f"Unsupported treatment securable type: {securable_type!r}")


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


def _identity_context(workspace: WorkspaceClient, principal: str) -> TargetServicePrincipal:
    try:
        target = workspace_target_identity(
            workspace,
            application_id=principal,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"App service principal {principal!r} did not resolve to one exact, "
            "active workspace identity"
        ) from exc
    groups = resolve_effective_groups(workspace, sp_id=target.scim_id)
    assert_non_admin_service_principal(
        workspace,
        sp_id=target.scim_id,
        effective_groups=groups,
        identity_role="app-runtime",
    )
    return target


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


def direct_privileges(
    workspace: WorkspaceClient,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
) -> set[str]:
    """Read only ``principal``'s own direct grants on one securable.

    Assignments for any other principal are skipped, never interpreted: the
    converger has no authority over another team's grants and must not read
    them into a plan that could revoke them.
    """

    privileges: set[str] = set()
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        response = workspace.grants.get(
            securable_type,
            full_name,
            principal=principal,
            page_token=page_token,
            max_results=0,
        )
        for assignment in getattr(response, "privilege_assignments", None) or []:
            grantee = getattr(assignment, "principal", None)
            if not isinstance(grantee, str) or not grantee or grantee != grantee.strip():
                raise RuntimeError("Direct grants API returned a noncanonical principal")
            if grantee != principal:
                if grantee.casefold() == principal.casefold():
                    raise RuntimeError("Direct grants API returned a case-variant principal")
                continue
            for privilege in getattr(assignment, "privileges", None) or []:
                name = _privilege_name(privilege)
                if not _PRIVILEGE_RE.fullmatch(name):
                    raise RuntimeError("Direct grants API returned an invalid privilege")
                privileges.add(name)
        next_token = str(getattr(response, "next_page_token", "") or "").strip()
        if not next_token:
            break
        if next_token in seen_tokens:
            raise RuntimeError("Direct grants API repeated a pagination token")
        seen_tokens.add(next_token)
        page_token = next_token
    return privileges


def ordered_privileges(privileges: Iterable[str]) -> list[str]:
    def key(name: str) -> tuple[int, str]:
        rank = _REVIEWED_ORDER.index(name) if name in _REVIEWED_ORDER else len(_REVIEWED_ORDER)
        return rank, name

    return sorted(set(privileges), key=key)


def plan_securable(
    current: set[str], reviewed: frozenset[str]
) -> tuple[list[str], list[str], list[str]]:
    """Return the (grant, revoke, retained) diff that reaches ``reviewed``.

    ``retained`` holds privileges outside ALL PRIVILEGES: convergence leaves
    them in place and its exact postflight refuses, as it always has.
    """

    grant = ordered_privileges(reviewed - current)
    revoke = ordered_privileges(current - reviewed - OUTSIDE_ALL_PRIVILEGES)
    retained = ordered_privileges((current - reviewed) & OUTSIDE_ALL_PRIVILEGES)
    return grant, revoke, retained


def _privilege_sql(names: list[str]) -> str:
    for name in names:
        if not _PRIVILEGE_RE.fullmatch(name):
            raise ValueError(f"Invalid privilege name: {name!r}")
    return ", ".join(name.replace("_", " ") for name in names)


def _converge_securable(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    securable: Securable,
    principal: str,
    principal_sql: str,
    reviewed: frozenset[str],
    grant: bool = True,
) -> list[str]:
    """Revoke what ``reviewed`` does not contain, then grant only what it lacks.

    Revoking first means no intermediate state holds more than the start or the
    end. With ``grant=False`` only the revokes run. Returns the reviewed
    privileges still missing after the revokes, before any grant.
    """

    def current() -> set[str]:
        return direct_privileges(
            workspace,
            securable_type=securable.securable_type,
            full_name=securable.full_name,
            principal=principal,
        )

    def change(verb: str, names: list[str], preposition: str) -> None:
        execute_sql(
            workspace,
            warehouse_id=warehouse_id,
            statement=(
                f"{verb} {_privilege_sql(names)} ON {securable.sql_kind} "
                f"{securable.object_sql} {preposition} {principal_sql}"
            ),
        )

    held = current()
    if _ALL_PRIVILEGES in held - reviewed:
        # REVOKE ALL PRIVILEGES also removes every individual privilege it
        # implies, reviewed ones included, so it cannot follow the grants. This
        # drift state alone keeps a brief window; the revoke-all converger
        # opened the same window on every run.
        change("REVOKE", [_ALL_PRIVILEGES], "FROM")
        held = current()
    missing, extra, _retained = plan_securable(held, reviewed)
    if extra:
        change("REVOKE", extra, "FROM")
    if grant and missing:
        change("GRANT", missing, "TO")
    return missing


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


def governed_securables(catalog: str) -> tuple[Securable, Securable, Securable]:
    """Return the catalog, audit schema and treatment table securables."""

    catalog_name = _validate_identifier("catalog", catalog)
    catalog_sql = _quoted_identifier(catalog_name)
    schema_sql = f"{catalog_sql}.{_quoted_identifier(_SCHEMA)}"
    return (
        Securable("catalog", catalog_name, catalog_sql, "catalog"),
        Securable("schema", f"{catalog_name}.{_SCHEMA}", schema_sql, "audit schema"),
        Securable(
            "table",
            f"{catalog_name}.{_SCHEMA}.{_TABLE}",
            f"{schema_sql}.{_quoted_identifier(_TABLE)}",
            "treatment table",
        ),
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
    group_membership_probe: Callable[[AccountClient, str, str, str, str], bool] | None = None,
    assert_single_writer: Callable[[], None] | None = None,
    workspace: WorkspaceClient | None = None,
) -> bool:
    warehouse = warehouse_id.strip()
    if not warehouse:
        raise ValueError("warehouse_id must be non-empty")
    if mode not in {"quiesce", "runtime"}:
        raise ValueError(f"Unsupported access convergence mode: {mode!r}")
    catalog_name = _validate_identifier("catalog", catalog)
    principal_name = principal.strip()
    if principal_name != principal:
        raise ValueError("principal must be canonical")
    principal_sql = _quoted_principal(principal_name)
    catalog_securable, schema_securable, table_securable = governed_securables(catalog_name)
    client = workspace or deployment_workspace_client()
    target = _identity_context(client, principal_name)
    workspace_host = str(
        getattr(getattr(client, "config", None), "host", "")
        or os.environ.get("DATABRICKS_HOST", "")
    ).strip()
    credential_lease = assert_single_writer
    if group_membership_probe is None and credential_lease is None:
        credential_lease = held_deployment_credential_assertion(client)
    membership_probe = group_membership_probe or (
        lambda account, account_sp_id, application_id, owner_group_id, owner_group: (
            target_group_membership_probe(
                account,
                account_sp_id,
                application_id,
                owner_group_id,
                owner_group,
                expected_workspace_scim_id=target.scim_id,
                workspace_host=workspace_host,
                assert_single_writer=credential_lease,  # type: ignore[arg-type]
                deployer_workspace=client,
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
    _assert_metastore_boundary(client, principal=principal_name, owner_policy=owner_policy)
    objects = _object_presence(client, catalog=catalog_name)
    catalog_object, schema_object, table_object = objects
    if catalog_object is None:
        return False

    def converge(
        securable: Securable, reviewed: frozenset[str], *, grant: bool = True
    ) -> list[str]:
        return _converge_securable(
            client,
            warehouse_id=warehouse,
            securable=securable,
            principal=principal_name,
            principal_sql=principal_sql,
            reviewed=reviewed,
            grant=grant,
        )

    table_reviewed = reviewed_privileges("table", mode)
    actions = ordered_privileges(table_reviewed)
    present = [
        securable
        for securable, found in (
            (table_securable, table_object),
            (schema_securable, schema_object),
            (catalog_securable, catalog_object),
        )
        if found is not None
    ]
    table_stage = False
    try:
        # Phase 1 strips every unreviewed privilege, deepest securable first, as
        # the revoke-all converger did, before any grant runs. Unity Catalog
        # applies MODIFY on a catalog or schema to every table below it, so a
        # parent USE grant issued while any unreviewed MODIFY remained could
        # complete a write path that the run then depends on a later revoke to
        # close. Revoking an unreviewed privilege never removes a reviewed one.
        parent_missing = False
        for securable in present:
            missing = converge(
                securable, reviewed_privileges(securable.securable_type, mode), grant=False
            )
            parent_missing |= securable is not table_securable and bool(missing)
        if parent_missing and table_object is not None and "MODIFY" in table_reviewed:
            # A parent grant would complete a write path through the table
            # MODIFY the App already holds before the boundary is proven. Hold
            # MODIFY back until the table stage re-grants it after the proof.
            # A complete parent boundary needs no grant, so a converged runtime
            # boundary keeps MODIFY and issues no statement.
            converge(table_securable, reviewed_privileges("table", "quiesce"), grant=False)
        # Phase 2 grants only the reviewed parent privileges that are missing.
        converge(catalog_securable, reviewed_privileges("catalog", mode))
        if schema_object is not None:
            converge(schema_securable, reviewed_privileges("schema", mode))
        verified_objects = _assert_existing_boundaries(
            client,
            catalog=catalog_name,
            principal=principal_name,
            owner_policy=owner_policy,
            table_actions=None,
        )
        if verified_objects[2] is None:
            if mode == "runtime":
                raise RuntimeError("Cannot grant runtime access before the treatment table exists")
            return False
        table_stage = True
        converge(table_securable, table_reviewed)
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
                _quiesce_after_runtime_failure(
                    client,
                    catalog=catalog_name,
                    principal=principal_name,
                    owner_policy=owner_policy,
                    converge_table=lambda grant: converge(
                        table_securable, reviewed_privileges("table", "quiesce"), grant=grant
                    ),
                    table_stage=table_stage,
                )
            except BaseException as cleanup_error:
                raise RuntimeError(
                    "Runtime grant verification failed and compensating write "
                    "quiescence could not be proven"
                ) from cleanup_error
        raise
    return True


def _quiesce_after_runtime_failure(
    client: WorkspaceClient,
    *,
    catalog: str,
    principal: str,
    owner_policy: ApprovedOwnerPolicy,
    converge_table: Callable[[bool], object],
    table_stage: bool,
) -> None:
    """Leave no treatment write path after a failed runtime convergence.

    A failure at the table stage restores and proves the exact quiesced boundary,
    as before. An earlier failure only revokes table authority and proves no
    write path remains: the revoke-all converger had removed it by then, and an
    unproven parent boundary must not gain a grant.
    """

    if table_stage:
        converge_table(True)
        _assert_existing_boundaries(
            client,
            catalog=catalog,
            principal=principal,
            owner_policy=owner_policy,
            table_actions=["SELECT"],
        )
        return
    if _object_presence(client, catalog=catalog)[2] is None:
        return
    converge_table(False)
    actual = _effective_privileges(
        client,
        securable_type="table",
        full_name=f"{catalog}.{_SCHEMA}.{_TABLE}",
        principal=principal,
    )
    if not actual <= {"SELECT"}:
        raise RuntimeError(
            f"Effective table privileges retain a treatment write path: {sorted(actual)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id")
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--principal", required=True)
    parser.add_argument("--mode", choices=("quiesce", "runtime"), required=True)
    parser.add_argument(
        "--approved-owner-principal",
        action="append",
        default=[],
        help="Explicit trusted UC owner principal; repeat for multiple owners.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only: print the symbolic grant diff for --mode and exit 3 on drift.",
    )
    args = parser.parse_args(argv)
    if args.check:
        from tools.databricks.campaign_treatment_access_check import run_check

        return run_check(catalog=args.catalog, principal=args.principal, mode=args.mode)
    if not args.warehouse_id:
        parser.error("--warehouse-id is required unless --check is given")
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
