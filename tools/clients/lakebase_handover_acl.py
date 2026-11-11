"""The Lakebase handover's ACL reads and its only GRANT/REVOKE builders (spike S3, answers 3b, 4 and 6).

Three facts the owner changes alone do not cover:

* **Schema usage.** The grants job the deployer runs after the handover
  (jobs/lakebase_migration_grants.py) revokes on every table, sequence and
  routine in every non-system schema, so it fails "permission denied for
  schema X" for any schema the deployer cannot use (spike S3, answer 4).
  ``schemas_without_usage`` lists them, by the grants job's own predicate,
  leaving out ``mip_app`` (the handover gives it to the target) and a schema
  ``pg_database_owner`` owns (``public``): the target uses it as, or through,
  the database owner, which the plan requires on its own
  (``target_will_not_own_database``), because a USAGE row granted on
  ``public`` is one the grants job's exact public-schema gate refuses (round
  2 of the PR #336 review). USAGE is needed, not enough: each object there
  the target must REVOKE on is tools/clients/lakebase_handover_reach.py's.
* **The target's own grants.** ``ALTER ... OWNER TO`` rewrites the old owner to
  the new one as grantor and grantee and merges duplicates (PostgreSQL's
  ``aclnewowner``). On the apply, an entry the legacy owner granted the
  target, such as the L-A precondition ``CREATE ON SCHEMA mip_app``, merges
  into the target's owner entry; on the inverse, every entry naming the
  target becomes the legacy role's, so the target ends with nothing (spike
  S3, answer 3b: after an apply and a revert the deployer held no entry on
  ``mip_app`` and the next plan refused ``target_needs_schema_create``).
  ``target_grants`` reads those entries before the apply so the plan, the
  journal and the revert carry the ``GRANT`` that restores each one, run by
  the legacy owner right after the object is back in its hands: the same
  entry, same grantor. Any other entry naming the target cannot come back
  exactly (one another role granted it would return granted by the legacy
  owner, one with a grant option would leave that option on the owner's own
  entry, one the target granted would change grantor, one whose privilege
  the owner's own entry lacks would leave the owner holding it, and a column
  grant or a grant on a column's own sequence follows its table), so it is
  counted as unrevertible and the plan refuses rather than claim an exact
  revert.
* **The rehearsal's schema.** ``--rehearse`` creates and owns its schema, so
  it grants the target ``CREATE`` there itself and revokes exactly that grant
  afterwards (spike S3, answer 6).

Every GRANT and REVOKE the handover tools can render is built here, from
quoted identifiers and a reviewed privilege vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from tools.clients.lakebase_handover_sql import SCHEMA_NAME, PgObject, kind_of, quote_ident

# The grants job's schema predicate (jobs/lakebase_migration_grants.py), verbatim.
NON_SYSTEM_SCHEMA_PREDICATE = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema')",
    "n.nspname !~ '^pg_'",
    "n.nspname <> '__db_system'",
)
_PREDICATE = "\n  AND ".join(NON_SYSTEM_SCHEMA_PREDICATE)
# Read-only: every non-system schema the target cannot use, but mip_app and
# one pg_database_owner owns (the database owner's, module docstring).
SCHEMA_USAGE_SQL = f"""
SELECT n.nspname::text
FROM pg_namespace n
WHERE {_PREDICATE}
  AND n.nspname <> %(schema)s
  AND pg_get_userbyid(n.nspowner) <> 'pg_database_owner'
  AND NOT COALESCE(
      (SELECT has_schema_privilege(target_role.oid, n.oid, 'USAGE')
       FROM pg_roles target_role WHERE target_role.rolname = %(target)s),
      FALSE)
