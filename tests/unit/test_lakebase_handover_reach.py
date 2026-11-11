"""The Lakebase handover plan refuses an object the grants job run as the target could not REVOKE on (PR #336 review).

USAGE on a schema let the plan pass while the grants job, run as the target
after the apply, failed "permission denied for table synced" on a
legacy-owned table there (PostgreSQL 16.15). The fake models PostgreSQL's
rule, pinned first: a REVOKE needs SOME privilege on the object (owned or
inherited, granted, or PUBLIC's, including PostgreSQL's default PUBLIC
EXECUTE on a routine and CONNECT on a database). The real SQL runs in
tests/integration/test_lakebase_handover_postgres.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.fixtures.fake_lakebase import APP_ROLE, DEPLOYER, LEGACY, FakeServer, owner_entries
from tools.clients import lakebase_handover as handover
from tools.clients import lakebase_handover_reach as reach
from tools.clients import lakebase_handover_roles as roles
from tools.clients.lakebase_handover_sql import PgObject

REPO = Path(__file__).resolve().parents[2]
SYNCED = "TABLE:extras.synced"


def _with(server: FakeServer, *items: PgObject) -> FakeServer:
    server.objects.extend(items)
    server.owners.update({item.key: item.owner for item in items})
    return server


def _extras(owner: str = LEGACY, *items: PgObject) -> FakeServer:
    """A usable extra schema (the target holds USAGE) with a table ``owner`` owns."""

    server = FakeServer(schemas={"public": "pg_database_owner", "extras": owner})
    server.acl["SCHEMA:extras"] = {(DEPLOYER, owner, "USAGE"): False}
    return _with(server, PgObject("TABLE", "extras", "synced", "", owner), *items)


def _refusals(server: FakeServer) -> list[str]:
    return handover.plan(server.read_session, target=DEPLOYER).refusals


def _statements(server: FakeServer) -> tuple[list[str], list[str]]:
    with server.read_session() as cursor:
        lines, refusals = roles.preconditions(cursor, target=DEPLOYER)
    return [line for line in lines if not line.startswith("--")], refusals


def test_the_fake_holds_postgresqls_default_public_privileges() -> None:
    server = _with(FakeServer(), PgObject("FUNCTION", "extras", "helper", "", LEGACY), PgObject("DATABASE", "", "other", "", APP_ROLE))
    assert server.has_privilege(DEPLOYER, "FUNCTION:extras.helper()")  # PUBLIC EXECUTE, until the ACL is explicit
    assert server.has_privilege(DEPLOYER, "DATABASE:other", "CONNECT")
    server.acl["FUNCTION:extras.helper()"] = owner_entries("FUNCTION:", LEGACY)  # the grants job revoked PUBLIC
    assert not server.has_privilege(DEPLOYER, "FUNCTION:extras.helper()")
    assert not server.has_privilege(DEPLOYER, "TABLE:mip_app.campaigns"), "a table grants PUBLIC nothing"


def test_usage_on_the_schema_is_not_enough_the_plan_refuses_the_table_by_count(tmp_path: Path) -> None:
    server = _extras()
    refused = handover.plan(server.read_session, target=DEPLOYER)
    assert refused.refusals == ["target_needs_object_privilege"]
    assert "synced" not in json.dumps(refused.public()) and "extras" not in json.dumps(refused.public()), "counts, never names"
    with pytest.raises(handover.LakebaseHandoverRefused, match="refuses"):
        handover.apply(server.read_session, server.connect, target=DEPLOYER, ack=refused.sha256, journal_path=tmp_path / "j.json")
    assert server.transactions == []


@pytest.mark.parametrize(
    "item",
    [
        PgObject("VIEW", "extras", "synced_view", "", LEGACY),
        PgObject("SEQUENCE", "extras", "standalone", "", LEGACY),
        PgObject("PROCEDURE", "extras", "refresh", "", LEGACY),
        PgObject("TABLE", "public", "leftover", "", LEGACY),
        PgObject("TABLE", "mip_app", "app_owned_extra", "", APP_ROLE),
    ],
    ids=("view", "sequence", "procedure_without_public_execute", "legacy_table_in_public", "another_roles_mip_app_table"),
)
def test_every_kind_the_grants_job_revokes_on_counts(item: PgObject) -> None:
    server = _extras(LEGACY)
    server.acl[SYNCED] = {(DEPLOYER, LEGACY, "SELECT"): False}
    _with(server, item)
    server.acl[item.key] = owner_entries(f"{item.kind}:", item.owner)
    assert _refusals(server) == ["target_needs_object_privilege"]


def test_a_database_the_handover_leaves_counts_when_the_target_cannot_connect() -> None:
    server = FakeServer()
    server.owners["DATABASE:mip_app_state"] = APP_ROLE
    server.schemas["public"] = APP_ROLE  # not pg_database_owner's, so the usage check stays quiet
    server.acl["SCHEMA:public"] = {(DEPLOYER, APP_ROLE, "USAGE"): False}
    assert _refusals(server) == []  # PUBLIC CONNECT
    server.acl["DATABASE:mip_app_state"] = owner_entries("DATABASE:", APP_ROLE)
    assert _refusals(server) == ["target_needs_object_privilege"]


def test_what_the_target_reaches_or_the_grants_job_skips_plans_clean() -> None:
    server = _extras(
        LEGACY,
        PgObject("FUNCTION", "extras", "default_public", "", LEGACY),  # PUBLIC EXECUTE by default
        PgObject("TABLE", "public", "provider_table", "", "cloud_admin"),  # the grants job skips it
        PgObject("TABLE", "extras", "already_the_targets", "", DEPLOYER),
    )
    server.acl[SYNCED] = {(DEPLOYER, APP_ROLE, "TRIGGER"): False}  # any privilege, from anyone
    server.acl["TABLE:public.provider_table"] = {}
    assert _refusals(server) == []
    assert _refusals(FakeServer()) == [], "every mip_app object the legacy role owns moves to the target"
    del server.acl["TABLE:mip_app.app_owned_scratch"]
    assert _refusals(server) == ["target_needs_object_privilege"], "another role's mip_app table the target holds nothing on"


def test_preconditions_print_one_grant_per_object_the_legacy_owner_can_grant_and_refuse_the_rest() -> None:
    server = _extras(
        LEGACY,
        PgObject("SEQUENCE", "extras", "odd\"seq", "", LEGACY),
        PgObject("FUNCTION", "extras", "refresh", "since date", LEGACY),
        PgObject("TABLE", "extras", "vendor", "", APP_ROLE),
    )
    server.acl['SEQUENCE:extras.odd"seq'] = owner_entries("SEQUENCE:", LEGACY)
    server.acl["FUNCTION:extras.refresh(since date)"] = owner_entries("FUNCTION:", LEGACY)
    statements, refusals = _statements(server)
    assert statements == [
        f'GRANT EXECUTE ON FUNCTION "extras"."refresh"(since date) TO "{DEPLOYER}";',
        f'GRANT SELECT ON SEQUENCE "extras"."odd""seq" TO "{DEPLOYER}";',
        f'GRANT SELECT ON TABLE "extras"."synced" TO "{DEPLOYER}";',
    ]
    assert refusals == ["target_needs_object_privilege"], "the App role's table: its owner grants it"
    with server.connect().transaction() as connection, connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement.removesuffix(";"))
    assert _refusals(server) == ["target_needs_object_privilege"]
    server.acl["TABLE:extras.vendor"] = {(DEPLOYER, APP_ROLE, "SELECT"): False}
    assert _refusals(server) == [] and _statements(server) == ([], [])


def test_preconditions_for_a_missing_target_say_to_re_run_once_it_exists() -> None:
    server = _extras()
    server.roles.discard(DEPLOYER)
    with server.read_session() as cursor:
        lines, _refusals = roles.preconditions(cursor, target=DEPLOYER)
    assert any(line.startswith("-- 5. Once the target exists, re-run this mode") for line in lines)
    assert not [line for line in lines if line.startswith("GRANT SELECT")]


def test_the_reach_read_uses_the_grants_jobs_own_predicates() -> None:
    """A drift alarm: the clauses the grants job selects what it revokes on with, in the reach SQL verbatim."""

    source = re.sub(r"\s+", " ", (REPO / "jobs" / "lakebase_migration_grants.py").read_text(encoding="utf-8"))
    for clause in (reach.RELATION_KINDS, reach.SEQUENCE_KIND, f"NOT ( {reach.PROVIDER_PUBLIC} )"):
        assert clause in source, clause
    assert 'routine_schema == "public" and routine_owner == "cloud_admin"' in source, "routines: the same exclusion"
    assert "REVOKE TEMPORARY ON DATABASE" in source, "and the database"
    flattened = re.sub(r"\s+", " ", reach.OBJECT_REACH_SQL)
    for clause in (reach.RELATION_KINDS, reach.SEQUENCE_KIND, f"NOT ({reach.PROVIDER_PUBLIC})"):
        assert clause in flattened, clause
    assert flattened.count(f"NOT ({reach.PROVIDER_PUBLIC})") == 2, "relations and routines"
