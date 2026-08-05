"""Target-bound transient wrapper for Lakebase OAuth role creation."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from typing import Any

from psycopg import sql as psql

from jobs.lakebase_migration_schema_hooks import _postflight_event_trigger_inventory
from tools.databricks.lakebase_oauth_role_bootstrap_wrapper_contract import (
    _WRAPPER_FUNCTION,
    _WRAPPER_OWNER,
    WrapperFunctionFingerprint,
)
from tools.databricks.lakebase_oauth_role_bootstrap_wrapper_contract import (
    wrapper_function_contract as _wrapper_function_contract,
)


def _assert_wrapper_lock(
    lock_cursor: Any | None,
    lock_key: Any | None,
    *,
    allow_unlocked_recovery_for_tests: bool,
) -> None:
    if lock_cursor is not None and lock_key is not None:
        from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
            assert_bootstrap_lock_held,
        )

        assert_bootstrap_lock_held(lock_cursor, lock_key=lock_key)
    elif not allow_unlocked_recovery_for_tests:
        raise RuntimeError("temporary Lakebase wrapper mutation lacks canonical lock")


def _event_trigger_preflight(
    cursor: Any,
    *,
    principal_label: str,
    allow_absent_managed_event_triggers: bool,
) -> None:
    _postflight_event_trigger_inventory(
        cursor,
        "",
        principal_label=principal_label,
        allow_absent_managed=allow_absent_managed_event_triggers,
    )


def wrapper_schema_name(
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{instance_name}\0{database_name}\0{target_application_id}".encode()
    ).hexdigest()
    return f"mip_lb_bootstrap_{digest[:24]}"


def _wrapper_body(
    target_application_id: str,
    bootstrap_application_id: str,
) -> str:
    target = psql.Literal(target_application_id).as_string()
    bootstrap = psql.Literal(bootstrap_application_id).as_string()
    return (
        "BEGIN ATOMIC\n"
        " RETURN ( SELECT public.databricks_create_role(\n"
        f"                   {target}::pg_catalog.text,\n"
        "                   'SERVICE_PRINCIPAL'::pg_catalog.text)\n"
        f"          WHERE ((CURRENT_USER = {bootstrap}::pg_catalog.name)\n"
        f"             AND (SESSION_USER = {bootstrap}::pg_catalog.name)));\n"
        "END"
    )


def _schema_acl_contract(
    cursor: Any,
    *,
    schema_name: str,
    bootstrap_application_id: str | None,
    allow_provider_grants: bool,
    expected_executor: str,
) -> tuple[str, int, int]:
    cursor.execute(
        """
        SELECT namespace.oid,
               owner.rolname,
               database_object.oid
        FROM pg_namespace namespace
        JOIN pg_roles owner ON owner.oid = namespace.nspowner
        CROSS JOIN pg_database database_object
        WHERE namespace.nspname = %s
          AND database_object.datname = current_database()
        """,
        (schema_name,),
    )
    rows = list(cursor.fetchall())
    if len(rows) != 1:
        raise RuntimeError("temporary Lakebase bootstrap wrapper schema inventory drifted")
    schema_oid, owner, database_oid = int(rows[0][0]), str(rows[0][1]), int(rows[0][2])
    cursor.execute("SELECT current_user, session_user")
    identity_row = cursor.fetchone()
    identities = tuple(str(value or "") for value in identity_row or ())
    if (
        len(identities) != 2
        or identities[1] != expected_executor
        or identities[0] not in {expected_executor, _WRAPPER_OWNER}
        or owner != _WRAPPER_OWNER
    ):
        raise RuntimeError("temporary Lakebase bootstrap wrapper schema owner drifted")

    cursor.execute(
        """
        SELECT CASE WHEN acl.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END,
               acl.privilege_type,
               acl.is_grantable,
               grantor.rolname
        FROM pg_namespace namespace
        CROSS JOIN LATERAL aclexplode(namespace.nspacl) acl
        LEFT JOIN pg_roles grantee ON grantee.oid = acl.grantee
        JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE namespace.nspname = %s
        ORDER BY 1, 2, 3, 4
        """,
        (schema_name,),
    )
    actual = {tuple(row) for row in cursor.fetchall()}
    owner_acl = {
        (owner, "CREATE", False, owner),
        (owner, "USAGE", False, owner),
    }
    expected = set(owner_acl)
    if allow_provider_grants:
        expected.update(
            {
                ("databricks_superuser", "CREATE", True, owner),
                ("databricks_superuser", "USAGE", True, owner),
                (
                    f"databricks_writer_{database_oid}",
                    "CREATE",
                    False,
                    "databricks_superuser",
                ),
                (
                    f"databricks_writer_{database_oid}",
                    "USAGE",
                    False,
                    "databricks_superuser",
                ),
                ("databricks_gateway", "USAGE", False, owner),
                (
                    f"databricks_reader_{database_oid}",
                    "USAGE",
                    False,
                    "databricks_superuser",
                ),
            }
        )
    if bootstrap_application_id is not None:
        expected.add((bootstrap_application_id, "USAGE", False, owner))
    if actual != expected:
        raise RuntimeError("temporary Lakebase bootstrap wrapper schema ACL drifted")
    return owner, schema_oid, database_oid


def _schema_object_contract(
    cursor: Any,
    *,
    schema_oid: int,
    function_oid: int | None,
) -> None:
    cursor.execute(
        """
        SELECT dependency.classid::regclass::text,
               dependency.objid,
               dependency.deptype
        FROM pg_depend dependency
        WHERE dependency.refclassid = 'pg_namespace'::regclass
          AND dependency.refobjid = %s
          AND dependency.deptype = 'n'
        ORDER BY 1, 2, 3
        """,
        (schema_oid,),
    )
    expected = [] if function_oid is None else [("pg_proc", function_oid, "n")]
    if list(cursor.fetchall()) != expected:
        raise RuntimeError("temporary Lakebase bootstrap wrapper object dependency drifted")


def _role_acl_contract(
    cursor: Any,
    *,
    database_oid: int,
    schema_oid: int,
    function_oid: int,
    bootstrap_application_id: str,
    expected_privileges: frozenset[str] | None = None,
) -> frozenset[str]:
    cursor.execute(
        """
        SELECT dependency.dbid,
               dependency.classid::regclass::text,
               dependency.objid,
               dependency.objsubid,
               dependency.deptype
        FROM pg_shdepend dependency
        WHERE dependency.refclassid = 'pg_authid'::regclass
          AND dependency.refobjid = (
              SELECT oid FROM pg_roles WHERE rolname = %s
          )
        ORDER BY 1, 2, 3, 4, 5
        """,
        (bootstrap_application_id,),
    )
    actual = {tuple(row) for row in cursor.fetchall()}
    allowed = {
        "USAGE": (database_oid, "pg_namespace", schema_oid, 0, "a"),
        "EXECUTE": (database_oid, "pg_proc", function_oid, 0, "a"),
    }
    if not actual <= set(allowed.values()):
        raise RuntimeError("temporary Lakebase bootstrap wrapper role dependency drifted")
    privileges = frozenset(
        privilege for privilege, dependency in allowed.items() if dependency in actual
    )
    if len(actual) != len(privileges):
        raise RuntimeError("temporary Lakebase bootstrap wrapper role dependency drifted")
    if expected_privileges is not None and privileges != expected_privileges:
        raise RuntimeError("temporary Lakebase bootstrap wrapper role ACL state drifted")
    return privileges


def assert_wrapper_contract(
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
    expected_executor: str,
    expected_privileges: frozenset[str] | None = None,
    expected_function_fingerprint: WrapperFunctionFingerprint | None = None,
) -> tuple[frozenset[str], WrapperFunctionFingerprint]:
    schema_name = wrapper_schema_name(
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
    )
    owner, schema_oid, database_oid = _schema_acl_contract(
        cursor,
        schema_name=schema_name,
        bootstrap_application_id=(
            bootstrap_application_id
            if expected_privileges is None or "USAGE" in expected_privileges
            else None
        ),
        allow_provider_grants=False,
        expected_executor=expected_executor,
    )
    function_oid, function_owner, fingerprint = _wrapper_function_contract(
        cursor,
        schema_name=schema_name,
        target_application_id=target_application_id,
        bootstrap_application_id=bootstrap_application_id,
        allow_bootstrap_execute=(expected_privileges is None or "EXECUTE" in expected_privileges),
        expected_fingerprint=expected_function_fingerprint,
    )
    if owner != function_owner:
        raise RuntimeError("temporary Lakebase bootstrap wrapper ownership drifted")
    _schema_object_contract(cursor, schema_oid=schema_oid, function_oid=function_oid)
    return (
        _role_acl_contract(
            cursor,
            database_oid=database_oid,
            schema_oid=schema_oid,
            function_oid=function_oid,
            bootstrap_application_id=bootstrap_application_id,
            expected_privileges=expected_privileges,
        ),
        fingerprint,
    )


def _publish_wrapper(
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> tuple[str, WrapperFunctionFingerprint]:
    schema_name = wrapper_schema_name(
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
    )
    cursor.execute("SELECT to_regnamespace(%s)", (schema_name,))
    if cursor.fetchone() != (None,):
        raise RuntimeError("temporary Lakebase bootstrap wrapper already exists")
    cursor.execute(
        """
        SELECT current_user,
               session_user,
               has_database_privilege(current_user, current_database(), 'CREATE'),
               pg_has_role(current_user, 'pg_database_owner', 'SET')
        """
    )
    executor_row = cursor.fetchone()
    if executor_row != (expected_executor, expected_executor, True, True):
        raise RuntimeError("temporary Lakebase wrapper creation executor contract drifted")

    _event_trigger_preflight(
        cursor,
        principal_label="bootstrap wrapper CREATE SCHEMA",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    _assert_wrapper_lock(
        bootstrap_lock_cursor,
        bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=False,
    )
    cursor.execute(
        psql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
            psql.Identifier(schema_name),
            psql.Identifier(_WRAPPER_OWNER),
        )
    )
    # pg_database_owner is a pseudo-role: the real database owner may SET it,
    # but the pseudo-role itself does not inherit the owner's database CREATE
    # privilege. Create the schema as the exact deployer with explicit
    # AUTHORIZATION, then assume the durable owner for every remaining
    # owner-scoped ACL and function mutation in the same transaction.
    cursor.execute(psql.SQL("SET LOCAL ROLE {}").format(psql.Identifier(_WRAPPER_OWNER)))
    owner, schema_oid, database_oid = _schema_acl_contract(
        cursor,
        schema_name=schema_name,
        bootstrap_application_id=None,
        allow_provider_grants=True,
        expected_executor=expected_executor,
    )
    _schema_object_contract(cursor, schema_oid=schema_oid, function_oid=None)

    provider_grant_roots = (
        "databricks_superuser",
        "databricks_gateway",
    )
    _event_trigger_preflight(
        cursor,
        principal_label="bootstrap wrapper provider ACL REVOKE",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    _assert_wrapper_lock(
        bootstrap_lock_cursor,
        bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=False,
    )
    cursor.execute(
        psql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {} CASCADE").format(
            psql.Identifier(schema_name),
            psql.SQL(", ").join(psql.Identifier(role) for role in provider_grant_roots),
        )
    )
    _schema_acl_contract(
        cursor,
        schema_name=schema_name,
        bootstrap_application_id=None,
        allow_provider_grants=False,
        expected_executor=expected_executor,
    )

    body = _wrapper_body(target_application_id, bootstrap_application_id)
    _event_trigger_preflight(
        cursor,
        principal_label="bootstrap wrapper CREATE FUNCTION",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    _assert_wrapper_lock(
        bootstrap_lock_cursor,
        bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=False,
    )
    cursor.execute(
        psql.SQL(
            """
            CREATE FUNCTION {}.{}() RETURNS text
            LANGUAGE sql VOLATILE PARALLEL UNSAFE SECURITY INVOKER
            SET search_path = pg_catalog
            SET createrole_self_grant = ''
            {}
            """
        ).format(
            psql.Identifier(schema_name),
            psql.Identifier(_WRAPPER_FUNCTION),
            psql.SQL(body),
        )
    )
    _event_trigger_preflight(
        cursor,
        principal_label="bootstrap wrapper PUBLIC EXECUTE REVOKE",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    _assert_wrapper_lock(
        bootstrap_lock_cursor,
        bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=False,
    )
    cursor.execute(
        psql.SQL("REVOKE ALL PRIVILEGES ON FUNCTION {}.{}() FROM PUBLIC").format(
            psql.Identifier(schema_name),
            psql.Identifier(_WRAPPER_FUNCTION),
        )
    )
    function_oid, function_owner, _fingerprint = _wrapper_function_contract(
        cursor,
        schema_name=schema_name,
        target_application_id=target_application_id,
        bootstrap_application_id=bootstrap_application_id,
        allow_bootstrap_execute=False,
    )
    if owner != function_owner:
        raise RuntimeError("temporary Lakebase bootstrap wrapper ownership drifted")
    _schema_object_contract(cursor, schema_oid=schema_oid, function_oid=function_oid)

    _event_trigger_preflight(
        cursor,
        principal_label="bootstrap wrapper schema GRANT",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    _assert_wrapper_lock(
        bootstrap_lock_cursor,
        bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=False,
    )
    cursor.execute(
        psql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            psql.Identifier(schema_name),
            psql.Identifier(bootstrap_application_id),
        )
    )
    _event_trigger_preflight(
        cursor,
        principal_label="bootstrap wrapper function GRANT",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    _assert_wrapper_lock(
        bootstrap_lock_cursor,
        bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=False,
    )
    cursor.execute(
        psql.SQL("GRANT EXECUTE ON FUNCTION {}.{}() TO {}").format(
            psql.Identifier(schema_name),
            psql.Identifier(_WRAPPER_FUNCTION),
            psql.Identifier(bootstrap_application_id),
        )
    )
    _privileges, fingerprint = assert_wrapper_contract(
        cursor,
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
        bootstrap_application_id=bootstrap_application_id,
        expected_executor=expected_executor,
        expected_privileges=frozenset({"USAGE", "EXECUTE"}),
    )
    return schema_name, fingerprint


def create_wrapper(
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> tuple[str, WrapperFunctionFingerprint]:
    """Publish the closed wrapper as one all-or-nothing transaction."""

    cursor.execute("BEGIN")
    try:
        schema_name, fingerprint = _publish_wrapper(
            cursor,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=target_application_id,
            bootstrap_application_id=bootstrap_application_id,
            expected_executor=expected_executor,
            allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        _assert_wrapper_lock(
            bootstrap_lock_cursor,
            bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=False,
        )
        cursor.execute("COMMIT")
        return schema_name, fingerprint
    except BaseException:
        with suppress(Exception):
            cursor.execute("ROLLBACK")
        raise


def _teardown_wrapper(
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str | None,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
) -> None:
    schema_name = wrapper_schema_name(
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
    )
    cursor.execute("SELECT to_regnamespace(%s)", (schema_name,))
    if cursor.fetchone() == (None,):
        return

    # Accept only the exact interruption states emitted by create_wrapper.
    schema_usage = False
    provider_grants = False
    schema_contract: tuple[str, int, int] | None = None
    candidates = [(True, None), (False, None)]
    if bootstrap_application_id is not None:
        candidates.append((False, bootstrap_application_id))
    for candidate_provider, candidate_bootstrap in candidates:
        try:
            schema_contract = _schema_acl_contract(
                cursor,
                schema_name=schema_name,
                bootstrap_application_id=candidate_bootstrap,
                allow_provider_grants=candidate_provider,
                expected_executor=expected_executor,
            )
        except RuntimeError:
            continue
        provider_grants = candidate_provider
        schema_usage = candidate_bootstrap is not None
        break
    if schema_contract is None:
        raise RuntimeError("temporary Lakebase bootstrap wrapper schema state drifted")
    owner, schema_oid, database_oid = schema_contract
    if provider_grants:
        provider_roles = (
            "databricks_superuser",
            f"databricks_writer_{database_oid}",
            "databricks_gateway",
            f"databricks_reader_{database_oid}",
        )
        _event_trigger_preflight(
            cursor,
            principal_label="bootstrap wrapper recovery provider ACL REVOKE",
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )
        _assert_wrapper_lock(
            bootstrap_lock_cursor,
            bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
        cursor.execute(
            psql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(
                psql.Identifier(schema_name),
                psql.SQL(", ").join(psql.Identifier(role) for role in provider_roles),
            )
        )

    cursor.execute(
        """
        SELECT routine.oid
        FROM pg_proc routine
        JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
        WHERE namespace.nspname = %s
        ORDER BY routine.proname, oidvectortypes(routine.proargtypes)
        """,
        (schema_name,),
    )
    function_rows = list(cursor.fetchall())
    if provider_grants and function_rows:
        raise RuntimeError(
            "temporary Lakebase bootstrap wrapper provider grants survived function creation"
        )
    if schema_usage and not function_rows:
        raise RuntimeError(
            "temporary Lakebase bootstrap wrapper schema grant has no bound function"
        )
    function_oid: int | None = None
    privileges = frozenset({"USAGE"}) if schema_usage else frozenset()
    if function_rows:
        function_execute = False
        try:
            function_oid, function_owner, _fingerprint = _wrapper_function_contract(
                cursor,
                schema_name=schema_name,
                target_application_id=target_application_id,
                bootstrap_application_id=str(bootstrap_application_id),
                allow_bootstrap_execute=True,
            )
            function_execute = bootstrap_application_id is not None
        except RuntimeError:
            function_oid, function_owner, _fingerprint = _wrapper_function_contract(
                cursor,
                schema_name=schema_name,
                target_application_id=target_application_id,
                bootstrap_application_id=str(bootstrap_application_id),
                allow_bootstrap_execute=False,
            )
        if owner != function_owner:
            raise RuntimeError("temporary Lakebase bootstrap wrapper ownership drifted")
        if bootstrap_application_id is not None:
            dependency_privileges = _role_acl_contract(
                cursor,
                database_oid=database_oid,
                schema_oid=schema_oid,
                function_oid=function_oid,
                bootstrap_application_id=bootstrap_application_id,
            )
            expected = set(privileges)
            if function_execute:
                expected.add("EXECUTE")
            privileges = frozenset(expected)
            if dependency_privileges != privileges:
                raise RuntimeError(
                    "temporary Lakebase bootstrap wrapper ACL/dependency state drifted"
                )
        _schema_object_contract(cursor, schema_oid=schema_oid, function_oid=function_oid)
    else:
        _schema_object_contract(cursor, schema_oid=schema_oid, function_oid=None)

    if "EXECUTE" in privileges:
        _event_trigger_preflight(
            cursor,
            principal_label="bootstrap wrapper function REVOKE",
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )
        _assert_wrapper_lock(
            bootstrap_lock_cursor,
            bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
        cursor.execute(
            psql.SQL("REVOKE EXECUTE ON FUNCTION {}.{}() FROM {}").format(
                psql.Identifier(schema_name),
                psql.Identifier(_WRAPPER_FUNCTION),
                psql.Identifier(str(bootstrap_application_id)),
            )
        )
    if "USAGE" in privileges:
        _event_trigger_preflight(
            cursor,
            principal_label="bootstrap wrapper schema REVOKE",
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )
        _assert_wrapper_lock(
            bootstrap_lock_cursor,
            bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
        cursor.execute(
            psql.SQL("REVOKE USAGE ON SCHEMA {} FROM {}").format(
                psql.Identifier(schema_name),
                psql.Identifier(str(bootstrap_application_id)),
            )
        )

    if function_oid is not None:
        _wrapper_function_contract(
            cursor,
            schema_name=schema_name,
            target_application_id=target_application_id,
            bootstrap_application_id=str(bootstrap_application_id),
            allow_bootstrap_execute=False,
        )
        if bootstrap_application_id is not None:
            _role_acl_contract(
                cursor,
                database_oid=database_oid,
                schema_oid=schema_oid,
                function_oid=function_oid,
                bootstrap_application_id=bootstrap_application_id,
                expected_privileges=frozenset(),
            )
        _event_trigger_preflight(
            cursor,
            principal_label="bootstrap wrapper DROP FUNCTION",
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )
        _assert_wrapper_lock(
            bootstrap_lock_cursor,
            bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
        cursor.execute(
            psql.SQL("DROP FUNCTION {}.{}() RESTRICT").format(
                psql.Identifier(schema_name),
                psql.Identifier(_WRAPPER_FUNCTION),
            )
        )
    _schema_object_contract(cursor, schema_oid=schema_oid, function_oid=None)
    _schema_acl_contract(
        cursor,
        schema_name=schema_name,
        bootstrap_application_id=None,
        allow_provider_grants=False,
        expected_executor=expected_executor,
    )
    _event_trigger_preflight(
        cursor,
        principal_label="bootstrap wrapper DROP SCHEMA",
        allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
    )
    _assert_wrapper_lock(
        bootstrap_lock_cursor,
        bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
    )
    cursor.execute(psql.SQL("DROP SCHEMA {} RESTRICT").format(psql.Identifier(schema_name)))
    cursor.execute("SELECT to_regnamespace(%s)", (schema_name,))
    if cursor.fetchone() != (None,):
        raise RuntimeError("temporary Lakebase bootstrap wrapper cleanup did not converge")


def cleanup_wrapper(
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str | None,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
) -> None:
    """Tear down any reviewed wrapper state in one explicit transaction."""

    schema_name = wrapper_schema_name(
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
    )
    cursor.execute("SELECT to_regnamespace(%s)", (schema_name,))
    if cursor.fetchone() == (None,):
        return
    cursor.execute("BEGIN")
    try:
        cursor.execute(psql.SQL("SET LOCAL ROLE {}").format(psql.Identifier(_WRAPPER_OWNER)))
        _teardown_wrapper(
            cursor,
            instance_name=instance_name,
            database_name=database_name,
            target_application_id=target_application_id,
            bootstrap_application_id=bootstrap_application_id,
            expected_executor=expected_executor,
            allow_absent_managed_event_triggers=(allow_absent_managed_event_triggers),
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
        _assert_wrapper_lock(
            bootstrap_lock_cursor,
            bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
        cursor.execute("COMMIT")
    except BaseException:
        with suppress(Exception):
            cursor.execute("ROLLBACK")
        raise
