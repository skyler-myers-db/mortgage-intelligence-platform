"""Exact app-runtime and verifier privilege postflights."""

from __future__ import annotations

from jobs.lakebase_migration_acl import (
    _postflight_direct_column_privileges,
    _postflight_effective_column_only_privileges,
    _postflight_effective_default_privileges,
    _postflight_effective_routine_privileges,
    _postflight_effective_schema_privileges,
    _postflight_role_security,
)
from jobs.lakebase_migration_contracts import (
    _AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES,
    _APP_ROLE_ROUTINE_PRIVILEGES,
    _APP_ROLE_SEQUENCE_PRIVILEGES,
    _APP_ROLE_TABLE_PRIVILEGES,
    _SEQUENCE_PRIVILEGE_NAMES,
    _TABLE_PRIVILEGE_NAMES,
)
from jobs.lakebase_migration_roles import _raise_object_inventory_mismatch
from jobs.lakebase_migration_schema_hooks import _postflight_trigger_inventory


def _postflight_app_role_grants(cur: object, role: str) -> None:
    """Verify the app role's effective privileges exactly match the matrix."""
    cur.execute(  # type: ignore[attr-defined]
        "SELECT rolname FROM pg_roles WHERE rolname = %s",
        (role,),
    )
    role_rows = cur.fetchall()  # type: ignore[attr-defined]
    if role_rows != [(role,)]:
        raise RuntimeError(f"Lakebase app-role postflight could not verify exact role {role!r}")

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            has_database_privilege(%s, current_database(), 'CONNECT'),
            has_database_privilege(%s, current_database(), 'CREATE'),
            has_database_privilege(%s, current_database(), 'TEMPORARY'),
            has_schema_privilege(%s, 'mip_app', 'USAGE'),
            has_schema_privilege(%s, 'mip_app', 'CREATE')
        """,
        (role, role, role, role, role),
    )
    (
        database_connect,
        database_create,
        database_temporary,
        schema_usage,
        schema_create,
    ) = cur.fetchone()  # type: ignore[attr-defined]
    if (
        not database_connect
        or database_create
        or database_temporary
        or not schema_usage
        or schema_create
    ):
        raise RuntimeError(
            "Lakebase app-role database/schema postflight failed for "
            f"{role!r}: database_connect={database_connect}, "
            f"database_create={database_create}, schema_usage={schema_usage}, "
            f"schema_create={schema_create}, database_temporary={database_temporary}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT CASE
                   WHEN c.relkind = 'r' THEN c.relname
                   ELSE '__non_base_relation__:' || c.relname || ':' || c.relkind
               END
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY c.relname
        """
    )
    actual_tables = {str(row[0]) for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_tables = set(_APP_ROLE_TABLE_PRIVILEGES)
    _raise_object_inventory_mismatch("table", actual=actual_tables, expected=expected_tables)

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE n.nspname = 'mip_app'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND has_table_privilege(%s, c.oid, privilege.name)
        ORDER BY c.relname, privilege.name
        """,
        (list(_TABLE_PRIVILEGE_NAMES), role),
    )
    actual_table_privileges: dict[str, set[str]] = {str(table): set() for table in actual_tables}
    for table, privilege in cur.fetchall():  # type: ignore[attr-defined]
        actual_table_privileges.setdefault(table, set()).add(privilege)

    delete_tables = sorted(
        table for table, privileges in actual_table_privileges.items() if "DELETE" in privileges
    )
    if delete_tables:
        raise RuntimeError(f"Lakebase app role {role!r} has forbidden DELETE on {delete_tables}")

    expected_table_privileges = {
        table: set(privileges) for table, privileges in _APP_ROLE_TABLE_PRIVILEGES.items()
    }
    if actual_table_privileges != expected_table_privileges:
        raise RuntimeError(
            "Lakebase app-role table privilege postflight failed for "
            f"{role!r}: actual={actual_table_privileges}, "
            f"expected={expected_table_privileges}"
        )

    _postflight_direct_column_privileges(
        cur,
        role,
        principal_label="app role",
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind = 'S'
        ORDER BY c.relname
        """
    )
    actual_sequences = {row[0] for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_sequences = set(_APP_ROLE_SEQUENCE_PRIVILEGES)
    _raise_object_inventory_mismatch(
        "sequence", actual=actual_sequences, expected=expected_sequences
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE n.nspname = 'mip_app'
          AND c.relkind = 'S'
          AND has_sequence_privilege(%s, c.oid, privilege.name)
        ORDER BY c.relname, privilege.name
        """,
        (list(_SEQUENCE_PRIVILEGE_NAMES), role),
    )
    actual_sequence_privileges: dict[str, set[str]] = {
        str(sequence): set() for sequence in actual_sequences
    }
    for sequence, privilege in cur.fetchall():  # type: ignore[attr-defined]
        actual_sequence_privileges.setdefault(sequence, set()).add(privilege)
    expected_sequence_privileges = {
        sequence: set(privileges) for sequence, privileges in _APP_ROLE_SEQUENCE_PRIVILEGES.items()
    }
    if actual_sequence_privileges != expected_sequence_privileges:
        raise RuntimeError(
            "Lakebase app-role sequence privilege postflight failed for "
            f"{role!r}: actual={actual_sequence_privileges}, "
            f"expected={expected_sequence_privileges}"
        )

    _postflight_effective_default_privileges(
        cur,
        role,
        principal_label="app role",
    )
    _postflight_effective_schema_privileges(
        cur,
        role,
        principal_label="app role",
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'mip_app')
          AND n.nspname !~ '^pg_'
          AND has_table_privilege(%s, c.oid, privilege.name)
          AND (
              n.nspname <> 'public'
              OR has_schema_privilege(%s, n.oid, 'USAGE')
          )
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (
            list(_TABLE_PRIVILEGE_NAMES),
            role,
            role,
        ),
    )
    other_table_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if other_table_privileges:
        raise RuntimeError(
            "Lakebase app role has forbidden effective privileges on other tables: "
            f"{other_table_privileges}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind = 'S'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'mip_app')
          AND n.nspname !~ '^pg_'
          AND has_sequence_privilege(%s, c.oid, privilege.name)
          AND (
              n.nspname <> 'public'
              OR has_schema_privilege(%s, n.oid, 'USAGE')
          )
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (list(_SEQUENCE_PRIVILEGE_NAMES), role, role),
    )
    other_sequence_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if other_sequence_privileges:
        raise RuntimeError(
            "Lakebase app role has forbidden effective privileges on other sequences: "
            f"{other_sequence_privileges}"
        )

    _postflight_effective_routine_privileges(
        cur,
        role,
        principal_label="app role",
        expected=_APP_ROLE_ROUTINE_PRIVILEGES,
    )
    _postflight_role_security(cur, role, principal_label="app role")
    _postflight_effective_column_only_privileges(
        cur,
        role,
        principal_label="app role",
    )
    _postflight_trigger_inventory(
        cur,
        role,
        principal_label="app role",
    )


def _postflight_ai_gateway_verifier_grants(cur: object, role: str) -> None:
    """Verify the verifier can write only the AI Gateway proof ledger."""

    cur.execute(  # type: ignore[attr-defined]
        "SELECT rolname FROM pg_roles WHERE rolname = %s",
        (role,),
    )
    if cur.fetchall() != [(role,)]:  # type: ignore[attr-defined]
        raise RuntimeError(
            "Lakebase AI Gateway verifier postflight could not verify exact " f"role {role!r}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT
            has_database_privilege(%s, current_database(), 'CONNECT'),
            has_database_privilege(%s, current_database(), 'CREATE'),
            has_database_privilege(%s, current_database(), 'TEMPORARY'),
            has_schema_privilege(%s, 'mip_app', 'USAGE'),
            has_schema_privilege(%s, 'mip_app', 'CREATE')
        """,
        (role, role, role, role, role),
    )
    (
        database_connect,
        database_create,
        database_temporary,
        schema_usage,
        schema_create,
    ) = cur.fetchone()  # type: ignore[attr-defined]
    if (
        not database_connect
        or database_create
        or database_temporary
        or not schema_usage
        or schema_create
    ):
        raise RuntimeError(
            "Lakebase AI Gateway verifier database/schema postflight failed for "
            f"{role!r}: database_connect={database_connect}, "
            f"database_create={database_create}, schema_usage={schema_usage}, "
            f"schema_create={schema_create}, database_temporary={database_temporary}"
        )

    _postflight_effective_schema_privileges(
        cur,
        role,
        principal_label="AI Gateway verifier",
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT CASE
                   WHEN c.relkind = 'r' THEN c.relname
                   ELSE '__non_base_relation__:' || c.relname || ':' || c.relkind
               END
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY c.relname
        """
    )
    actual_tables = {str(row[0]) for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_tables = set(_APP_ROLE_TABLE_PRIVILEGES)
    _raise_object_inventory_mismatch(
        "AI Gateway verifier table",
        actual=actual_tables,
        expected=expected_tables,
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE n.nspname = 'mip_app'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND has_table_privilege(%s, c.oid, privilege.name)
        ORDER BY c.relname, privilege.name
        """,
        (list(_TABLE_PRIVILEGE_NAMES), role),
    )
    actual_table_privileges: dict[str, set[str]] = {str(table): set() for table in actual_tables}
    for table, privilege in cur.fetchall():  # type: ignore[attr-defined]
        actual_table_privileges.setdefault(table, set()).add(privilege)
    expected_table_privileges: dict[str, set[str]] = {
        str(table): set() for table in expected_tables
    }
    expected_table_privileges["ai_gateway_proof_ledger"] = set(
        _AI_GATEWAY_VERIFIER_TABLE_PRIVILEGES
    )
    if actual_table_privileges != expected_table_privileges:
        raise RuntimeError(
            "Lakebase AI Gateway verifier table privilege postflight failed for "
            f"{role!r}: actual={actual_table_privileges}, "
            f"expected={expected_table_privileges}"
        )

    _postflight_direct_column_privileges(
        cur,
        role,
        principal_label="AI Gateway verifier",
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND NOT (
              n.nspname = 'mip_app'
              AND c.relname = 'ai_gateway_proof_ledger'
          )
          AND has_table_privilege(%s, c.oid, privilege.name)
          AND (
              n.nspname <> 'public'
              OR has_schema_privilege(%s, n.oid, 'USAGE')
          )
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (
            list(_TABLE_PRIVILEGE_NAMES),
            role,
            role,
        ),
    )
    other_table_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if other_table_privileges:
        raise RuntimeError(
            "Lakebase AI Gateway verifier has forbidden privileges on other tables: "
            f"{other_table_privileges}"
        )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind = 'S'
        ORDER BY c.relname
        """
    )
    actual_sequences = {row[0] for row in cur.fetchall()}  # type: ignore[attr-defined]
    expected_sequences = set(_APP_ROLE_SEQUENCE_PRIVILEGES)
    _raise_object_inventory_mismatch(
        "AI Gateway verifier sequence",
        actual=actual_sequences,
        expected=expected_sequences,
    )

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind = 'S'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND has_sequence_privilege(%s, c.oid, privilege.name)
          AND (
              n.nspname <> 'public'
              OR has_schema_privilege(%s, n.oid, 'USAGE')
          )
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (list(_SEQUENCE_PRIVILEGE_NAMES), role, role),
    )
    verifier_sequence_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if verifier_sequence_privileges:
        raise RuntimeError(
            "Lakebase AI Gateway verifier has forbidden sequence privileges: "
            f"{verifier_sequence_privileges}"
        )

    _postflight_effective_default_privileges(
        cur,
        role,
        principal_label="AI Gateway verifier",
    )
    _postflight_effective_routine_privileges(
        cur,
        role,
        principal_label="AI Gateway verifier",
        expected={},
    )
    _postflight_role_security(cur, role, principal_label="AI Gateway verifier")
    _postflight_effective_column_only_privileges(
        cur,
        role,
        principal_label="AI Gateway verifier",
    )
    _postflight_trigger_inventory(
        cur,
        role,
        principal_label="AI Gateway verifier",
    )
