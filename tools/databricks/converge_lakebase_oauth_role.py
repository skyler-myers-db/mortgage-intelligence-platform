"""Converge exact Lakebase OAuth roles to LOGIN-only without REPLICATION."""

from __future__ import annotations

import argparse
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql as psql

from jobs.lakebase_migration_contracts import (
    _APP_ROLE_ROUTINE_PRIVILEGES,
    _APP_ROLE_SEQUENCE_PRIVILEGES,
    _APP_ROLE_TABLE_PRIVILEGES,
)
from jobs.lakebase_migration_provider_plane import (
    _close_public_schema_boundary,
    _postflight_no_pre_boundary_sessions,
    _postflight_public_schema_boundary,
)
from jobs.lakebase_migration_schema_hooks import _postflight_event_trigger_inventory
from tools.databricks.lakebase_oauth_role_bootstrap import (
    LEGACY_API_OAUTH_PROFILE,
    SAFE_OAUTH_PROFILE,
)
from tools.databricks.lakebase_oauth_role_bootstrap import (
    assert_oauth_security_label as _assert_oauth_security_label,
)
from tools.databricks.lakebase_oauth_role_bootstrap import (
    create_login_only_role as _create_login_only_role,
)
from tools.databricks.lakebase_oauth_role_bootstrap import (
    read_profile as _read_profile,
)
from tools.databricks.lakebase_oauth_role_bootstrap_target import (
    _assert_target_role_settings,
    delete_fenced_target_role,
)
from tools.databricks.lakebase_oauth_role_profile import (
    assert_service_principal_metadata as _assert_service_principal_metadata,
)
from tools.databricks.lakebase_oauth_role_profile import (
    resolve_service_principal_id as _resolve_service_principal_id,
)
from tools.databricks.lakebase_oauth_role_recovery import (
    recover_stale_bootstrap_identities as _recover_stale_bootstrap_identities,
)

_SDK_HTTP_TIMEOUT_SECONDS = 30
_SDK_RETRY_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class RoleConvergenceResult:
    created: bool
    repaired_unsafe_role: bool


class RoleRelationshipMismatchError(RuntimeError):
    pass


def _reviewed_acl_objects(role_contract: str) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    if role_contract == "verifier":
        return {"ai_gateway_proof_ledger"}, set(), set()
    if role_contract != "app":
        raise ValueError(f"unsupported role contract {role_contract!r}")
    tables = {name for name, privileges in _APP_ROLE_TABLE_PRIVILEGES.items() if privileges}
    sequences = {name for name, privileges in _APP_ROLE_SEQUENCE_PRIVILEGES.items() if privileges}
    routines = {
        identity for identity, privileges in _APP_ROLE_ROUTINE_PRIVILEGES.items() if privileges
    }
    return tables, sequences, routines


