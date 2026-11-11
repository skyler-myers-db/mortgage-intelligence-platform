"""Spike S3's coverage gaps in the Lakebase handover, closed (answers 3b, 3c, 4, 5 and 6).

Against ``tests/fixtures/fake_lakebase.FakeServer``, which models what S3
measured on PostgreSQL 16.15; the first tests pin that model so a future fake
cannot drift back to answering the way the tool hoped:

* order: tables and sequences move before views, and SECURITY DEFINER
  routines after the schema (their owner resolves names with its USAGE
  there), so an App read through a view or a definer routine never fails
  during the apply or the revert (it failed 42501 when views moved first,
  and a definer did while it moved before the schema);
* schema usage: the plan refuses a non-system schema the target cannot use
  (``target_needs_schema_usage``, a count), because the grants job it runs
  next revokes in every one;
* exact revert: the target's own grants the apply merged away are journaled
  and restored, so apply, revert and a new plan give the original digest;
* retire-check reports the legacy role's extensions and default-ACL rows;
* ``--preconditions`` prints the owner's L-A SQL, in order and quoted;
* ``--rehearse`` grants the target CREATE on its own schema and takes it back.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures.fake_lakebase import (
    APP_ROLE,
    DEPLOYER,
    LEGACY,
    FakeServer,
    InsufficientPrivilege,
    VirtualConcurrent,
    owner_entries,
    throwaway,
)
from tests.fixtures.handover_jobs_fake import jobs_workspace
from tools.clients import handover_journal as journals
from tools.clients import lakebase_handover as handover
from tools.clients import lakebase_handover_acl as acl
from tools.clients import lakebase_handover_rehearse as rehearsal
from tools.clients import lakebase_handover_roles as roles
from tools.clients import lakebase_handover_sql as sql

REPO = Path(__file__).resolve().parents[2]
VIEW = "VIEW:mip_app.campaign_summary"
DEFINER = "FUNCTION:mip_app.approval_count()"
SCHEMA = "SCHEMA:mip_app"
# The pre-S3 apply order, with a definer routine where every routine was.
PRE_S3_ORDER = ("FUNCTION", "PROCEDURE", sql.DEFINER_PHASE, "DOMAIN", "TYPE", "SEQUENCE", "VIEW", "MATERIALIZED VIEW", "TABLE", "SCHEMA", "DATABASE")


def _clock(server: FakeServer) -> handover.Clock:
    return handover.Clock(now=server.clock, sleep=server.sleep)


def _plan(server: FakeServer) -> handover.LakebasePlan:
    return handover.plan(server.read_session, target=DEPLOYER)


def _apply(server: FakeServer, path: Path) -> journals.Journal:
    return handover.apply(
        server.read_session, server.connect, target=DEPLOYER, ack=_plan(server).sha256, journal_path=path, clock=_clock(server)
    )


def _revert(server: FakeServer, path: Path) -> bool:
    journal = journals.load(path, kind=handover.KIND)
    document, refusals = handover.revert_plan(server.read_session, journal)
    assert refusals == []
    ack = journals.plan_sha256(document, domain=handover.REVERT_DOMAIN)
    return handover.revert(server.read_session, server.connect, journal, ack=ack, clock=_clock(server))


def _probe_owner_reads(server: FakeServer) -> list[tuple[str, str]]:
    """After every committed transaction, an App read through each owner-privileged object."""

    failures: list[tuple[str, str]] = []

    def probe(_committed: list[str]) -> None:
        for key in server.owner_reads:
            try:
                server.app_read(key)
            except InsufficientPrivilege as exc:
                failures.append((key, exc.sqlstate))

    server.after_commit.append(probe)
    return failures


# -- the fake models what S3 measured ----------------------------------------


def test_the_fake_reads_a_view_with_its_owners_privileges_as_postgresql_does() -> None:
    """S3 R1-R3: a target-owned view over a legacy-owned table is 42501; L-A's INHERIT covers the reverse."""

    server = FakeServer()
    server.app_read(VIEW)  # both legacy-owned
    server.owners["TABLE:mip_app.campaigns"] = DEPLOYER
    server.app_read(VIEW)  # legacy view, target table: the legacy role inherits the target
    server.owners["TABLE:mip_app.campaigns"] = LEGACY
    server.owners[VIEW] = DEPLOYER
    with pytest.raises(InsufficientPrivilege) as refused:
        server.app_read(VIEW)
    assert refused.value.sqlstate == "42501"


