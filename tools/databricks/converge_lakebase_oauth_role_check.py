"""Read-only check of an exact Lakebase OAuth role (``converge_lakebase_oauth_role --check``).

The check runs every read the converger uses to decide whether a role is
already exact (identity, managed event triggers, one-use bootstrap residue,
role profile, settings, control-plane identity, OAuth label, relationships,
ACL dependencies and the public-schema boundary) and reports each deviation
as a symbolic line instead of acting on it. It never stops the App, never
takes the bootstrap lock, and never creates, rotates, deletes or relabels a
role. The database session is set read-only before the first query, so a
stray write would fail at the server too. The only non-GET call is the
Lakebase login token for the deployer's own read session, which the
database requires for any connection and which changes no estate state.

Lines carry no instance, database, role or principal identifier; the App is
"the role" and bootstrap identities are counted.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

import psycopg

from jobs.lakebase_migration_provider_plane import _postflight_public_schema_boundary
from jobs.lakebase_migration_schema_hooks import _postflight_event_trigger_inventory
from tools.databricks.check_exit_codes import CHECK_DRIFT, CHECK_EXACT
from tools.databricks.converge_lakebase_oauth_role import (
    LEGACY_API_OAUTH_PROFILE,
    SAFE_OAUTH_PROFILE,
    RoleRelationshipMismatchError,
    _assert_connection_identity,
    _assert_oauth_security_label,
    _assert_role_is_rotatable,
    _assert_service_principal_metadata,
    _assert_target_role_settings,
    _connection_kwargs,
    _read_profile,
    _resolve_service_principal_id,
    _reviewed_acl_objects,
)
from tools.databricks.lakebase_oauth_role_bootstrap_principal import exact_bootstrap_principals
from tools.databricks.lakebase_oauth_role_recovery_marker import bootstrap_identity_contract

READ_ONLY_SESSION = "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"
_PREFIX = "[mip-lakebase-oauth-role]"
_LABEL = "OAuth role read-only check"


def _rotation_drift(
    cursor: Any,
    *,
    application_id: str,
    database_name: str,
    role_contract: str,
    creator_role: str,
) -> list[str]:
    try:
        _assert_role_is_rotatable(
            cursor,
            application_id,
            database_name=database_name,
            role_contract=role_contract,
        )
        return []
    except RoleRelationshipMismatchError:
        pass
    except RuntimeError:
        return ["role: unreviewed ACL or shared dependencies (convergence refuses)"]
    try:
        _assert_role_is_rotatable(
            cursor,
            application_id,
            database_name=database_name,
            role_contract=role_contract,
            allowed_creator_role=creator_role,
        )
    except RuntimeError:
        return ["role: unreviewed role relationships (convergence refuses)"]
    return [
        "role: the one-use creator membership remains "
        "(convergence rotates the role; an App role needs a stopped App)"
    ]


def check_role(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    role_contract: str,
    connect: Callable[..., Any] | None = None,
    allow_absent_provider_schema: bool = False,
) -> list[str]:
    """Return symbolic drift lines; an empty list means the role is already exact."""

    application_id = application_id.strip()
    if not application_id:
        raise ValueError("application_id is required")
    _reviewed_acl_objects(role_contract)
    service_principal_id = _resolve_service_principal_id(client, application_id)
    connection_kwargs, accepted_users = _connection_kwargs(
        client,
        instance_name=instance_name,
        database_name=database_name,
    )
    drift: list[str] = []
    open_connection = connect or psycopg.connect
    with open_connection(**connection_kwargs) as connection, connection.cursor() as cursor:
        cursor.execute(READ_ONLY_SESSION)
        creator_role = _assert_connection_identity(cursor, accepted_users)
        try:
            _postflight_event_trigger_inventory(
                cursor,
                application_id,
                principal_label=_LABEL,
                allow_absent_managed=allow_absent_provider_schema,
            )
        except RuntimeError:
            drift.append("managed event triggers differ from the reviewed inventory")
        display_name, external_id = bootstrap_identity_contract(
            instance_name=instance_name,
            database_name=database_name,
            application_id=application_id,
        )
        try:
            stale = exact_bootstrap_principals(
                client,
                display_name=display_name,
                external_id=external_id,
                account_client=account_client,
            )
        except RuntimeError:
            drift.append("one-use bootstrap identity markers are ambiguous (convergence refuses)")
        else:
            if stale:
                drift.append(
                    f"{len(stale)} stale one-use bootstrap identity found "
                    "(convergence recovers it first)"
                )
        profile = _read_profile(cursor, application_id)
        if profile is None:
            drift.append(
                "role: absent (convergence creates a LOGIN-only role; "
                "an App role needs a stopped App)"
            )
        else:
            try:
                _assert_target_role_settings(cursor, application_id)
            except RuntimeError:
                drift.append(
                    "role: settings differ from the reviewed contract (convergence refuses)"
                )
            if profile == SAFE_OAUTH_PROFILE:
                try:
                    _assert_service_principal_metadata(
                        client,
                        instance_name=instance_name,
                        application_id=application_id,
                    )
                except RuntimeError:
                    # Only the identity contract is drift. An SDK, auth or
                    # network failure propagates: the probe could not decide.
                    drift.append(
                        "role: control-plane identity is not an exact service principal "
                        "(convergence refuses)"
                    )
                try:
                    _assert_oauth_security_label(
                        cursor,
                        application_id=application_id,
                        service_principal_id=service_principal_id,
                    )
                except RuntimeError:
                    drift.append("role: OAuth security label does not bind the App identity")
                drift.extend(
                    _rotation_drift(
                        cursor,
                        application_id=application_id,
                        database_name=database_name,
                        role_contract=role_contract,
                        creator_role=creator_role,
                    )
                )
            elif profile == LEGACY_API_OAUTH_PROFILE:
                drift.append(
                    "role: legacy REPLICATION profile "
                    "(convergence rotates the role; an App role needs a stopped App)"
                )
            else:
                drift.append("role: unreviewed attributes (convergence refuses)")
        try:
            _postflight_public_schema_boundary(
                cursor,
                (application_id,) if profile is not None else (),
                principal_label=_LABEL,
                allow_legacy_public_usage=False,
                allow_absent_provider_schema=allow_absent_provider_schema,
                allow_empty_target_roles=profile is None,
            )
        except RuntimeError:
            drift.append("public-schema boundary is not closed (convergence closes it)")
    return drift


def run_check(client: Any, account_client: Any, args: argparse.Namespace) -> int:
    drift = check_role(
        client,
        account_client=account_client,
        instance_name=args.lakebase_instance,
        database_name=args.lakebase_database,
        application_id=args.application_id,
        role_contract=args.role_contract,
    )
    contract = args.role_contract
    if not drift:
        print(f"{_PREFIX} check passed role-contract={contract}: role is exact LOGIN-only")
        return CHECK_EXACT
    for line in drift:
        print(f"{_PREFIX} drift role-contract={contract}: {line}")
    return CHECK_DRIFT
