"""Exact cleanup for interrupted pre-wrapper bootstrap ACL states."""

from __future__ import annotations

from typing import Any

from psycopg import sql as psql

from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
    cleanup_executor_identity,
)
from tools.databricks.lakebase_oauth_role_bootstrap_wrapper import (
    _event_trigger_preflight,
)


def _legacy_acl_state(
    cursor: Any,
    *,
    database_name: str,
    application_id: str,
    executor: str,
) -> tuple[bool, bool, int, int]:
    cursor.execute(
        """
        SELECT database_object.oid,
               acl.privilege_type,
               acl.is_grantable,
               grantor.rolname
        FROM pg_database database_object
        CROSS JOIN LATERAL aclexplode(database_object.datacl) acl
        JOIN pg_roles grantee ON grantee.oid = acl.grantee
        JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE database_object.datname = %s
          AND grantee.rolname = %s
        ORDER BY 2, 3, 4
        """,
        (database_name, application_id),
    )
    database_rows = list(cursor.fetchall())
    cursor.execute(
        """
        SELECT database_object.oid,
               namespace.oid,
               acl.privilege_type,
               acl.is_grantable,
               grantor.rolname
        FROM pg_namespace namespace
        CROSS JOIN pg_database database_object
        CROSS JOIN LATERAL aclexplode(namespace.nspacl) acl
        JOIN pg_roles grantee ON grantee.oid = acl.grantee
        JOIN pg_roles grantor ON grantor.oid = acl.grantor
        WHERE namespace.nspname = 'public'
          AND database_object.datname = current_database()
          AND grantee.rolname = %s
        ORDER BY 3, 4, 5
        """,
        (application_id,),
    )
    schema_rows = list(cursor.fetchall())

    database_oid = int(database_rows[0][0]) if database_rows else 0
    schema_database_oid = int(schema_rows[0][0]) if schema_rows else 0
    if database_oid and schema_database_oid and database_oid != schema_database_oid:
        raise RuntimeError("legacy bootstrap ACL database identity drifted")
    if not database_oid:
        cursor.execute("SELECT oid FROM pg_database WHERE datname = %s", (database_name,))
        row = cursor.fetchone()
        if row is None:
            if schema_rows:
                raise RuntimeError("legacy bootstrap ACL exists outside the absent target database")
            cursor.execute("SELECT oid FROM pg_database WHERE datname = current_database()")
            current_database_row = cursor.fetchone()
            cursor.execute("SELECT oid FROM pg_namespace WHERE nspname = 'public'")
            current_schema_row = cursor.fetchone()
            if current_database_row is None or current_schema_row is None:
                raise RuntimeError("legacy bootstrap admin database contract is absent")
            return False, False, int(current_database_row[0]), int(current_schema_row[0])
        database_oid = int(row[0])
    if not schema_rows:
        cursor.execute("SELECT oid FROM pg_namespace WHERE nspname = 'public'")
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("legacy bootstrap public schema is absent")
        schema_oid = int(row[0])
    else:
        schema_oid = int(schema_rows[0][1])

    expected_database = [(database_oid, "CREATE", False, executor)]
    expected_schema = [(database_oid, schema_oid, "USAGE", False, executor)]
    if database_rows not in ([], expected_database):
        raise RuntimeError("legacy bootstrap database ACL drifted")
    if schema_rows not in ([], expected_schema):
        raise RuntimeError("legacy bootstrap public-schema ACL drifted")
    return bool(database_rows), bool(schema_rows), database_oid, schema_oid


def _assert_legacy_dependencies(
    cursor: Any,
    *,
    application_id: str,
    database_oid: int,
    schema_oid: int,
    database_create: bool,
    public_usage: bool,
) -> None:
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
        (application_id,),
    )
    expected: list[tuple[Any, ...]] = []
    if database_create:
        expected.append((0, "pg_database", database_oid, 0, "a"))
    if public_usage:
        expected.append((database_oid, "pg_namespace", schema_oid, 0, "a"))
    if list(cursor.fetchall()) != sorted(expected):
        raise RuntimeError("legacy bootstrap ACL dependency drifted")


def cleanup_legacy_acl_dependencies(
    cursor: Any,
    *,
    database_name: str,
    application_id: str,
    expected_executor: str,
    allow_absent_managed_event_triggers: bool,
) -> None:
    """Converge only the four reviewed old ACL states to no dependencies."""

    executor = cleanup_executor_identity(
        cursor,
        excluded_application_id=application_id,
        expected_executor=expected_executor,
    )
    database_create, public_usage, database_oid, schema_oid = _legacy_acl_state(
        cursor,
        database_name=database_name,
        application_id=application_id,
        executor=executor,
    )
    _assert_legacy_dependencies(
        cursor,
        application_id=application_id,
        database_oid=database_oid,
        schema_oid=schema_oid,
        database_create=database_create,
        public_usage=public_usage,
    )
    if database_create:
        _event_trigger_preflight(
            cursor,
            principal_label="legacy bootstrap database ACL REVOKE",
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )
        cursor.execute(
            psql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(
                psql.Identifier(database_name),
                psql.Identifier(application_id),
            )
        )
    if public_usage:
        _event_trigger_preflight(
            cursor,
            principal_label="legacy bootstrap public-schema ACL REVOKE",
            allow_absent_managed_event_triggers=allow_absent_managed_event_triggers,
        )
        cursor.execute(
            psql.SQL("REVOKE USAGE ON SCHEMA public FROM {}").format(
                psql.Identifier(application_id),
            )
        )
    state = _legacy_acl_state(
        cursor,
        database_name=database_name,
        application_id=application_id,
        executor=executor,
    )
    if state[:2] != (False, False):
        raise RuntimeError("legacy bootstrap ACL cleanup did not converge")
    _assert_legacy_dependencies(
        cursor,
        application_id=application_id,
        database_oid=database_oid,
        schema_oid=schema_oid,
        database_create=False,
        public_usage=False,
    )
