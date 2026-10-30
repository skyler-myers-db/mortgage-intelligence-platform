"""Executor probe for ``--mode plan``: the ownership apply needs, named.

Apply runs as the database owner, replaces functions, alters tables and
recreates triggers, and its trigger postflight requires the executor to own
each trigger's table and function exactly. The probe reads, before any App
stop, every one of those ownerships the connecting identity lacks.
"""

from __future__ import annotations

from collections import Counter

from jobs.lakebase_migration_contracts import (
    _APP_ROLE_ROUTINE_PRIVILEGES,
    _APP_ROLE_SEQUENCE_PRIVILEGES,
    _APP_ROLE_TABLE_PRIVILEGES,
)
from jobs.lakebase_migration_read_only import ReadOnlyCursor

_EXECUTOR_AUTHORITY_SQL = """
SELECT
    pg_has_role(current_user, database_object.datdba, 'USAGE'),
    pg_has_role(current_user, 'pg_database_owner', 'SET'),
    has_database_privilege(current_user, current_database(), 'CREATE'),
    schema_namespace.oid IS NOT NULL,
    COALESCE(schema_namespace.nspowner = executor.oid, FALSE)
FROM pg_database database_object
JOIN pg_roles executor ON executor.rolname = current_user
LEFT JOIN pg_namespace schema_namespace ON schema_namespace.nspname = 'mip_app'
WHERE database_object.datname = current_database()
"""
# Apply replaces functions, alters tables and recreates triggers, and its
# trigger postflight requires the executor to own each trigger's table and
# function exactly. Implicit row and array types follow their owners.
_EXECUTOR_UNOWNED_SQL = """
SELECT mip_object.object_kind, mip_object.object_name
FROM (
    SELECT 'relation' AS object_kind,
           relation.relname::text AS object_name,
           relation.relowner AS owner_oid
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = 'mip_app'
      AND relation.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'c')
    UNION ALL
    SELECT 'function',
           routine.proname || '(' || oidvectortypes(routine.proargtypes) || ')',
           routine.proowner
    FROM pg_proc routine
    JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
    WHERE namespace.nspname = 'mip_app'
    UNION ALL
    SELECT 'type', type_object.typname::text, type_object.typowner
    FROM pg_type type_object
    JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
    WHERE namespace.nspname = 'mip_app'
      AND type_object.typrelid = 0
      AND NOT EXISTS (
          SELECT 1 FROM pg_type element_type WHERE element_type.typarray = type_object.oid
      )
) mip_object
WHERE mip_object.owner_oid <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
ORDER BY mip_object.object_kind, mip_object.object_name
"""


def _reviewed_object(kind: str, name: str) -> bool:
    if kind == "relation":
        return name in _APP_ROLE_TABLE_PRIVILEGES or name in _APP_ROLE_SEQUENCE_PRIVILEGES
    if kind == "function":
        return any(
            f"{routine}({arguments})" == name for routine, arguments in _APP_ROLE_ROUTINE_PRIVILEGES
        )
    return False


def probe_executor(cur: ReadOnlyCursor) -> tuple[str, ...]:
    """Name every ownership the connecting identity lacks to run apply.

    Reviewed ``mip_app`` objects are named; any other object (which apply's
    inventory postflight would reject anyway) is only counted per kind, so a
    foreign object name never reaches a public report.
    """

    cur.execute(_EXECUTOR_AUTHORITY_SQL)
    authority = cur.fetchone()
    if authority is None:
        raise RuntimeError("Lakebase executor probe found no current database row")
    database_owner, owner_role_set, database_create, schema_present, schema_owner = authority
    missing: list[str] = []
    if not database_owner:
        missing.append("database:owner")
    if not owner_role_set:
        missing.append("role:pg_database_owner:SET")
    if not schema_present and not database_create:
        missing.append("database:CREATE")
    if schema_present and not schema_owner:
        missing.append("schema:mip_app")
    cur.execute(_EXECUTOR_UNOWNED_SQL)
    unreviewed: Counter[str] = Counter()
    for kind, name in cur.fetchall():
        if _reviewed_object(str(kind), str(name)):
            missing.append(f"{kind}:{name}")
        else:
            unreviewed[str(kind)] += 1
    missing.extend(f"{kind}:<unreviewed>x{count}" for kind, count in sorted(unreviewed.items()))
    return tuple(missing)