def _assert_reviewed_acl_dependencies(
    cursor: Any,
    *,
    application_id: str,
    database_name: str,
    role_contract: str,
) -> None:
    cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", (database_name,))
    database_row = cursor.fetchone()
    if database_row is None:
        raise RuntimeError(f"Lakebase database {database_name!r} does not exist")
    database_oid = int(database_row[0])
    cursor.execute(
        """
        SELECT dependency.dbid,
               dependency.classid::regclass::text,
               dependency.objsubid,
               dependency.deptype,
               COALESCE(relation_namespace.nspname, routine_namespace.nspname, ''),
               COALESCE(
                   relation.relname,
                   routine.proname,
                   schema.nspname,
                   database.datname,
                   ''
               ),
               CASE
                   WHEN dependency.classid = 'pg_proc'::regclass
                   THEN oidvectortypes(routine.proargtypes)
                   ELSE ''
               END,
               COALESCE(relation.relkind::text, '')
        FROM pg_shdepend dependency
        LEFT JOIN pg_database database
          ON dependency.classid = 'pg_database'::regclass
         AND database.oid = dependency.objid
        LEFT JOIN pg_namespace schema
          ON dependency.dbid = (SELECT oid FROM pg_database WHERE datname = %s)
         AND dependency.classid = 'pg_namespace'::regclass
         AND schema.oid = dependency.objid
        LEFT JOIN pg_class relation
          ON dependency.dbid = (SELECT oid FROM pg_database WHERE datname = %s)
         AND dependency.classid = 'pg_class'::regclass
         AND relation.oid = dependency.objid
        LEFT JOIN pg_namespace relation_namespace ON relation_namespace.oid = relation.relnamespace
        LEFT JOIN pg_proc routine
          ON dependency.dbid = (SELECT oid FROM pg_database WHERE datname = %s)
         AND dependency.classid = 'pg_proc'::regclass
         AND routine.oid = dependency.objid
        LEFT JOIN pg_namespace routine_namespace ON routine_namespace.oid = routine.pronamespace
        WHERE dependency.refclassid = 'pg_authid'::regclass
          AND dependency.refobjid = (
              SELECT oid FROM pg_roles WHERE rolname = %s
          )
        ORDER BY 1, 2, 3, 4, 5, 6, 7
        """,
        (database_name, database_name, database_name, application_id),
    )
    dependencies = cursor.fetchall()
    allowed_tables, allowed_sequences, allowed_routines = _reviewed_acl_objects(role_contract)
    invalid: list[tuple[Any, ...]] = []
    for row in dependencies:
        dbid, class_name, objsubid, dependency_type, schema, name, arguments, relkind = row
        allowed = dependency_type == "a" and int(objsubid) == 0
        if allowed and int(dbid) == 0 and class_name == "pg_database":
            allowed = name == database_name
        elif allowed and int(dbid) == database_oid and class_name == "pg_namespace":
            allowed = name == "mip_app"
        elif allowed and int(dbid) == database_oid and class_name == "pg_class":
            allowed = schema == "mip_app" and (
                (relkind == "S" and name in allowed_sequences)
                or (relkind != "S" and name in allowed_tables)
            )
        elif allowed and int(dbid) == database_oid and class_name == "pg_proc":
            allowed = schema == "mip_app" and (name, arguments) in allowed_routines
        else:
            allowed = False
        if not allowed:
            invalid.append(tuple(row))
    if invalid:
        raise RuntimeError(
            f"Lakebase role {application_id!r} has unreviewed ACL or shared dependencies; "
            "refusing replacement"
        )


def _revoke_reviewed_acl_dependencies(
    cursor: Any,
    *,
    application_id: str,
    database_name: str,
    role_contract: str,
    allow_absent_managed_event_triggers: bool,
) -> None:
    tables, sequences, routines = _reviewed_acl_objects(role_contract)
    role = psql.Identifier(application_id)
    _postflight_event_trigger_inventory(
        cursor,
        application_id,
        principal_label="OAuth role database ACL REVOKE",
        allow_absent_managed=allow_absent_managed_event_triggers,
    )
    cursor.execute(
        psql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}").format(
            psql.Identifier(database_name),
            role,
        )
    )
    _postflight_event_trigger_inventory(
        cursor,
        application_id,
        principal_label="OAuth role schema ACL REVOKE",
        allow_absent_managed=allow_absent_managed_event_triggers,
    )
    cursor.execute(
        psql.SQL("REVOKE USAGE ON SCHEMA {} FROM {}").format(
            psql.Identifier("mip_app"),
            role,
        )
    )
    for table in sorted(tables):
        _postflight_event_trigger_inventory(
            cursor,
            application_id,
            principal_label="OAuth role table ACL REVOKE",
            allow_absent_managed=allow_absent_managed_event_triggers,
        )
        cursor.execute(
            psql.SQL("REVOKE ALL PRIVILEGES ON TABLE {}.{} FROM {}").format(
                psql.Identifier("mip_app"),
                psql.Identifier(table),
                role,
            )
        )
    for sequence in sorted(sequences):
        _postflight_event_trigger_inventory(
            cursor,
            application_id,
            principal_label="OAuth role sequence ACL REVOKE",
            allow_absent_managed=allow_absent_managed_event_triggers,
        )
        cursor.execute(
            psql.SQL("REVOKE ALL PRIVILEGES ON SEQUENCE {}.{} FROM {}").format(
                psql.Identifier("mip_app"),
                psql.Identifier(sequence),
                role,
            )
        )
    for name, arguments in sorted(routines):
        _postflight_event_trigger_inventory(
            cursor,
            application_id,
            principal_label="OAuth role function ACL REVOKE",
            allow_absent_managed=allow_absent_managed_event_triggers,
        )
        cursor.execute(
            psql.SQL("REVOKE ALL PRIVILEGES ON FUNCTION {}.{}({}) FROM {}").format(
                psql.Identifier("mip_app"),
                psql.Identifier(name),
                psql.SQL(arguments),
                role,
            )
        )
    _assert_reviewed_acl_dependencies(
        cursor,
        application_id=application_id,
        database_name=database_name,
        role_contract=role_contract,
    )


