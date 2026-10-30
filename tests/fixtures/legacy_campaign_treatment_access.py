"""Frozen reference: the revoke-all treatment-access converger before diff convergence.

``_set_table_actions`` and ``converge_campaign_treatment_access`` below are
byte-for-byte copies of ``tools/databricks/converge_campaign_treatment_access.py``
at commit 771f8939 (lines 188-205 and 247-389). The golden differential in
``tests/unit/test_campaign_treatment_access_diff_convergence.py`` runs this
oracle and the diff converger against the same fake grant store and requires
the same final grants. Never edit the copied bodies; the shared helpers are
imported from the live module, which the diff change left untouched.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from databricks.sdk import AccountClient, WorkspaceClient

from tools.databricks.campaign_treatment_identity_probe import target_group_membership_probe
from tools.databricks.converge_campaign_treatment_access import (
    _SCHEMA,
    _TABLE,
    Mode,
    _assert_existing_boundaries,
    _assert_metastore_boundary,
    _identity_context,
    _object_presence,
    _quoted_identifier,
    _quoted_principal,
    _validate_identifier,
    execute_sql,
)
from tools.databricks.oauth_credential_boundary import (
    held_deployment_credential_assertion,
)
from tools.databricks.uc_owner_policy import ApprovedOwnerPolicy, account_client_from_env
from tools.databricks.workspace_auth import deployment_workspace_client


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
    catalog_sql = _quoted_identifier(catalog_name)
    schema_sql = f"{catalog_sql}.{_quoted_identifier(_SCHEMA)}"
    relation = f"{schema_sql}.{_quoted_identifier(_TABLE)}"
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
