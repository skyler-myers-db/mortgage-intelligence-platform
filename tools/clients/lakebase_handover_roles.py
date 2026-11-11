"""The two roles around the Lakebase handover, read-only: L-A setup before it, the legacy role's residue after (spike S3).

``--preconditions`` (run as the legacy owner, connected to the App database,
like ``--plan``) prints, in order and quoted, the SQL the owner runs before
``--plan`` so the deployer's role exists by spike S3's L-A mechanism:

1. ``databricks_create_role`` run BY THE LEGACY OWNER, which leaves it an
   ADMIN edge on the new role. A role created first through the REST API or
   the UI leaves no such edge, so step 2 would fail and the plan would refuse
   ``target_role_not_settable`` (spike S3, answer 6.5);
2. ``GRANT <target> TO <legacy> WITH INHERIT TRUE, SET TRUE``: SET for the
   apply, INHERIT for the exact revert (``caller_cannot_revert``);
3. ``GRANT CREATE ON SCHEMA mip_app TO <target>`` (``target_needs_schema_create``);
4. ``GRANT USAGE ON SCHEMA <s> TO <target>`` for every other non-system
   schema the target cannot use (``target_needs_schema_usage``);
5. ``GRANT <one privilege> ON <object> TO <target>`` for every object the
   handover leaves to another owner that the target holds nothing on, when
   the legacy owner can grant on it; the rest refuse
   ``target_needs_object_privilege`` (tools/clients/lakebase_handover_reach.py).

Each step already held is left out, so the output is exactly what remains to
run. Role and schema names come from the profile and the database, never from
source; the printed SQL names them, so it belongs in the owner's terminal or
``--report-out`` (mode 0600), never in a shared log. Nothing here executes a
statement that is not a read.

``--retire-check`` adds the Lakebase half of the gate before the owner drops
the legacy role: ``legacy_residue`` counts, by category and across every
database on the instance, what PostgreSQL's DROP ROLE would refuse over
(``pg_shdepend``) plus the memberships the legacy role granted. Spike S3
measured two the handover deliberately leaves: the ``pgcrypto`` extension
(the inventory moves no extension) and the legacy owner's own
``pg_default_acl`` row.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from tools.clients import handover_journal as journals
from tools.clients.lakebase_handover_acl import (
    grant_statement,
    membership_statement,
    schemas_without_usage,
)
from tools.clients.lakebase_handover_reach import unreachable_objects
from tools.clients.lakebase_handover_sql import (
    AUTHORITY_SQL,
    SCHEMA_NAME,
    LakebaseHandoverRefused,
    PgObject,
)

_PREFIX = "[mip-lakebase-handover]"
# The deployer is a service principal: its Postgres role is its application id.
_APPLICATION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FACTS_SQL = """
SELECT current_user::text,
       current_database()::text,
       pg_get_userbyid(database_object.datdba)::text,
       EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %(target)s),
       CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %(target)s)
            THEN pg_has_role(current_user, %(target)s, 'MEMBER WITH ADMIN OPTION') ELSE FALSE END,
       EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'databricks_auth')