def _assert_role_is_rotatable(
    cursor: Any,
    application_id: str,
    *,
    database_name: str,
    role_contract: str,
    allowed_creator_role: str | None = None,
) -> None:
    cursor.execute(
        """
        SELECT parent.rolname,
               member.rolname,
               membership.admin_option,
               membership.inherit_option,
               membership.set_option,
               grantor.rolname
        FROM pg_auth_members membership
        JOIN pg_roles parent ON parent.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles grantor ON grantor.oid = membership.grantor
        WHERE membership.roleid = (SELECT oid FROM pg_roles WHERE rolname = %s)
           OR membership.member = (SELECT oid FROM pg_roles WHERE rolname = %s)
        ORDER BY parent.rolname, member.rolname
        """,
        (application_id, application_id),
    )
    relationships = cursor.fetchall()
    expected_relationships = (
        [(application_id, allowed_creator_role, True, False, False, "cloud_admin")]
        if allowed_creator_role is not None
        else []
    )
    if relationships != expected_relationships:
        raise RoleRelationshipMismatchError(
            f"Lakebase role {application_id!r} has unreviewed role relationships; "
            "refusing replacement"
        )

    _assert_reviewed_acl_dependencies(
        cursor,
        application_id=application_id,
        database_name=database_name,
        role_contract=role_contract,
    )


def _assert_app_is_stopped(
    client: Any,
    app_name: str | None,
    *,
    application_id: str,
    service_principal_id: str,
) -> None:
    reviewed_name = str(app_name or "").strip()
    if not reviewed_name:
        raise RuntimeError("App role mutation requires the exact Databricks App name")
    app = client.apps.get(reviewed_name)
    actual_client_id = str(getattr(app, "service_principal_client_id", "") or "").strip()
    actual_scim_id = str(getattr(app, "service_principal_id", "") or "").strip()
    if actual_client_id != application_id or actual_scim_id != service_principal_id:
        raise RuntimeError("Databricks App identity does not match the OAuth role mutation target")
    raw_state = getattr(getattr(app, "compute_status", None), "state", None)
    state = str(getattr(raw_state, "value", raw_state) or "").split(".")[-1].upper()
    if state != "STOPPED":
        raise RuntimeError(
            f"Databricks App {reviewed_name!r} must be STOPPED before its Lakebase "
            f"OAuth role can change; observed={state or 'absent'!r}"
        )


def _stop_app_for_role_mutation(
    client: Any,
    *,
    app_name: str | None,
    application_id: str,
    service_principal_id: str,
) -> None:
    reviewed_name = str(app_name or "").strip()
    if not reviewed_name:
        raise RuntimeError("App role mutation requires the exact Databricks App name")
    app = client.apps.get(reviewed_name)
    app_id = str(getattr(app, "id", "") or "").strip()
    client_id = str(getattr(app, "service_principal_client_id", "") or "").strip()
    scim_id = str(getattr(app, "service_principal_id", "") or "").strip()
    if not app_id or client_id != application_id or scim_id != service_principal_id:
        raise RuntimeError("Databricks App identity does not match the OAuth role mutation target")

    from tools.databricks.stop_app_fail_closed import stop_app_fail_closed

    outcome = stop_app_fail_closed(
        app_name=reviewed_name,
        workspace=client,
        expected_app_id=app_id,
        expected_client_id=client_id,
        expected_scim_id=scim_id,
    )
    if outcome != "stopped":
        raise RuntimeError("Databricks App disappeared before OAuth role mutation")