def test_the_fake_merges_the_targets_grant_into_the_owner_entry_as_postgresql_does() -> None:
    """S3 answer 3b: after ALTER OWNER and its inverse, the target held no entry on mip_app."""

    server = FakeServer()
    server.change_owner(SCHEMA, LEGACY, DEPLOYER)
    assert server.acl[SCHEMA] == {**owner_entries(SCHEMA, DEPLOYER), (APP_ROLE, DEPLOYER, "USAGE"): False}
    server.change_owner(SCHEMA, DEPLOYER, LEGACY)
    assert server.acl[SCHEMA] == {**owner_entries(SCHEMA, LEGACY), (APP_ROLE, LEGACY, "USAGE"): False}
    assert not server.has_privilege(DEPLOYER, SCHEMA, "CREATE")


def test_the_fake_rewrites_every_target_entry_on_the_inverse_whatever_its_grantor() -> None:
    """aclnewowner on the inverse: grantee target becomes the legacy role, so another role's grant moves to it."""

    server = FakeServer()
    key = "TABLE:mip_app.approvals"
    server.acl[key] = {**owner_entries(key, LEGACY), (DEPLOYER, APP_ROLE, "SELECT"): False}
    server.change_owner(key, LEGACY, DEPLOYER)
    assert (DEPLOYER, APP_ROLE, "SELECT") in server.acl[key]
    server.change_owner(key, DEPLOYER, LEGACY)
    assert (LEGACY, APP_ROLE, "SELECT") in server.acl[key] and (DEPLOYER, APP_ROLE, "SELECT") not in server.acl[key]


def test_the_fake_resolves_a_definer_routines_names_with_its_owners_schema_usage() -> None:
    """PostgreSQL 16.15: a target-owned definer in a legacy-owned mip_app is 42501 (schema); a view is not."""

    server = FakeServer()
    server.owners["TABLE:mip_app.approvals"] = DEPLOYER
    server.owners["TABLE:mip_app.campaigns"] = DEPLOYER
    server.owners[VIEW] = DEPLOYER
    server.app_read(VIEW)  # a view's names were resolved when it was created
    server.owners[DEFINER] = DEPLOYER
    with pytest.raises(InsufficientPrivilege, match="schema"):
        server.app_read(DEFINER)
    server.owners[SCHEMA] = DEPLOYER
    server.app_read(DEFINER)
    server.owners[DEFINER] = LEGACY
    server.app_read(DEFINER)  # the legacy owner inherits the target's ownership of the schema


# -- order ---------------------------------------------------------------------


def test_no_app_read_through_a_view_or_a_definer_routine_fails_during_the_apply_or_the_revert(tmp_path: Path) -> None:
    server = FakeServer()
    failures = _probe_owner_reads(server)
    journal = _apply(server, tmp_path / "j.json")
    assert journal.status == "applied" and len(server.transactions) == len(journal.entries)
    assert failures == []
    phases = [entry["phase"] for entry in journal.entries]
    assert phases.index("TABLE") < phases.index("VIEW") < phases.index("SCHEMA") < phases.index(sql.DEFINER_PHASE) < phases.index("DATABASE")
    assert journal.entry(DEFINER)["kind"] == "FUNCTION" and journal.entry(DEFINER)["phase"] == sql.DEFINER_PHASE
    assert _revert(server, tmp_path / "j.json")
    assert failures == []


