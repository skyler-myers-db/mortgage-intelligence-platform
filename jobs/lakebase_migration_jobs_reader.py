"""The optional Lakebase ``jobs_reader`` role (M2-PR5, plan section 4.3).

The lifecycle sync (jobs/sync_lifecycle_state.py) and the KPI snapshot
(jobs/kpi_snapshot.py) run as the non-admin jobs identity. The sync reads the
latest approval and disposition per borrower; the snapshot upserts one
``kpi_snapshots`` row per day (``INSERT ... ON CONFLICT DO UPDATE ...
RETURNING``, so SELECT, INSERT and UPDATE on that one table). This role is that
identity's whole Lakebase surface: USAGE on ``mip_app`` and
:data:`JOBS_READER_TABLE_PRIVILEGES`, nothing else. The name stays
``jobs_reader``: every other grant it holds is a read, and the KPI write is the
one the deployer (the database owner) made before M2-PR5 moved the job off it.

It is off unless jobs/lakebase_migrate.py is given ``--jobs-reader-client-id``
(the bundle passes ``${var.jobs_principal}``). Unset, the grant reconciliation
issues exactly the statements it issued before this role existed
(tests/unit/test_lakebase_jobs_reader.py pins the statement log's digest). Set,
inside the same failure-atomic transaction as the App and verifier roles:

* the role must already exist (tools.databricks.converge_lakebase_oauth_role
  ``--role-contract jobs_reader`` creates it LOGIN-only before the migration)
  and be distinct from the App, verifier and executor roles;
* everything it holds on the database, every schema, table, sequence and
  routine is revoked, then USAGE and the reviewed table grants are made;
* :func:`postflight_jobs_reader_grants` proves exactly that set, and any other
  privilege rolls the whole transaction back.

The lifecycle sync's snooze wake also UPDATEs ``mip_app.lead_snoozes``; this
role grants no write, so under it that fail-soft step is skipped (the sync's
exit code is unchanged). Granting that write is a separate owner decision.
"""

from __future__ import annotations

from collections.abc import Mapping

from jobs.lakebase_migration_acl import (
    _postflight_direct_column_privileges,
    _postflight_effective_column_only_privileges,
    _postflight_effective_default_privileges,
    _postflight_effective_routine_privileges,
    _postflight_effective_schema_privileges,
    _postflight_role_security,
)
from jobs.lakebase_migration_contracts import (
    _APP_ROLE_TABLE_PRIVILEGES,
    _SEQUENCE_PRIVILEGE_NAMES,
    _TABLE_PRIVILEGE_NAMES,
)
from jobs.lakebase_migration_roles import _raise_object_inventory_mismatch
from jobs.lakebase_migration_schema_hooks import _postflight_trigger_inventory

JOBS_READER_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "approvals": ("SELECT",),
    "call_dispositions": ("SELECT",),
    # The KPI snapshot's daily upsert (kpi_snapshots has a UUID key, no sequence).
    "kpi_snapshots": ("SELECT", "INSERT", "UPDATE"),
}
LABEL = "jobs reader"
_PLACEHOLDERS = frozenset({"00000000PLACEHOLDER"})


def resolve_jobs_reader_role(
    explicit_client_id: str | None,
    *,
    distinct_from: Mapping[str, str | None],
) -> str | None:
    """The jobs reader role, or ``None`` when the option is unset (the role is off)."""

    role = (explicit_client_id or "").strip()
    if not role:
        return None
    if role in _PLACEHOLDERS or (role.startswith("<") and role.endswith(">")):
        raise RuntimeError("the jobs reader client ID must be a real service principal, not a placeholder")
    for label, other in distinct_from.items():
        if other is not None and other.strip().casefold() == role.casefold():
            raise RuntimeError(f"the jobs reader role must be distinct from the {label} role")
    return role


def revoke_statements(
    role_identifier: str,
    *,
    database_identifier: str,
    schema_identifiers: list[str],
    table_identifiers: list[str],
    sequence_identifiers: list[str],
    routines: list[tuple[str, str, str, str, str, bool, str]],
) -> list[str]:
    """Everything the role could hold, revoked; the same shape the verifier's revokes take."""

    statements = [
        f"REVOKE CREATE ON DATABASE {database_identifier} FROM {role_identifier}",
        f"REVOKE TEMPORARY ON DATABASE {database_identifier} FROM {role_identifier}",
    ]
    statements += [f"REVOKE ALL PRIVILEGES ON SCHEMA {s} FROM {role_identifier}" for s in schema_identifiers]
    statements += [f"REVOKE ALL PRIVILEGES ON TABLE {t} FROM {role_identifier}" for t in table_identifiers]
    statements += [
        f"REVOKE ALL PRIVILEGES ON SEQUENCE {s} FROM {role_identifier}" for s in sequence_identifiers
    ]
    for kind, identity, schema, _name, _arguments, _definer, owner in routines:
        if schema == "public" and owner == "cloud_admin":
            continue
        statements.append(f"REVOKE ALL PRIVILEGES ON {kind} {identity} FROM {role_identifier}")
    return statements