def _connection_kwargs(
    client: Any,
    *,
    instance_name: str,
    database_name: str,
) -> tuple[dict[str, Any], set[str]]:
    instance = client.database.get_database_instance(instance_name)
    host = str(getattr(instance, "read_write_dns", "") or "").strip()
    if not host:
        raise RuntimeError(f"Lakebase instance {instance_name!r} has no read_write_dns")
    credential = client.database.generate_database_credential(
        instance_names=[instance_name],
        request_id=str(uuid.uuid4()),
    )
    token = str(getattr(credential, "token", "") or "")
    if not token:
        raise RuntimeError("Lakebase credential response contained no token")
    current_identity = client.current_user.me()
    accepted_users = {
        str(value).strip()
        for value in (
            getattr(current_identity, "application_id", None),
            getattr(current_identity, "user_name", None),
        )
        if str(value or "").strip()
    }
    if not accepted_users:
        raise RuntimeError("current Databricks identity has no database login name")
    database_user = next(
        (
            value
            for value in (
                getattr(current_identity, "application_id", None),
                getattr(current_identity, "user_name", None),
            )
            if str(value or "").strip()
        ),
        None,
    )
    return (
        {
            "host": host,
            "port": 5432,
            "dbname": database_name,
            "user": str(database_user),
            "password": token,
            "sslmode": "require",
            "connect_timeout": 15,
            "autocommit": True,
        },
        accepted_users,
    )


def _assert_connection_identity(cursor: Any, accepted_users: set[str]) -> str:
    cursor.execute("SELECT current_user, session_user")
    row = cursor.fetchone()
    identities = tuple(str(value or "") for value in row or ())
    if (
        len(identities) != 2
        or identities[0] != identities[1]
        or not all(value in accepted_users for value in identities)
    ):
        raise RuntimeError(
            f"Lakebase authenticated as unexpected identities {identities!r}; "
            "refusing role mutation"
        )
    return identities[0]


def _wait_for_role_metadata(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
    attempts: int = 5,
) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            _assert_service_principal_metadata(
                client,
                instance_name=instance_name,
                application_id=application_id,
            )
            return
        except Exception as exc:  # noqa: BLE001 - SDK errors vary by release
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1)
    assert last_error is not None
    raise RuntimeError(
        "created Lakebase role did not converge in the control plane"
    ) from last_error


def _wait_for_profile(
    cursor: Any,
    *,
    application_id: str,
    expected: tuple[bool, ...] | None,
    attempts: int = 15,
) -> None:
    actual: tuple[bool, ...] | None = None
    for attempt in range(attempts):
        actual = _read_profile(cursor, application_id)
        if actual == expected:
            return
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(
        f"Lakebase role profile did not converge after control-plane mutation: {actual!r}"
    )