def test_a_definer_routine_moved_before_the_schema_fails_until_the_schema_follows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: definers before the schema (the PR's first order) broke the definer's reads both ways."""

    monkeypatch.setattr(sql, "ORDER", ("TYPE", "DOMAIN", "TABLE", "SEQUENCE", "FUNCTION", "PROCEDURE", "VIEW", "MATERIALIZED VIEW", sql.DEFINER_PHASE, "SCHEMA", "DATABASE"))
    server = FakeServer()
    failures = _probe_owner_reads(server)
    _apply(server, tmp_path / "j.json")
    assert failures == [(DEFINER, "42501")]
    assert _revert(server, tmp_path / "j.json")
    assert failures == [(DEFINER, "42501")] * 2


def test_the_pre_s3_order_breaks_those_reads_for_the_whole_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: views and routines first left them unable to read their tables (42501)."""

    monkeypatch.setattr(sql, "ORDER", PRE_S3_ORDER)
    server = FakeServer()
    failures = _probe_owner_reads(server)
    _apply(server, tmp_path / "j.json")
    assert {key for key, _code in failures} == {VIEW, DEFINER} and {code for _key, code in failures} == {"42501"}


# -- schema usage --------------------------------------------------------------


def test_the_plan_refuses_a_schema_the_target_cannot_use_by_count(tmp_path: Path) -> None:
    server = FakeServer(schemas={"public": "pg_database_owner", "extra_schema_a": LEGACY, "extra_schema_b": APP_ROLE})
    refused = _plan(server)
    assert refused.refusals.count("target_needs_schema_usage") == 2
    assert "extra_schema" not in json.dumps(refused.public()), "counts, never names"
    with pytest.raises(sql.LakebaseHandoverRefused, match="refuses"):
        _apply(server, tmp_path / "j.json")
    server.acl["SCHEMA:extra_schema_a"] = {(DEPLOYER, LEGACY, "USAGE"): False}
    server.acl["SCHEMA:extra_schema_b"] = {(DEPLOYER, APP_ROLE, "USAGE"): False}
    assert _plan(server).refusals == []


def test_public_counts_unless_the_target_will_own_the_database() -> None:
    """``public`` is pg_database_owner's: the target uses it once the database is its own."""

    assert _plan(FakeServer()).refusals == []
    elsewhere = FakeServer()
    elsewhere.owners["DATABASE:mip_app_state"] = APP_ROLE  # the handover does not move it
    assert _plan(elsewhere).refusals == ["target_needs_schema_usage"]


def test_the_usage_check_reads_the_grants_jobs_own_schema_predicate() -> None:
    source = (REPO / "jobs" / "lakebase_migration_grants.py").read_text(encoding="utf-8")
    flattened = re.sub(r"\s+", " ", source)
    assert " AND ".join(acl.NON_SYSTEM_SCHEMA_PREDICATE) in flattened
    assert all(clause in acl.SCHEMA_USAGE_SQL for clause in acl.NON_SYSTEM_SCHEMA_PREDICATE)


# -- exact revert --------------------------------------------------------------


def test_apply_revert_and_a_new_plan_are_clean(tmp_path: Path) -> None:
    server = FakeServer()
    before = _plan(server)
    owners, grants = dict(server.owners), copy.deepcopy(server.acl)
    journal = _apply(server, tmp_path / "j.json")
    assert journal.entry(SCHEMA)["revert_grants"] == [f'GRANT CREATE ON SCHEMA "mip_app" TO "{DEPLOYER}"']
    assert _revert(server, tmp_path / "j.json")
    assert server.owners == owners and server.acl == grants
    after = _plan(server)
    assert after.refusals == [] and after.sha256 == before.sha256
    schema_revert = next(statements for statements in server.transactions if f'ALTER SCHEMA "mip_app" OWNER TO "{LEGACY}"' in statements)
    assert schema_revert == [*sql.LOCK_BOUNDS, f'ALTER SCHEMA "mip_app" OWNER TO "{LEGACY}"', *journal.entry(SCHEMA)["revert_grants"], "COMMIT"]


def test_without_the_restore_a_re_plan_refuses_as_s3_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: the inverse alone drops the target's CREATE and the next plan refuses."""

    server = FakeServer()
    monkeypatch.setattr(handover, "restore_statements", lambda entry: [entry["revert_statement"]])
    _apply(server, tmp_path / "j.json")
    assert _revert(server, tmp_path / "j.json")
    assert _plan(server).refusals == ["target_needs_schema_create"]


APPROVALS = "TABLE:mip_app.approvals"


@pytest.mark.parametrize(
    "entries",
    [
        {(APP_ROLE, DEPLOYER, "SELECT"): False},
        {(DEPLOYER, APP_ROLE, "SELECT"): False},
        {(DEPLOYER, LEGACY, "SELECT"): True},
        {},
    ],
    ids=("target_is_a_grantor", "granted_by_another_role", "with_grant_option", "column_grant"),
)
def test_a_target_entry_the_revert_cannot_restore_exactly_refuses(entries: dict[tuple[str, str, str], bool]) -> None:
    server = FakeServer()
    server.acl[APPROVALS] = {**owner_entries(APPROVALS, LEGACY), **entries}
    if not entries:
        server.column_grants.append((APPROVALS, "decided_by", DEPLOYER, LEGACY, "SELECT"))
    assert _plan(server).refusals == ["target_grant_unrevertible"]


def test_a_legacy_granted_table_entry_is_restored_exactly(tmp_path: Path) -> None:
    server = FakeServer()
    server.acl[APPROVALS] = {**owner_entries(APPROVALS, LEGACY), (DEPLOYER, LEGACY, "SELECT"): False, (DEPLOYER, LEGACY, "INSERT"): False}
    before = copy.deepcopy(server.acl)
    journal = _apply(server, tmp_path / "j.json")
    assert journal.entry(APPROVALS)["revert_grants"] == [f'GRANT INSERT, SELECT ON TABLE "mip_app"."approvals" TO "{DEPLOYER}"']
    assert _revert(server, tmp_path / "j.json")
    assert server.acl == before


def test_the_apply_still_sends_no_grant_and_the_revert_only_journaled_restores(tmp_path: Path) -> None:
    server = FakeServer()
    journal = _apply(server, tmp_path / "j.json")
    applied = [text for statements in server.transactions for text in statements]
    assert not [text for text in applied if text.startswith(("GRANT", "REVOKE"))]
    count = len(server.transactions)
    assert _revert(server, tmp_path / "j.json")
    reverted = [text for statements in server.transactions[count:] for text in statements if text.startswith(("GRANT", "REVOKE"))]
    assert reverted == [text for entry in journal.entries for text in entry["revert_grants"]] and reverted


# -- retire-check --------------------------------------------------------------


def test_retire_check_reports_the_extension_and_default_acl_row_the_handover_leaves(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer()
    _apply(server, tmp_path / "j.json")
    workspace = jobs_workspace({}, [], me=LEGACY)
    session = SimpleNamespace(workspace=lambda: workspace, read_session=server.read_session)
    capsys.readouterr()
    assert handover.main(["--retire-check", "--report-out", str(tmp_path / "r.json")], session=lambda *a: session) == 2
    captured = capsys.readouterr()
    assert "legacy_role_residue (default_acl, owns_extension)" in captured.err and "legacy_identity_in_use" not in captured.err
    assert LEGACY not in captured.out + captured.err
    residue = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["lakebase_residue"]
    assert residue == {category: int(category in {"default_acl", "owns_extension"}) for category in roles.RESIDUE_CATEGORIES}
    server.extensions["pgcrypto"] = DEPLOYER
    server.default_acl_roles.clear()
    assert handover.main(["--retire-check"], session=lambda *a: session) == 0


def test_retire_check_counts_what_the_legacy_role_still_owns_holds_or_granted() -> None:
    server = FakeServer(other_owned=1, memberships_granted=2)
    with server.read_session() as cursor:
        before = roles.legacy_residue(cursor, legacy=LEGACY)
    assert before["owns_object"] == 11 + 1 and before["granted_membership"] == 2
    with server.read_session() as cursor:
        assert roles.legacy_residue(cursor, legacy="absent-role@example.com") == dict.fromkeys(roles.RESIDUE_CATEGORIES, 0)


# -- preconditions -------------------------------------------------------------


def _preconditions(server: FakeServer, *, target: str = DEPLOYER) -> tuple[list[str], list[str]]:
    with server.read_session() as cursor:
        return roles.preconditions(cursor, target=target)


def test_preconditions_print_the_owners_l_a_sql_in_order_for_a_missing_target() -> None:
    server = FakeServer(roles={LEGACY, APP_ROLE}, extensions={}, schemas={"public": "pg_database_owner", 'odd"schema': LEGACY})
    server.acl[SCHEMA].pop((DEPLOYER, LEGACY, "CREATE"))
    lines, refusals = _preconditions(server)
    assert refusals == []
    statements = [line for line in lines if not line.startswith("--")]
    assert statements == [
        "CREATE EXTENSION IF NOT EXISTS databricks_auth;",
        f"SELECT databricks_create_role('{DEPLOYER}', 'SERVICE_PRINCIPAL');",
        f'GRANT "{DEPLOYER}" TO "{LEGACY}" WITH INHERIT TRUE, SET TRUE;',
        f'GRANT CREATE ON SCHEMA "mip_app" TO "{DEPLOYER}";',
        f'GRANT USAGE ON SCHEMA "odd""schema" TO "{DEPLOYER}";',
    ]
    assert lines[-1].endswith("lakebase_handover --plan")


def test_preconditions_leave_out_what_holds_and_print_nothing_when_all_do() -> None:
    lines, refusals = _preconditions(FakeServer())
    assert refusals == [] and [line for line in lines if not line.startswith("--")] == []
    assert "-- Every precondition holds." in lines


def test_preconditions_refuse_a_target_created_without_the_admin_edge() -> None:
    """A role created through the REST API or the UI first: the legacy owner cannot self-grant SET and INHERIT."""

    server = FakeServer(admin_edge=False, settable=False, inherits=False)
    lines, refusals = _preconditions(server)
    assert refusals == ["target_role_without_admin_edge"]
    assert not any(line.startswith(("SELECT databricks_create_role", f'GRANT "{DEPLOYER}"')) for line in lines)


def test_preconditions_quote_role_names_and_literals() -> None:
    target = "o'dd\"role"
    server = FakeServer(roles={LEGACY}, extensions={})
    lines, refusals = _preconditions(server, target=target)
    assert refusals == ["target_identity_unreviewed"], "the deployer is a service principal's application id"
    assert "SELECT databricks_create_role('o''dd\"role', 'SERVICE_PRINCIPAL');" in lines
    assert 'GRANT "o\'dd""role" TO "legacy-owner@example.com" WITH INHERIT TRUE, SET TRUE;' in lines


def test_the_preconditions_mode_reads_only_and_writes_the_private_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeServer(roles={LEGACY, APP_ROLE})
    session = SimpleNamespace(read_session=server.read_session, target=DEPLOYER)
    assert handover.main(["--preconditions", "--report-out", str(tmp_path / "p.json")], session=lambda *a: session) == 0
    assert server.transactions == [], "read-only: no write transaction"
    out = capsys.readouterr().out
    assert f"SELECT databricks_create_role('{DEPLOYER}', 'SERVICE_PRINCIPAL');" in out
    assert json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))["refusals"] == []
    assert "connect" not in inspect.getsource(roles.run_preconditions)