def grant_statements(
    role_identifier: str,
    *,
    schema_identifier: str,
    table_identifiers: Mapping[str, str],
) -> list[str]:
    statements = [f"GRANT USAGE ON SCHEMA {schema_identifier} TO {role_identifier}"]
    for table, privileges in JOBS_READER_TABLE_PRIVILEGES.items():
        statements.append(
            f"GRANT {', '.join(privileges)} ON TABLE {table_identifiers[table]} TO {role_identifier}"
        )
    return statements


def postflight_jobs_reader_grants(cur: object, role: str) -> None:
    """The role holds exactly CONNECT, USAGE on mip_app and the reviewed table grants; else raise."""

    cur.execute("SELECT rolname FROM pg_roles WHERE rolname = %s", (role,))  # type: ignore[attr-defined]
    if cur.fetchall() != [(role,)]:  # type: ignore[attr-defined]
        raise RuntimeError(f"Lakebase {LABEL} postflight could not verify exact role {role!r}")
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
    connect, create, temporary, usage, schema_create = cur.fetchone()  # type: ignore[attr-defined]
    if not connect or create or temporary or not usage or schema_create:
        raise RuntimeError(
            f"Lakebase {LABEL} database/schema postflight failed for {role!r}: "
            f"database_connect={connect}, database_create={create}, schema_usage={usage}, "
            f"schema_create={schema_create}, database_temporary={temporary}"
        )
    _postflight_effective_schema_privileges(cur, role, principal_label=LABEL)

    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT CASE
                   WHEN c.relkind = 'r' THEN c.relname
                   ELSE '__non_base_relation__:' || c.relname || ':' || c.relkind::text
               END
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'mip_app' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY c.relname
        """
    )
    actual_tables = {str(row[0]) for row in cur.fetchall()}  # type: ignore[attr-defined]
    _raise_object_inventory_mismatch(
        f"{LABEL} table", actual=actual_tables, expected=set(_APP_ROLE_TABLE_PRIVILEGES)
    )
    # Every table privilege on every relation of every non-system schema: the
    # reviewed grants and nothing else, so another schema cannot hide a grant.
    cur.execute(  # type: ignore[attr-defined]
        """
        SELECT n.nspname, c.relname, privilege.name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(%s::text[]) AS privilege(name)
        WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND n.nspname !~ '^pg_'
          AND has_table_privilege(%s, c.oid, privilege.name)
          AND (
              n.nspname <> 'public'
              OR has_schema_privilege(%s, n.oid, 'USAGE')
          )
        ORDER BY n.nspname, c.relname, privilege.name
        """,
        (list(_TABLE_PRIVILEGE_NAMES), role, role),
    )
    actual = {(str(schema), str(table), str(privilege)) for schema, table, privilege in cur.fetchall()}  # type: ignore[attr-defined]
    expected = {
        ("mip_app", table, privilege)
        for table, privileges in JOBS_READER_TABLE_PRIVILEGES.items()
        for privilege in privileges
    }
    if actual != expected:
        raise RuntimeError(
            f"Lakebase {LABEL} table privilege postflight failed for {role!r}: "
            f"unexpected={sorted(actual - expected)}, missing={sorted(expected - actual)}"
        )
    _postflight_direct_column_privileges(cur, role, principal_label=LABEL)
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
    sequence_privileges = cur.fetchall()  # type: ignore[attr-defined]
    if sequence_privileges:
        raise RuntimeError(f"Lakebase {LABEL} has forbidden sequence privileges: {sequence_privileges}")
    _postflight_effective_default_privileges(cur, role, principal_label=LABEL)
    _postflight_effective_routine_privileges(cur, role, principal_label=LABEL, expected={})
    _postflight_role_security(cur, role, principal_label=LABEL)
    _postflight_effective_column_only_privileges(cur, role, principal_label=LABEL)
    _postflight_trigger_inventory(cur, role, principal_label=LABEL)