def _converge_role_locked(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    role_contract: str,
    repair_legacy_replication: bool,
    app_name: str | None = None,
    stop_app_for_mutation: bool = False,
    connect: Callable[..., Any] = psycopg.connect,
    workspace_client_factory: Callable[..., Any] | None = None,
    allow_absent_provider_schema: bool = False,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> RoleConvergenceResult:
    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        assert_bootstrap_lock_held,
    )

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

    with connect(**connection_kwargs) as connection, connection.cursor() as cursor:
        creator_role = _assert_connection_identity(cursor, accepted_users)
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
        _postflight_event_trigger_inventory(
            cursor,
            application_id,
            principal_label="OAuth role convergence preflight",
            allow_absent_managed=allow_absent_provider_schema,
        )
        _recover_stale_bootstrap_identities(
            client,
            cursor,
            account_client=account_client,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=application_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            expected_executor=creator_role,
        )
        profile = _read_profile(cursor, application_id)
        target_roles = (application_id,) if profile is not None else ()
        _postflight_event_trigger_inventory(
            cursor,
            application_id,
            principal_label="OAuth role public-schema cutover",
            allow_absent_managed=allow_absent_provider_schema,
        )
        with connection.transaction():
            _close_public_schema_boundary(
                cursor,
                target_roles,
                principal_label="OAuth role convergence quarantine",
                allow_absent_provider_schema=allow_absent_provider_schema,
                allow_empty_target_roles=profile is None,
            )
        if target_roles:
            _postflight_no_pre_boundary_sessions(
                cursor,
                target_roles,
                allow_absent_provider_schema=allow_absent_provider_schema,
            )
        if profile is not None:
            _assert_target_role_settings(cursor, application_id)
        if profile == SAFE_OAUTH_PROFILE:
            _assert_service_principal_metadata(
                client,
                instance_name=instance_name,
                application_id=application_id,
            )
            _assert_oauth_security_label(
                cursor,
                application_id=application_id,
                service_principal_id=service_principal_id,
            )
            try:
                _assert_role_is_rotatable(
                    cursor,
                    application_id,
                    database_name=database_name,
                    role_contract=role_contract,
                )
            except RoleRelationshipMismatchError:
                if not repair_legacy_replication:
                    raise
                _assert_role_is_rotatable(
                    cursor,
                    application_id,
                    database_name=database_name,
                    role_contract=role_contract,
                    allowed_creator_role=creator_role,
                )
            else:
                return RoleConvergenceResult(False, False)

        if profile is None:
            if role_contract == "app":
                if stop_app_for_mutation:
                    _stop_app_for_role_mutation(
                        client,
                        app_name=app_name,
                        application_id=application_id,
                        service_principal_id=service_principal_id,
                    )
                _assert_app_is_stopped(
                    client,
                    app_name,
                    application_id=application_id,
                    service_principal_id=service_principal_id,
                )
            _create_login_only_role(
                client,
                cursor,
                account_client=account_client,
                instance_name=instance_name,
                database_name=database_name,
                application_id=application_id,
                service_principal_id=service_principal_id,
                connect=connect,
                workspace_client_factory=workspace_client_factory,
                bootstrap_lock_cursor=bootstrap_lock_cursor,
                bootstrap_lock_key=bootstrap_lock_key,
            )
            _wait_for_role_metadata(
                client,
                instance_name=instance_name,
                application_id=application_id,
            )
            _postflight_public_schema_boundary(
                cursor,
                (application_id,),
                principal_label="created OAuth role quarantine",
                allow_legacy_public_usage=False,
                allow_absent_provider_schema=allow_absent_provider_schema,
            )
            return RoleConvergenceResult(True, False)

        if profile not in {
            SAFE_OAUTH_PROFILE,
            LEGACY_API_OAUTH_PROFILE,
        }:
            raise RuntimeError(
                f"Lakebase role {application_id!r} has unreviewed attributes {profile!r}; "
                "refusing mutation"
            )
        if profile == LEGACY_API_OAUTH_PROFILE and not repair_legacy_replication:
            raise RuntimeError(
                f"Lakebase role {application_id!r} has the legacy REPLICATION capability; "
                "rerun only at a stopped/quiesced deployment boundary with "
                "--repair-legacy-replication"
            )
        if profile == LEGACY_API_OAUTH_PROFILE:
            _assert_service_principal_metadata(
                client,
                instance_name=instance_name,
                application_id=application_id,
            )
            _assert_oauth_security_label(
                cursor,
                application_id=application_id,
                service_principal_id=service_principal_id,
            )
            _assert_role_is_rotatable(
                cursor,
                application_id,
                database_name=database_name,
                role_contract=role_contract,
            )

        if role_contract == "app":
            if stop_app_for_mutation:
                _stop_app_for_role_mutation(
                    client,
                    app_name=app_name,
                    application_id=application_id,
                    service_principal_id=service_principal_id,
                )
            _assert_app_is_stopped(
                client,
                app_name,
                application_id=application_id,
                service_principal_id=service_principal_id,
            )

        _revoke_reviewed_acl_dependencies(
            cursor,
            application_id=application_id,
            database_name=database_name,
            role_contract=role_contract,
            allow_absent_managed_event_triggers=allow_absent_provider_schema,
        )

        delete_fenced_target_role(
            client,
            cursor,
            instance_name=instance_name,
            application_id=application_id,
            service_principal_id=service_principal_id,
            allowed_creator_roles=frozenset({creator_role}),
            expected_executor=creator_role,
            expected_profile=profile,
            allow_absent_managed_event_triggers=allow_absent_provider_schema,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        _create_login_only_role(
            client,
            cursor,
            account_client=account_client,
            instance_name=instance_name,
            database_name=database_name,
            application_id=application_id,
            service_principal_id=service_principal_id,
            connect=connect,
            workspace_client_factory=workspace_client_factory,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        _wait_for_role_metadata(
            client,
            instance_name=instance_name,
            application_id=application_id,
        )
        _postflight_public_schema_boundary(
            cursor,
            (application_id,),
            principal_label="replaced OAuth role quarantine",
            allow_legacy_public_usage=False,
            allow_absent_provider_schema=allow_absent_provider_schema,
        )
        return RoleConvergenceResult(False, True)


def converge_role(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    role_contract: str,
    repair_legacy_replication: bool,
    app_name: str | None = None,
    stop_app_for_mutation: bool = False,
    connect: Callable[..., Any] = psycopg.connect,
    workspace_client_factory: Callable[..., Any] | None = None,
    allow_absent_provider_schema: bool = False,
) -> RoleConvergenceResult:
    from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
        acquire_bootstrap_lock,
        release_bootstrap_lock,
    )

    application_id = application_id.strip()
    if not application_id:
        raise ValueError("application_id is required")
    admin_kwargs, accepted_users = _connection_kwargs(
        client,
        instance_name=instance_name,
        database_name="databricks_postgres",
    )
    with connect(**admin_kwargs) as lock_connection, lock_connection.cursor() as lock_cursor:
        _assert_connection_identity(lock_cursor, accepted_users)
        lock_key = acquire_bootstrap_lock(
            lock_cursor,
            instance_name=instance_name,
            target_application_id=application_id,
        )
        try:
            return _converge_role_locked(
                client,
                account_client=account_client,
                instance_name=instance_name,
                database_name=database_name,
                application_id=application_id,
                role_contract=role_contract,
                repair_legacy_replication=repair_legacy_replication,
                app_name=app_name,
                stop_app_for_mutation=stop_app_for_mutation,
                connect=connect,
                workspace_client_factory=workspace_client_factory,
                allow_absent_provider_schema=allow_absent_provider_schema,
                bootstrap_lock_cursor=lock_cursor,
                bootstrap_lock_key=lock_key,
            )
        finally:
            release_bootstrap_lock(lock_cursor, lock_key=lock_key)


def recover_role_bootstrap(
    client: Any,
    *,
    account_client: Any,
    instance_name: str,
    database_name: str,
    application_id: str,
    connect: Callable[..., Any] = psycopg.connect,
) -> None:
    from tools.databricks.converge_lakebase_oauth_role_recovery import (
        recover_role_bootstrap as recover,
    )

    recover(
        client,
        account_client=account_client,
        instance_name=instance_name,
        database_name=database_name,
        application_id=application_id,
        connect=connect,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lakebase-instance", required=True)
    parser.add_argument("--lakebase-database", required=True)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--role-contract", required=True, choices=("app", "verifier"))
    parser.add_argument(
        "--recover-bootstrap-only",
        action="store_true",
        help="Recover an interrupted one-use creator without converging the target role.",
    )
    parser.add_argument(
        "--app-name",
        help="Exact Databricks App name; required before any app-role mutation.",
    )
    parser.add_argument(
        "--stop-app-for-mutation",
        action="store_true",
        help="Stop and identity-pin the exact App if its database role must change.",
    )
    parser.add_argument(
        "--repair-legacy-replication",
        action="store_true",
        help=(
            "Replace only the exact legacy API-created REPLICATION profile after proving "
            "zero ownership, memberships, and non-ACL dependencies."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.config import Config
    from tools.databricks.lakebase_oauth_role_account_principal import (
        account_client_from_env,
    )

    client = WorkspaceClient(
        config=Config(
            http_timeout_seconds=_SDK_HTTP_TIMEOUT_SECONDS,
            retry_timeout_seconds=_SDK_RETRY_TIMEOUT_SECONDS,
        )
    )
    account_client = account_client_from_env()
    if args.recover_bootstrap_only:
        recover_role_bootstrap(
            client,
            account_client=account_client,
            instance_name=args.lakebase_instance,
            database_name=args.lakebase_database,
            application_id=args.application_id,
        )
        print("[mip-lakebase-oauth-role] one-use bootstrap recovery passed")
        return 0

    result = converge_role(
        client,
        account_client=account_client,
        instance_name=args.lakebase_instance,
        database_name=args.lakebase_database,
        application_id=args.application_id,
        role_contract=args.role_contract,
        repair_legacy_replication=args.repair_legacy_replication,
        app_name=args.app_name,
        stop_app_for_mutation=args.stop_app_for_mutation,
    )
    state = (
        "repaired-unsafe-role"
        if result.repaired_unsafe_role
        else "created-login-only"
        if result.created
        else "already-login-only"
    )
    print(f"[mip-lakebase-oauth-role] role convergence passed state={state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
