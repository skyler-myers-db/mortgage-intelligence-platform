"""What the grants job, run as the deployer after the Lakebase handover, must be able to REVOKE on (PR #336 review).

The grants job (jobs/lakebase_migration_grants.py) revokes every privilege of
the App, verifier and jobs-reader roles on the database and on every relation,
sequence and routine in every non-system schema, but the provider's own
objects in ``public``. PostgreSQL refuses a REVOKE outright, "permission
denied for table X", when the role issuing it holds no privilege at all on
the object (``restrict_and_check_grant``: none of its own, none through
PUBLIC or a role it inherits, and not the owner's). USAGE on the object's
schema (``target_needs_schema_usage``) is needed for the lookup but is not
that privilege: on PostgreSQL 16.15 a legacy-owned table outside ``mip_app``,
with USAGE granted, planned clean, and the grants job run as the target failed
on it after the apply.

``unreachable_objects`` reads, by the grants job's own predicates, every such
object the handover does not move to the target (everything outside
``mip_app``, and each ``mip_app`` object another role owns) that the target
holds no privilege on. The plan refuses each one by count
(``target_needs_object_privilege``). ``--preconditions`` prints, for each one
the legacy owner can grant on (it owns it, or holds its owner's privileges),
the GRANT of one reviewed privilege (``REMEDY_PRIVILEGES``) that ends the
refusal, and refuses the rest: their owner grants it.

Not predicted: the column-level and default-privilege revokes. The grants job
revokes only entries naming the App roles there, which this tool cannot name;
a grants run before the handover leaves none, and one granted later fails the
grants job closed rather than silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.clients.lakebase_handover_acl import (
    NON_SYSTEM_SCHEMA_PREDICATE,
    PRIVILEGES_BY_KIND,
    grant_statement,
)
from tools.clients.lakebase_handover_sql import SCHEMA_NAME, PgObject

# The grants job's own clauses, verbatim (pinned against its source by a test).
RELATION_KINDS = "c.relkind IN ('r', 'p', 'v', 'm', 'f')"
SEQUENCE_KIND = "c.relkind = 'S'"
PROVIDER_PUBLIC = "n.nspname = 'public' AND owner.rolname = 'cloud_admin'"
_PREDICATE = "\n  AND ".join(NON_SYSTEM_SCHEMA_PREDICATE)
_ANY = {kind: ", ".join(privileges) for kind, privileges in PRIVILEGES_BY_KIND.items()}
# Read-only. ``moving`` is the legacy role: the handover gives the target each
# mip_app object it owns, and the database when it owns that. The privilege
# lists are PRIVILEGES_BY_KIND (any one held is enough); a CASE keeps a
# sequence function off a table and the reverse.
OBJECT_REACH_SQL = f"""
WITH target_role AS (
    SELECT oid FROM pg_roles WHERE rolname = %(target)s
), moving AS (
    SELECT oid FROM pg_roles WHERE rolname = %(legacy)s
), relation AS (
    SELECT c.oid, c.relkind, n.nspname, c.relname, c.relowner
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_roles owner ON owner.oid = c.relowner
    WHERE ({RELATION_KINDS} OR {SEQUENCE_KIND})
      AND {_PREDICATE}
      AND NOT ({PROVIDER_PUBLIC})
      AND NOT (n.nspname = %(schema)s AND c.relowner IN (SELECT oid FROM moving))
)
SELECT CASE WHEN relation.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END,
       relation.nspname::text, relation.relname::text, '',
       pg_has_role(current_user, relation.relowner, 'USAGE')
FROM relation
CROSS JOIN target_role
WHERE CASE WHEN relation.relkind = 'S'
           THEN NOT has_sequence_privilege(target_role.oid, relation.oid, '{_ANY["SEQUENCE"]}')
           ELSE NOT has_table_privilege(target_role.oid, relation.oid, '{_ANY["TABLE"]}')
      END
UNION ALL
SELECT CASE WHEN routine.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
       n.nspname::text, routine.proname::text, pg_get_function_identity_arguments(routine.oid)::text,
       pg_has_role(current_user, routine.proowner, 'USAGE')
FROM pg_proc routine
JOIN pg_namespace n ON n.oid = routine.pronamespace
JOIN pg_roles owner ON owner.oid = routine.proowner
CROSS JOIN target_role
WHERE {_PREDICATE}
  AND NOT ({PROVIDER_PUBLIC})
  AND NOT (n.nspname = %(schema)s AND routine.proowner IN (SELECT oid FROM moving))
  AND NOT has_function_privilege(target_role.oid, routine.oid, '{_ANY["FUNCTION"]}')
UNION ALL
SELECT 'DATABASE', '', database_object.datname::text, '',
       pg_has_role(current_user, database_object.datdba, 'USAGE')
FROM pg_database database_object
CROSS JOIN target_role
WHERE database_object.datname = current_database()
  AND database_object.datdba NOT IN (SELECT oid FROM moving)
  AND NOT has_database_privilege(target_role.oid, database_object.oid, '{_ANY["DATABASE"]}')
ORDER BY 1, 2, 3, 4
"""
# The one privilege per kind ``--preconditions`` grants: the least that gives
# the target a privilege on the object (reading it, or running it).
REMEDY_PRIVILEGES = {"TABLE": "SELECT", "SEQUENCE": "SELECT", "FUNCTION": "EXECUTE", "PROCEDURE": "EXECUTE", "DATABASE": "CONNECT"}


@dataclass(frozen=True)
class Unreachable:
    """An object the grants job revokes on that the target will hold no privilege on."""

    kind: str  # a REMEDY_PRIVILEGES key: the GRANT ... ON keyword
    schema: str
    name: str
    signature: str
    caller_can_grant: bool  # the session (the legacy owner) holds the owner's privileges

    def remedy(self, target: str) -> str:
        """The GRANT that ends the refusal; raises LakebaseHandoverRefused for an unreviewed signature."""

        item = PgObject(self.kind, self.schema, self.name, self.signature, "")
        return grant_statement(item, (REMEDY_PRIVILEGES[self.kind],), target)


def unreachable_objects(cursor: Any, *, legacy: str, target: str) -> list[Unreachable]:
    """Read-only; empty when the target role does not exist (the plan refuses that on its own)."""

    cursor.execute(OBJECT_REACH_SQL, {"target": target, "legacy": legacy, "schema": SCHEMA_NAME})
    return [
        Unreachable(str(kind), str(schema or ""), str(name), str(signature or ""), bool(grantable))
        for kind, schema, name, signature, grantable in cursor.fetchall()
    ]


__all__ = ["OBJECT_REACH_SQL", "PROVIDER_PUBLIC", "RELATION_KINDS", "REMEDY_PRIVILEGES", "SEQUENCE_KIND", "Unreachable", "unreachable_objects"]