ORDER BY n.nspname
"""
# Read-only: every explicit ACL entry naming the target, as grantee or grantor,
# on the database, mip_app and every mip_app object (the inventory's scope),
# with the object's owner; and the owner's own entry on each (last column
# TRUE), whose privileges a restorable entry must be within. Tags are the
# inventory's, plus ``owned:`` for a column's own sequence (it moves with its
# table) and ``column:`` for a column grant (its entry follows its table's
# owner change).
TARGET_ACL_SQL = """
WITH target_role AS (
    SELECT oid FROM pg_roles WHERE rolname = %(target)s
), scoped(tag, schema_name, object_name, signature, column_name, owner_name, acl) AS (
    SELECT 'DATABASE', '', datname::text, '', '', pg_get_userbyid(datdba)::text, datacl
    FROM pg_database WHERE datname = current_database()
    UNION ALL
    SELECT 'SCHEMA', '', nspname::text, '', '', pg_get_userbyid(nspowner)::text, nspacl
    FROM pg_namespace WHERE nspname = %(schema)s
    UNION ALL
    SELECT CASE WHEN EXISTS (
                SELECT 1 FROM pg_depend dependency
                WHERE dependency.objid = relation.oid AND dependency.deptype IN ('a', 'i')
                  AND dependency.refclassid = 'pg_class'::regclass)
                THEN 'owned:' ELSE 'relkind:' END || relation.relkind::text,
           namespace.nspname::text, relation.relname::text, '', '',
           pg_get_userbyid(relation.relowner)::text,
           CASE WHEN relation.relkind = 'c'
                THEN (SELECT row_type.typacl FROM pg_type row_type WHERE row_type.oid = relation.reltype)
                ELSE relation.relacl END
    FROM pg_class relation
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = %(schema)s
      AND relation.relkind IN ('r', 'p', 'v', 'm', 'S', 'c', 'f')
    UNION ALL
    SELECT 'column:' || relation.relkind::text, namespace.nspname::text, relation.relname::text, '',
           attribute.attname::text, pg_get_userbyid(relation.relowner)::text, attribute.attacl
    FROM pg_attribute attribute
    JOIN pg_class relation ON relation.oid = attribute.attrelid
    JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
    WHERE namespace.nspname = %(schema)s
      AND attribute.attnum > 0 AND NOT attribute.attisdropped AND attribute.attacl IS NOT NULL
    UNION ALL
    SELECT 'prokind:' || routine.prokind::text, namespace.nspname::text, routine.proname::text,
           pg_get_function_identity_arguments(routine.oid)::text, '',
           pg_get_userbyid(routine.proowner)::text, routine.proacl
    FROM pg_proc routine
    JOIN pg_namespace namespace ON namespace.oid = routine.pronamespace
    WHERE namespace.nspname = %(schema)s
    UNION ALL
    SELECT 'typtype:' || type_object.typtype::text, namespace.nspname::text, type_object.typname::text, '', '',
           pg_get_userbyid(type_object.typowner)::text, type_object.typacl
    FROM pg_type type_object
    JOIN pg_namespace namespace ON namespace.oid = type_object.typnamespace
    WHERE namespace.nspname = %(schema)s
      AND type_object.typtype IN ('e', 'r', 'd')
      AND type_object.typelem = 0
)
SELECT scoped.tag, scoped.schema_name, scoped.object_name, scoped.signature, scoped.column_name,
       scoped.owner_name, entry.privilege_type::text, entry.is_grantable,
       entry.grantee = target_role.oid, pg_get_userbyid(entry.grantor)::text,
       entry.grantee = entry.grantor AND pg_get_userbyid(entry.grantee) = scoped.owner_name
FROM scoped
CROSS JOIN target_role
CROSS JOIN LATERAL aclexplode(scoped.acl) entry
WHERE entry.grantee = target_role.oid OR entry.grantor = target_role.oid
   OR (entry.grantee = entry.grantor AND pg_get_userbyid(entry.grantee) = scoped.owner_name)
ORDER BY 1, 2, 3, 4, 5, 7
"""
# PostgreSQL 16's object privileges (MAINTAIN is PostgreSQL 17's).
REVIEWED_PRIVILEGES = frozenset(
    {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "EXECUTE", "USAGE", "CREATE", "CONNECT", "TEMPORARY", "MAINTAIN"}
)
# The privileges of each kind the grants job revokes on that let a role that
# holds one issue its REVOKE ALL (a has_*_privilege list: any one is enough).
# REVOKE ALL on a relation also revokes every column's privileges, system
# columns included, and that per-column check counts only column privileges
# (SELECT, INSERT, UPDATE, REFERENCES; a sequence's are SELECT and UPDATE):
# with TRIGGER, DELETE or TRUNCATE alone on a table, view or materialized
# view, or USAGE alone on a sequence, PostgreSQL 16.15 refused it
# "permission denied for column tableoid" (round 2 of the PR #336 review).
PRIVILEGES_BY_KIND = {
    "TABLE": ("SELECT", "INSERT", "UPDATE", "REFERENCES"),
    "SEQUENCE": ("SELECT", "UPDATE"),
    "FUNCTION": ("EXECUTE",),
}
# GRANT ... ON <keyword>: a view and a materialized view are granted ON TABLE.
GRANT_KEYWORDS = {
    "DATABASE": "DATABASE",
    "SCHEMA": "SCHEMA",
    "TABLE": "TABLE",
    "VIEW": "TABLE",
    "MATERIALIZED VIEW": "TABLE",
    "SEQUENCE": "SEQUENCE",
    "FUNCTION": "FUNCTION",
    "PROCEDURE": "PROCEDURE",
    "TYPE": "TYPE",
    "DOMAIN": "DOMAIN",
}


def _privileges(privileges: Iterable[str]) -> str:
    chosen = sorted(set(privileges))
    if not chosen or any(privilege not in REVIEWED_PRIVILEGES for privilege in chosen):
        raise ValueError("a grant names only reviewed privileges")
    return ", ".join(chosen)


def grant_statement(item: PgObject, privileges: Iterable[str], role: str, *, grantable: bool = False) -> str:
    """``GRANT <privileges> ON <kind> <object> TO <role>``, optionally ``WITH GRANT OPTION``."""

    statement = f"GRANT {_privileges(privileges)} ON {GRANT_KEYWORDS[item.kind]} {item.target()} TO {quote_ident(role)}"
    return f"{statement} WITH GRANT OPTION" if grantable else statement


def revoke_statement(item: PgObject, privileges: Iterable[str], role: str) -> str:
    """``REVOKE <privileges> ON <kind> <object> FROM <role>``: the rehearsal's cleanup only."""

    return f"REVOKE {_privileges(privileges)} ON {GRANT_KEYWORDS[item.kind]} {item.target()} FROM {quote_ident(role)}"


