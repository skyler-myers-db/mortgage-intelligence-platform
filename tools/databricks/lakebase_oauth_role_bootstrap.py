"""One-use privileged bootstrap for a LOGIN-only Lakebase OAuth role."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql as psql

from jobs.lakebase_migration_contracts import (
    _MANAGED_OAUTH_ROLE_FUNCTION_ACLS,
    _MANAGED_OAUTH_ROLE_FUNCTION_OWNER_ONLY_ACL,
    _MANAGED_OAUTH_ROLE_FUNCTION_PUBLIC_ACLS,
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES,
    _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256,
)

SAFE_OAUTH_PROFILE = (False, False, False, False, False, True, True)
LEGACY_API_OAUTH_PROFILE = (False, False, False, True, False, True, True)
_BOOTSTRAP_API_PROFILE = (False, True, False, True, False, True, True)
_ROLE_FUNCTION_SOURCE_SHA256 = _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_SHA256
_ROLE_FUNCTION_SOURCE_BYTES = _MANAGED_OAUTH_ROLE_FUNCTION_SOURCE_BYTES


def read_profile(cursor: Any, application_id: str) -> tuple[bool, ...] | None:
    cursor.execute(
        """
        SELECT rolsuper,
               rolcreaterole,
               rolcreatedb,
               rolreplication,
               rolbypassrls,
               rolinherit,
               rolcanlogin
        FROM pg_roles
        WHERE rolname = %s
        """,
        (application_id,),
    )
    row = cursor.fetchone()
    return tuple(row) if row is not None else None


def assert_oauth_security_label(
    cursor: Any,
    *,
    application_id: str,
    service_principal_id: str,
) -> None:
    cursor.execute(
        """
        SELECT label.provider, label.label
        FROM pg_roles role
        LEFT JOIN pg_shseclabel label
          ON label.classoid = 'pg_authid'::regclass
         AND label.objoid = role.oid
        WHERE role.rolname = %s
        ORDER BY label.provider, label.label
        """,
        (application_id,),
    )
    expected = [("databricks_auth", f"id={service_principal_id},type=service_principal")]
    if cursor.fetchall() != expected:
        raise RuntimeError(
            f"Lakebase role {application_id!r} has an invalid OAuth security label"
        )


def _connection_kwargs(
    client: Any,
    *,
    instance_name: str,
    database_name: str,
    database_user: str,
) -> dict[str, Any]:
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
    return {
        "host": host,
        "port": 5432,
        "dbname": database_name,
        "user": database_user,
        "password": token,
        "sslmode": "require",
        "connect_timeout": 15,
        "autocommit": True,
    }


def _assert_exact_bootstrap_membership(
    cursor: Any,
    *,
    application_id: str,
    bootstrap_application_id: str,
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
    expected = [
        (
            application_id,
            bootstrap_application_id,
            True,
            False,
            False,
            "cloud_admin",
        )
    ]
    if cursor.fetchall() != expected:
        raise RuntimeError("databricks_create_role returned an unreviewed bootstrap membership")


def _assert_no_relationships(
    cursor: Any,
    application_id: str,
    *,
    attempts: int = 15,
) -> None:
    for attempt in range(attempts):
        cursor.execute(
            """
            SELECT 1
            FROM pg_auth_members membership
            WHERE membership.roleid = (SELECT oid FROM pg_roles WHERE rolname = %s)
               OR membership.member = (SELECT oid FROM pg_roles WHERE rolname = %s)
            LIMIT 1
            """,
            (application_id, application_id),
        )
        if cursor.fetchone() is None:
            return
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("Lakebase bootstrap membership survived creator deletion")


def _assert_role_function_contract(
    deployer_cursor: Any,
    *,
    allow_legacy_acl_repair: bool = True,
) -> None:
    """Pin the provider-owned C primitive before a privileged caller executes it."""

    deployer_cursor.execute(
        """
        SELECT namespace.nspname,
               routine.proname,
               oidvectortypes(routine.proargtypes),
               routine.prokind,
               pg_get_function_result(routine.oid),
               routine_owner.rolname,
               language.lanname,
               routine.provolatile,
               routine.proparallel,
               routine.proleakproof,
               routine.proisstrict,
               routine.prosecdef,
               routine.proconfig,
               routine.probin,
               extension.extname,
               extension.extversion,
               extension.extrelocatable,
               extension_namespace.nspname,
               extension_owner.rolname,
               encode(sha256(convert_to(routine.prosrc, 'UTF8')), 'hex'),
               octet_length(convert_to(routine.prosrc, 'UTF8')),
               routine.proacl
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        JOIN pg_roles routine_owner ON routine_owner.oid = routine.proowner
        JOIN pg_language language ON language.oid = routine.prolang
        JOIN pg_depend extension_membership
          ON extension_membership.classid = 'pg_proc'::regclass
         AND extension_membership.objid = routine.oid
         AND extension_membership.objsubid = 0
         AND extension_membership.deptype = 'e'
        JOIN pg_extension extension ON extension.oid = extension_membership.refobjid
        JOIN pg_namespace extension_namespace ON extension_namespace.oid = extension.extnamespace
        JOIN pg_roles extension_owner ON extension_owner.oid = extension.extowner
        CROSS JOIN pg_database database_object
        WHERE routine.oid = to_regprocedure('public.databricks_create_role(text,text)')
          AND database_object.datname = current_database()
        """
    )
    expected = (
        "public",
        "databricks_create_role",
        "text, text",
        "f",
        "text",
        "cloud_admin",
        "c",
        "v",
        "s",
        False,
        True,
        False,
        None,
        "$libdir/databricks_auth",
        "databricks_auth",
        "1.0",
        True,
        "public",
        None,
        _ROLE_FUNCTION_SOURCE_SHA256,
        _ROLE_FUNCTION_SOURCE_BYTES,
        None,
    )
    row = deployer_cursor.fetchone()
    if row is None:
        raise RuntimeError("Databricks OAuth role-creation function contract is absent")
    function_acl = None if row[-1] is None else tuple(sorted(str(item) for item in row[-1]))
    if function_acl not in _MANAGED_OAUTH_ROLE_FUNCTION_ACLS:
        raise RuntimeError("Databricks OAuth role-creation function contract drifted")
    actual = (*tuple(row[:-1]), function_acl)
    expected_writer = f"databricks_writer_{int(actual_database_oid(deployer_cursor))}"
    expected = (*expected[:18], expected_writer, *expected[19:])
    expected = (*expected[:-1], function_acl)
    if actual != expected:
        raise RuntimeError("Databricks OAuth role-creation function contract drifted")
    if function_acl == _MANAGED_OAUTH_ROLE_FUNCTION_OWNER_ONLY_ACL:
        if not allow_legacy_acl_repair:
            raise RuntimeError(
                "Databricks OAuth role-creation function PUBLIC execution repair did not converge"
            )
        deployer_cursor.execute(
            "GRANT EXECUTE ON FUNCTION public.databricks_create_role(text,text) TO PUBLIC"
        )
        _assert_role_function_contract(
            deployer_cursor,
            allow_legacy_acl_repair=False,
        )
    elif function_acl not in _MANAGED_OAUTH_ROLE_FUNCTION_PUBLIC_ACLS:
        raise RuntimeError(
            "Databricks OAuth role-creation function is not executable by bootstrap identities"
        )


def actual_database_oid(cursor: Any) -> int:
    cursor.execute("SELECT oid FROM pg_database WHERE datname = current_database()")
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("current Lakebase database identity is absent")
    return int(row[0])


def _wait_for_profile(
    cursor: Any,
    *,
    application_id: str,
    expected: tuple[bool, ...] | None,
    attempts: int = 15,
) -> None:
    actual: tuple[bool, ...] | None = None
    for attempt in range(attempts):
        actual = read_profile(cursor, application_id)
        if actual == expected:
            return
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(
        f"temporary Lakebase bootstrap role profile did not converge: {actual!r}"
    )


def create_login_only_role(
    client: Any,
    deployer_cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
    service_principal_id: str,
    connect: Callable[..., Any] = psycopg.connect,
    workspace_client_factory: Callable[..., Any] | None = None,
) -> None:
    """Create a safe role via a one-use creator, then delete the creator.

    ``databricks_create_role`` grants its caller an ADMIN-only relationship to
    the new role, and that grant is recorded as coming from ``cloud_admin``.
    PostgreSQL will not let the caller revoke it.  Deleting a purpose-created
    caller role through the control plane removes the relationship without
    retaining a reusable privileged identity.
    """
    from databricks.sdk.service.database import (
        DatabaseInstanceRole,
        DatabaseInstanceRoleAttributes,
        DatabaseInstanceRoleIdentityType,
    )
    from tools.databricks.lakebase_oauth_role_recovery import (
        _assert_bootstrap_principal_contract,
        _assert_bootstrap_role_contract,
        _bootstrap_identity_contract,
        recover_stale_bootstrap_identities,
    )

    if workspace_client_factory is None:
        from databricks.sdk import WorkspaceClient

        workspace_client_factory = WorkspaceClient

    bootstrap_name, bootstrap_external_id = _bootstrap_identity_contract(
        instance_name=instance_name,
        database_name=database_name,
        application_id=application_id,
    )
    bootstrap_sp: Any | None = None
    create_attempted = False
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        recover_stale_bootstrap_identities(
            client,
            deployer_cursor,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=application_id,
        )
        _assert_role_function_contract(deployer_cursor)
        create_attempted = True
        bootstrap_sp = client.service_principals.create(
            display_name=bootstrap_name,
            external_id=bootstrap_external_id,
            active=True,
        )
        bootstrap_application_id = str(
            getattr(bootstrap_sp, "application_id", "") or ""
        ).strip()
        bootstrap_scim_id = str(getattr(bootstrap_sp, "id", "") or "").strip()
        if not bootstrap_application_id or not bootstrap_scim_id:
            raise RuntimeError("temporary Lakebase bootstrap principal has incomplete identity")
        verified_scim_id, verified_application_id = _assert_bootstrap_principal_contract(
            client,
            bootstrap_sp,
            display_name=bootstrap_name,
            external_id=bootstrap_external_id,
        )
        if (
            verified_scim_id != bootstrap_scim_id
            or verified_application_id != bootstrap_application_id
        ):
            raise RuntimeError("temporary Lakebase bootstrap creation identity changed")
        secret_response = client.service_principal_secrets_proxy.create(bootstrap_scim_id)
        bootstrap_secret = str(getattr(secret_response, "secret", "") or "")
        if not bootstrap_secret:
            raise RuntimeError("temporary Lakebase bootstrap credential was not returned")

        client.database.create_database_instance_role(
            instance_name,
            DatabaseInstanceRole(
                name=bootstrap_application_id,
                identity_type=DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL,
                attributes=DatabaseInstanceRoleAttributes(
                    bypassrls=False,
                    createdb=False,
                    createrole=True,
                ),
            ),
        )
        _wait_for_profile(
            deployer_cursor,
            application_id=bootstrap_application_id,
            expected=_BOOTSTRAP_API_PROFILE,
        )
        if not _assert_bootstrap_role_contract(
            client,
            deployer_cursor,
            instance_name=instance_name,
            database_name=database_name,
            application_id=bootstrap_application_id,
            target_application_id=application_id,
            external_id=bootstrap_external_id,
            service_principal_id=bootstrap_scim_id,
        ):
            raise RuntimeError("temporary Lakebase bootstrap role disappeared after creation")
        deployer_cursor.execute(
            psql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                psql.Identifier(database_name),
                psql.Identifier(bootstrap_application_id),
            )
        )

        bootstrap_client = workspace_client_factory(
            host=client.config.host,
            client_id=bootstrap_application_id,
            client_secret=bootstrap_secret,
            auth_type="oauth-m2m",
        )
        me = bootstrap_client.current_user.me()
        authenticated_ids = {
            str(getattr(me, field, "") or "").strip()
            for field in ("application_id", "user_name")
        }
        if bootstrap_application_id not in authenticated_ids:
            raise RuntimeError("temporary Lakebase bootstrap authenticated as the wrong identity")
        connection_kwargs = _connection_kwargs(
            bootstrap_client,
            instance_name=instance_name,
            database_name=database_name,
            database_user=bootstrap_application_id,
        )
        with connect(**connection_kwargs) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            if cursor.fetchone() != (bootstrap_application_id,):
                raise RuntimeError("temporary Lakebase bootstrap database identity mismatch")
            cursor.execute(
                "SELECT public.databricks_create_role(%s, 'SERVICE_PRINCIPAL')",
                (application_id,),
            )
            if read_profile(cursor, application_id) != SAFE_OAUTH_PROFILE:
                raise RuntimeError("databricks_create_role returned unsafe role attributes")
            assert_oauth_security_label(
                cursor,
                application_id=application_id,
                service_principal_id=service_principal_id,
            )
            _assert_exact_bootstrap_membership(
                cursor,
                application_id=application_id,
                bootstrap_application_id=bootstrap_application_id,
            )

    except BaseException as exc:
        primary_error = exc
    finally:
        if create_attempted:
            try:
                recover_stale_bootstrap_identities(
                    client,
                    deployer_cursor,
                    instance_name=instance_name,
                    database_name=database_name,
                    target_application_id=application_id,
                )
            except Exception as exc:  # noqa: BLE001 - cleanup must retain original failure
                cleanup_errors.append(f"bootstrap recovery: {type(exc).__name__}: {exc}")

    if primary_error is not None:
        if cleanup_errors:
            raise RuntimeError(
                f"Lakebase role bootstrap failed and cleanup was incomplete: {cleanup_errors!r}"
            ) from primary_error
        raise primary_error
    if cleanup_errors:
        raise RuntimeError(f"Lakebase role bootstrap cleanup was incomplete: {cleanup_errors!r}")
    if read_profile(deployer_cursor, application_id) != SAFE_OAUTH_PROFILE:
        raise RuntimeError("safe Lakebase role did not persist after bootstrap cleanup")
    assert_oauth_security_label(
        deployer_cursor,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    _assert_no_relationships(deployer_cursor, application_id)