FROM pg_database database_object
WHERE database_object.datname = current_database()
"""
# Read-only, instance-wide: what DROP ROLE would refuse over, by category.
RESIDUE_SQL = """
SELECT residue.category, count(*)::int
FROM (
    SELECT CASE
               WHEN dependency.classid = 'pg_extension'::regclass THEN 'owns_extension'
               WHEN dependency.classid = 'pg_default_acl'::regclass THEN 'default_acl'
               WHEN dependency.deptype = 'o' THEN 'owns_object'
               WHEN dependency.deptype = 'a' THEN 'holds_privilege'
               ELSE 'other_dependency'
           END
    FROM pg_shdepend dependency
    WHERE dependency.refclassid = 'pg_authid'::regclass
      AND dependency.refobjid = (SELECT oid FROM pg_roles WHERE rolname = %(legacy)s)
      AND dependency.classid <> 'pg_auth_members'::regclass
    UNION ALL
    SELECT 'granted_membership'
    FROM pg_auth_members membership
    WHERE membership.grantor = (SELECT oid FROM pg_roles WHERE rolname = %(legacy)s)
      AND membership.member <> membership.grantor
) AS residue(category)
GROUP BY residue.category
ORDER BY residue.category
"""
RESIDUE_CATEGORIES = ("default_acl", "granted_membership", "holds_privilege", "other_dependency", "owns_extension", "owns_object")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _schema(name: str) -> PgObject:
    return PgObject("SCHEMA", "", name, "", "")


def preconditions(cursor: Any, *, target: str) -> tuple[list[str], list[str]]:
    """Read-only: (the owner's remaining SQL, in order; refusal codes)."""

    cursor.execute(FACTS_SQL, {"target": target})
    facts = cursor.fetchone()
    if facts is None:
        raise RuntimeError("the precondition facts returned no row")
    legacy, database, database_owner = str(facts[0]), str(facts[1]), str(facts[2])
    target_exists, legacy_admin, extension_present = bool(facts[3]), bool(facts[4]), bool(facts[5])
    cursor.execute(AUTHORITY_SQL, {"target": target, "schema": SCHEMA_NAME})
    authority = cursor.fetchone() or ("", False, False, False, False, False, False)
    settable, schema_create, database_create, createdb, inherits = (bool(authority[index]) for index in (2, 3, 4, 5, 6))
    schemas = schemas_without_usage(cursor, target=target, owns_database=database_owner in {legacy, target})
    refusals: list[str] = []
    lines = [
        f"-- Lakebase handover preconditions (spike S3, L-A): run in this order as {legacy}, connected to {database}.",
        "-- The statements name roles: keep them out of shared logs.",
    ]
    if target == legacy:
        return lines, ["target_is_legacy"]
    if not target_exists:
        if not _APPLICATION_ID.fullmatch(target):
            refusals.append("target_identity_unreviewed")
        lines.append("-- 1. The target role, created by the legacy owner (never through the REST API or the UI first).")
        if not extension_present:
            lines.append("CREATE EXTENSION IF NOT EXISTS databricks_auth;")
        lines.append(f"SELECT databricks_create_role({_literal(target)}, 'SERVICE_PRINCIPAL');")
    elif not (settable and inherits) and not legacy_admin:
        refusals.append("target_role_without_admin_edge")
        lines.append(
            "-- REFUSED target_role_without_admin_edge: the target role exists but the legacy owner holds no ADMIN option "
            "on it (created through the REST API or the UI), so it cannot grant itself SET and INHERIT. "
            "The owner removes that role and re-runs this mode; the tool never drops a role."
        )
    if not (target_exists and settable and inherits) and "target_role_without_admin_edge" not in refusals:
        lines.append("-- 2. Hold the target's privileges (INHERIT, for the exact revert) and SET ROLE to it (for the apply).")
        lines.append(f"{membership_statement(target, legacy)};")
    if not (target_exists and schema_create):
        lines.append(f"-- 3. The target may own {SCHEMA_NAME}'s objects.")
        lines.append(f"{grant_statement(_schema(SCHEMA_NAME), ('CREATE',), target)};")
    if schemas:
        lines.append(f"-- 4. The target uses every other non-system schema ({len(schemas)}): the grants job it runs revokes in each.")
        lines.extend(f"{grant_statement(_schema(name), ('USAGE',), target)};" for name in schemas)
    if target_exists:
        refusals.extend(_object_privileges(cursor, lines, legacy=legacy, target=target))
    else:
        lines.append("-- 5. Once the target exists, re-run this mode: it then reads the objects the target must hold a privilege on.")
    if not database_create:
        refusals.append("caller_needs_database_create")
    if not createdb:
        refusals.append("caller_needs_createdb")
    if len(lines) == 2 and not refusals:
        lines.append("-- Every precondition holds.")
    lines.append("-- Then, read-only: python -m tools.clients.lakebase_handover --plan")
    return lines, refusals


def _object_privileges(cursor: Any, lines: list[str], *, legacy: str, target: str) -> list[str]:
    """Step 5: one privilege on each object the handover leaves to another owner (lakebase_handover_reach)."""

    unreachable = unreachable_objects(cursor, legacy=legacy, target=target)
    if not unreachable:
        return []
    lines.append(
        f"-- 5. The target holds a privilege on each object the handover leaves to another owner ({len(unreachable)}): "
        "the grants job it runs revokes on every one, and PostgreSQL refuses that from a role holding nothing on the object."
    )
    others = 0
    for item in unreachable:
        try:
            statement = item.remedy(target) if item.caller_can_grant else ""
        except LakebaseHandoverRefused:
            statement = ""
        if statement:
            lines.append(f"{statement};")
        else:
            others += 1
    if others:
        lines.append(
            f"-- REFUSED target_needs_object_privilege x{others}: the legacy owner cannot grant on these; "
            "each one's owner grants the target one privilege on it, then re-run this mode."
        )
    return ["target_needs_object_privilege"] * others


def run_preconditions(args: argparse.Namespace, current: Any) -> int:
    with current.read_session() as cursor:
        lines, refusals = preconditions(cursor, target=current.target)
    print("\n".join(lines))
    if args.report_out is not None:
        journals.write_private(args.report_out, {"statements": lines, "refusals": refusals})
    for code in sorted(set(refusals)):
        print(f"{_PREFIX} REFUSED {code}", file=sys.stderr)
    return 2 if refusals else 0


def legacy_residue(cursor: Any, *, legacy: str) -> dict[str, int]:
    """Read-only: per category, what keeps PostgreSQL from dropping the legacy role (zero when nothing does)."""

    cursor.execute(RESIDUE_SQL, {"legacy": legacy})
    found = {category: 0 for category in RESIDUE_CATEGORIES}
    for category, count in cursor.fetchall():
        found[str(category)] = found.get(str(category), 0) + int(count)
    return found


def run_retire_check(args: argparse.Namespace, current: Any) -> int:
    """Refuse while any job or pipeline runs as the legacy identity, or its Lakebase role still holds anything."""

    from tools.clients.handover_verify import retire_blockers

    workspace = current.workspace()
    legacy = args.legacy_identity or str(getattr(workspace.current_user.me(), "user_name", "") or "")
    blockers = retire_blockers(workspace, legacy_identity=legacy)
    with current.read_session() as cursor:
        residue = legacy_residue(cursor, legacy=legacy)
    if args.report_out is not None:
        journals.write_private(args.report_out, {"runs_as_legacy": blockers, "lakebase_residue": residue})
    print(f"{_PREFIX} retire-check: {len(blockers)} job(s) or pipeline(s) still run as the legacy identity")
    print(f"{_PREFIX} retire-check: the legacy role's Lakebase residue {json.dumps(residue, sort_keys=True)}")
    if blockers:
        print(f"{_PREFIX} REFUSED legacy_identity_in_use: keep the legacy role until none does", file=sys.stderr)
    held = sorted(category for category, count in residue.items() if count)
    if held:
        print(f"{_PREFIX} REFUSED legacy_role_residue ({', '.join(held)}): PostgreSQL would refuse to remove the role; clear it first", file=sys.stderr)
    return 2 if blockers or held else 0


__all__ = ["FACTS_SQL", "RESIDUE_CATEGORIES", "RESIDUE_SQL", "legacy_residue", "preconditions", "run_preconditions", "run_retire_check"]
