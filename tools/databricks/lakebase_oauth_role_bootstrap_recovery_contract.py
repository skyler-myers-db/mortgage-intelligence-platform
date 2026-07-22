"""Read-only exact dependency contract for recoverable bootstrap ACL states."""

from __future__ import annotations

from typing import Any

from tools.databricks.lakebase_oauth_role_bootstrap_legacy_acl import (
    _legacy_acl_state,
)
from tools.databricks.lakebase_oauth_role_bootstrap_wrapper import (
    _schema_acl_contract,
    _schema_object_contract,
    wrapper_schema_name,
)
from tools.databricks.lakebase_oauth_role_bootstrap_wrapper_contract import (
    wrapper_function_contract,
)


def assert_recoverable_bootstrap_dependencies(
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    target_application_id: str,
    bootstrap_application_id: str,
    expected_executor: str,
) -> None:
    """Accept only states emitted by atomic wrapper or legacy direct grants."""

    schema_name = wrapper_schema_name(
        instance_name=instance_name,
        database_name=database_name,
        target_application_id=target_application_id,
    )
    cursor.execute("SELECT to_regnamespace(%s)", (schema_name,))
    schema_present = cursor.fetchone() != (None,)
    expected: set[tuple[Any, ...]] = set()
    if schema_present:
        schema_contract: tuple[str, int, int] | None = None
        schema_usage = False
        provider_grants = False
        for candidate_provider, candidate_bootstrap in (
            (True, None),
            (False, None),
            (False, bootstrap_application_id),
        ):
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
                "temporary Lakebase bootstrap provider grants survived function creation"
            )
        if schema_usage and not function_rows:
            raise RuntimeError("temporary Lakebase bootstrap schema grant has no bound function")
        if function_rows:
            function_execute = False
            try:
                function_oid, function_owner = wrapper_function_contract(
                    cursor,
                    schema_name=schema_name,
                    target_application_id=target_application_id,
                    bootstrap_application_id=bootstrap_application_id,
                    allow_bootstrap_execute=True,
                )
                function_execute = True
            except RuntimeError:
                function_oid, function_owner = wrapper_function_contract(
                    cursor,
                    schema_name=schema_name,
                    target_application_id=target_application_id,
                    bootstrap_application_id=bootstrap_application_id,
                    allow_bootstrap_execute=False,
                )
            if owner != function_owner:
                raise RuntimeError("temporary Lakebase bootstrap wrapper ownership drifted")
            _schema_object_contract(
                cursor,
                schema_oid=schema_oid,
                function_oid=function_oid,
            )
            if schema_usage:
                expected.add((database_oid, "pg_namespace", schema_oid, 0, "a"))
            if function_execute:
                expected.add((database_oid, "pg_proc", function_oid, 0, "a"))
        else:
            _schema_object_contract(cursor, schema_oid=schema_oid, function_oid=None)

    database_create, public_usage, database_oid, public_schema_oid = _legacy_acl_state(
        cursor,
        database_name=database_name,
        application_id=bootstrap_application_id,
        executor=expected_executor,
    )
    if schema_present and (database_create or public_usage):
        raise RuntimeError(
            "temporary Lakebase bootstrap mixed wrapper and legacy ACL state drifted"
        )
    if database_create:
        expected.add((0, "pg_database", database_oid, 0, "a"))
    if public_usage:
        expected.add((database_oid, "pg_namespace", public_schema_oid, 0, "a"))
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
    if set(tuple(row) for row in cursor.fetchall()) != expected:
        raise RuntimeError("temporary Lakebase bootstrap dependency state drifted")