def membership_statement(target: str, legacy: str) -> str:
    """L-A's self-grant, run by the legacy owner, who holds the ADMIN edge on the target (printed, never run)."""

    return f"GRANT {quote_ident(target)} TO {quote_ident(legacy)} WITH INHERIT TRUE, SET TRUE"


def schemas_without_usage(cursor: Any, *, target: str) -> list[str]:
    cursor.execute(SCHEMA_USAGE_SQL, {"schema": SCHEMA_NAME, "target": target})
    return [str(row[0]) for row in cursor.fetchall()]


@dataclass(frozen=True)
class TargetGrants:
    """Per planned object, the GRANTs that restore the target's merged entries; and what cannot be restored."""

    restores: dict[str, list[str]] = field(default_factory=dict)
    unrevertible: int = 0


def target_grants(cursor: Any, *, legacy: str, target: str, planned: Mapping[str, PgObject]) -> TargetGrants:
    """Read-only: the target's explicit entries on the objects the plan moves.

    Restorable: an entry the legacy owner granted the target without a grant
    option, of a privilege the owner's own entry holds. Every other entry
    naming the target is unrevertible (module docstring).
    """

    cursor.execute(TARGET_ACL_SQL, {"target": target, "schema": SCHEMA_NAME})
    rows = cursor.fetchall()
    # What the owner's own entry holds, per object (tag, schema, name, signature, column).
    owner_holds: dict[tuple[str, ...], set[str]] = {}
    for tag, schema, name, signature, column, _owner, privilege, _grantable, _grantee, _grantor, owner_entry in rows:
        if owner_entry:
            owner_holds.setdefault((str(tag), str(schema), str(name), str(signature), str(column)), set()).add(str(privilege))
    held: dict[str, set[str]] = {}
    unrevertible = 0
    for tag, schema, name, signature, column, owner, privilege, grantable, grantee_is_target, grantor, owner_entry in rows:
        if str(owner) != legacy:
            continue  # the handover does not move it
        if owner_entry and not grantee_is_target:
            continue  # the legacy owner's own entry, read above
        prefix = str(tag).partition(":")[0]
        restorable = (
            prefix not in {"owned", "column"}
            and not column
            and bool(grantee_is_target)
            and str(grantor) == legacy
            and not grantable
            and str(privilege) in REVIEWED_PRIVILEGES
            # Merged into the target's owner entry, it comes back on the legacy owner's.
            and str(privilege) in owner_holds.get((str(tag), str(schema), str(name), str(signature), str(column)), set())
        )
        if not restorable:
            unrevertible += 1
            continue
        kind, _definer = kind_of(tag)
        key = PgObject(kind, str(schema or ""), str(name), str(signature or ""), legacy).key
        if key in planned:
            held.setdefault(key, set()).add(str(privilege))
    restores = {key: [grant_statement(planned[key], privileges, target)] for key, privileges in sorted(held.items())}
    return TargetGrants(restores, unrevertible)


def restore_statements(entry: Mapping[str, Any]) -> Sequence[str]:
    """A journaled change's revert transaction: its inverse, then the target's restored grants."""

    return [str(entry["revert_statement"]), *(str(text) for text in entry.get("revert_grants") or ())]


__all__ = [
    "GRANT_KEYWORDS",
    "NON_SYSTEM_SCHEMA_PREDICATE",
    "PRIVILEGES_BY_KIND",
    "REVIEWED_PRIVILEGES",
    "SCHEMA_USAGE_SQL",
    "TARGET_ACL_SQL",
    "TargetGrants",
    "grant_statement",
    "membership_statement",
    "restore_statements",
    "revoke_statement",
    "schemas_without_usage",
    "target_grants",
]