# -- rehearse ------------------------------------------------------------------


def _rehearse(server: FakeServer) -> rehearsal.RehearsalReport:
    return rehearsal.rehearse(
        server.connect,
        legacy=LEGACY,
        target=DEPLOYER,
        hold_s=8.0,
        concurrent=lambda _connect, hold: VirtualConcurrent(server, hold),
        clock=server.clock,
        sleep=server.sleep,
    )


def test_the_rehearsal_passes_with_no_manual_grant_and_takes_its_own_back() -> None:
    server = throwaway()
    key = f"SCHEMA:{rehearsal.REHEARSAL_SCHEMA}"
    report = _rehearse(server)
    assert report.passed and report.missing_preconditions == ()
    assert report.granted_schema_create and report.schema_create_revoked
    grant = f'GRANT CREATE ON SCHEMA "{rehearsal.REHEARSAL_SCHEMA}" TO "{DEPLOYER}"'
    revoke = f'REVOKE CREATE ON SCHEMA "{rehearsal.REHEARSAL_SCHEMA}" FROM "{DEPLOYER}"'
    assert [text for text in server.statements if text.startswith(("GRANT", "REVOKE"))] == [grant, revoke]
    assert server.statements.index(rehearsal.SETUP[0]) < server.statements.index(grant)
    assert not server.has_privilege(DEPLOYER, key, "CREATE") and server.acl.get(key) == {}


def test_a_rehearsal_whose_target_already_holds_create_grants_and_revokes_nothing() -> None:
    server = throwaway()
    server.schemas[rehearsal.REHEARSAL_SCHEMA] = LEGACY
    server.acl[f"SCHEMA:{rehearsal.REHEARSAL_SCHEMA}"] = {(DEPLOYER, LEGACY, "CREATE"): False}
    report = _rehearse(server)
    assert report.passed and not report.granted_schema_create
    assert not [text for text in server.statements if text.startswith(("GRANT", "REVOKE"))]
    assert server.has_privilege(DEPLOYER, f"SCHEMA:{rehearsal.REHEARSAL_SCHEMA}", "CREATE")


def test_a_failed_rehearsal_still_takes_its_grant_back() -> None:
    server = throwaway()
    server.fail_on.add(f"TABLE:{rehearsal.REHEARSAL_SCHEMA}.rehearsal_target")
    with pytest.raises(Exception, match="injected"):
        _rehearse(server)
    assert server.statements[-2].startswith("REVOKE CREATE ON SCHEMA")
    assert not server.has_privilege(DEPLOYER, f"SCHEMA:{rehearsal.REHEARSAL_SCHEMA}", "CREATE")
