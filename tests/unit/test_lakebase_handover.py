"""The Lakebase ownership handover: lock-bounded, journaled, exhausting into WR5 or revert (M2-PR8).

Against ``tests/fixtures/fake_lakebase.FakeServer`` on a virtual clock: a
lock timeout retries and then exhausts into the window-or-revert choice; a
long App transaction never blocks an App query longer than ``lock_timeout``
(and, as a positive control, would without the bound); every write is one
owner change between the two ``SET LOCAL`` bounds; a wrong or stale digest
changes nothing; revert is the exact inverse, also from a partial journal.
"""

from __future__ import annotations

import ast
import contextlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.fake_lakebase import (
    APP_ROLE,
    DEPLOYER,
    LEGACY,
    FakeServer,
    VirtualConcurrent,
    throwaway,
)
from tests.fixtures.handover_jobs_fake import jobs_workspace
from tools.clients import handover_journal as journals
from tools.clients import lakebase_handover as handover
from tools.clients import lakebase_handover_rehearse as rehearsal
from tools.clients import lakebase_handover_sql as sql
from tools.clients.handover_verify import retire_blockers
from tools.clients.lakebase_handover_sql import LOCK_BOUNDS, BudgetExhausted, LockBudget, PgObject

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "fixtures" / "lakebase_handover_plan_golden.json"
DESTRUCTIVE = re.compile(r"\b(DROP|DELETE|TRUNCATE|REASSIGN|GRANT|REVOKE)\b")
CAMPAIGNS = "TABLE:mip_app.campaigns"


def _clock(server: FakeServer) -> handover.Clock:
    return handover.Clock(now=server.clock, sleep=server.sleep)


def _plan(server: FakeServer) -> handover.LakebasePlan:
    return handover.plan(server.read_session, target=DEPLOYER)


def _apply(server: FakeServer, path: Path, *, ack: str | None = None, budget: LockBudget = sql.DEFAULT_BUDGET) -> journals.Journal:
    return handover.apply(
        server.read_session,
        server.connect,
        target=DEPLOYER,
        ack=_plan(server).sha256 if ack is None else ack,
        journal_path=path,
        budget=budget,
        clock=_clock(server),
    )


def _writes(server: FakeServer) -> list[list[str]]:
    return [statements for statements in server.transactions]


def test_the_plan_matches_its_golden_statements() -> None:
    current = _plan(FakeServer())
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert {**current.document, "plan_sha256": current.sha256} == golden


def test_the_plan_reads_only_and_leaves_other_owners_alone() -> None:
    server = FakeServer()
    current = _plan(server)
    assert server.transactions == [], "the plan opened no write transaction"
    keys = [entry["key"] for entry in current.document["changes"]]
    assert "TABLE:mip_app.app_owned_scratch" not in keys
    assert current.document["left_alone"] == {"other_owner": 1}
    assert keys[-2:] == ["SCHEMA:mip_app", "DATABASE:mip_app_state"], "schema and database last"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"roles": {LEGACY, APP_ROLE}}, "target_role_missing"),
        ({"settable": False}, "target_role_not_settable"),
        ({"inherits": False}, "caller_cannot_revert"),
        ({"caller": DEPLOYER}, "target_is_legacy"),
        ({"authority": {"schema_create": False, "database_create": True, "createdb": True}}, "target_needs_schema_create"),
        ({"authority": {"schema_create": True, "database_create": False, "createdb": True}}, "caller_needs_database_create"),
        ({"authority": {"schema_create": True, "database_create": True, "createdb": False}}, "caller_needs_createdb"),
    ],
)
def test_preconditions_refuse_before_any_change(tmp_path: Path, change: dict[str, Any], code: str) -> None:
    server = FakeServer(**change)
    assert code in _plan(server).refusals
    with pytest.raises(sql.LakebaseHandoverRefused, match="refuses"):
        _apply(server, tmp_path / "j.json")
    assert server.transactions == [] and not (tmp_path / "j.json").exists()


def test_a_set_only_membership_would_apply_and_then_fail_every_revert(tmp_path: Path) -> None:
    """Why the plan reads USAGE: PostgreSQL 16 lets SET give an object away, only INHERIT take it back.

    The fake checks ALTER ... OWNER TO as PostgreSQL does (the caller owns
    the object or inherits its owner, and can SET to the new owner); on a
    local PostgreSQL 16.15 a ``WITH INHERIT FALSE, SET TRUE`` membership
    applied and then reverted nothing (42501, "must be owner of database").
    """

    server = FakeServer()
    _apply(server, tmp_path / "j.json")
    server.inherits = False  # the membership the plan now refuses, after an apply it allowed
    journal = journals.load(tmp_path / "j.json", kind=handover.KIND)
    document, _refusals = handover.revert_plan(server.read_session, journal)
    with pytest.raises(Exception, match="must be owner") as failed:
        handover.revert(server.read_session, server.connect, journal, ack=journals.plan_sha256(document, domain=handover.REVERT_DOMAIN), clock=_clock(server))
    assert getattr(failed.value, "sqlstate", "") == "42501"
    assert "caller_cannot_revert" in _plan(FakeServer(inherits=False)).refusals


def test_an_unreviewed_object_refuses_the_plan() -> None:
    server = FakeServer()
    server.objects.append(PgObject("FOREIGN TABLE", "mip_app", "mirror", "", LEGACY))
    server.owners["FOREIGN TABLE:mip_app.mirror"] = LEGACY
    assert _plan(server).refusals == ["unreviewed_object"]


@pytest.mark.parametrize("ack", ["", "0" * 64, "stale"])
def test_a_wrong_or_stale_digest_changes_nothing(tmp_path: Path, ack: str) -> None:
    server = FakeServer()
    if ack == "stale":
        ack = _plan(server).sha256
        server.owners["TABLE:mip_app.app_owned_scratch"] = LEGACY
    with pytest.raises(sql.LakebaseHandoverRefused, match="not the acknowledged digest"):
        _apply(server, tmp_path / "j.json", ack=ack)
    assert server.transactions == [] and not (tmp_path / "j.json").exists()


def test_every_write_is_one_owner_change_between_the_two_bounds(tmp_path: Path) -> None:
    server = FakeServer()
    journal = _apply(server, tmp_path / "j.json")
    assert journal.status == "applied"
    planned = [entry["statement"] for entry in journal.entries]
    assert [statements[2] for statements in server.transactions] == planned
    for statements in server.transactions:
        assert statements[:2] == list(LOCK_BOUNDS) and statements[-1] == "COMMIT" and len(statements) == 4
    assert LOCK_BOUNDS == ("SET LOCAL lock_timeout = '3s'", "SET LOCAL statement_timeout = '30s'")
    executed = [text for statements in server.transactions for text in statements[:-1]]
    assert sql.statements_are_owner_changes(executed) == []
    assert not any(DESTRUCTIVE.search(text) for text in executed)
    assert all(owner == DEPLOYER for key, owner in server.owners.items() if key != "TABLE:mip_app.app_owned_scratch")


def test_no_handover_module_carries_a_destructive_statement() -> None:
    """Every string literal outside docstrings, in every handover module: no DROP, DELETE or TRUNCATE."""

    modules = ("lakebase_handover.py", "lakebase_handover_sql.py", "lakebase_handover_rehearse.py", "handover_uc_plan.py", "handover_uc_ownership.py")
    for name in modules:
        tree = ast.parse((REPO / "tools" / "clients" / name).read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
        ]
        assert literals, name
        assert [text for text in literals if DESTRUCTIVE.search(text)] == [], name
    assert DESTRUCTIVE.search("DROP ROLE x"), "positive control"


def test_a_lock_timeout_retries_and_never_blocks_the_app_past_lock_timeout(tmp_path: Path) -> None:
    server = FakeServer()
    server.hold(CAMPAIGNS, 7.0)
    journal = _apply(server, tmp_path / "j.json")
    assert journal.status == "applied"
    assert journal.entry(CAMPAIGNS)["lock_attempts"] == 2
    assert server.exclusive_waits[0] == (0.0, 3.0), "the first wait ended at lock_timeout"
    assert server.max_app_block() <= 3.0
    rolled_back = [statements for statements in server.transactions if statements[-1] == "ROLLBACK"]
    assert len(rolled_back) == 1


def test_without_the_lock_bound_the_app_would_wait_as_long_as_the_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: the fake really blocks an unbounded ALTER for the holder's whole run."""

    server = FakeServer()
    server.hold(CAMPAIGNS, 7.0)
    monkeypatch.setattr(sql, "LOCK_BOUNDS", ())
    connection = server.connect()
    target = next(entry for entry in _plan(server).document["changes"] if entry["key"] == CAMPAIGNS)
    sql.run_bounded(connection, target["statement"], budget=LockBudget(), deadline=600.0, clock=server.clock, sleep=server.sleep)
    assert server.max_app_block() == 7.0 > 3.0


def test_the_budget_exhausts_into_the_window_or_revert_choice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeServer()
    server.hold(CAMPAIGNS, 3600.0)
    digest = _plan(server).sha256
    session = SimpleNamespace(read_session=server.read_session, connect=server.connect, workspace=None, target=DEPLOYER, live_instance="live")
    rc = handover.main(["--apply", "--ack-plan", digest, "--journal", str(tmp_path / "j.json")], session=lambda *a: session, clock=_clock(server))
    assert rc == handover.EXHAUSTED_EXIT
    err = capsys.readouterr().err
    assert "WR5 window" in err and "--revert --journal" in err
    journal = journals.load(tmp_path / "j.json", kind=handover.KIND)
    assert journal.status == "exhausted"
    assert journal.entry(CAMPAIGNS)["state"] == "pending" and journal.entry(CAMPAIGNS)["error"] == "lock_budget"
    waits = [end - start for start, end in server.exclusive_waits]
    assert len(waits) == 10 and max(waits) == 3.0, "ten attempts, each bounded by lock_timeout"
    assert server.owners[CAMPAIGNS] == LEGACY


def test_the_total_budget_bounds_the_whole_run() -> None:
    server = FakeServer()
    server.hold(CAMPAIGNS, 3600.0)
    target = next(entry for entry in _plan(server).document["changes"] if entry["key"] == CAMPAIGNS)
    with pytest.raises(BudgetExhausted):
        sql.run_bounded(server.connect(), target["statement"], budget=LockBudget(total_s=10.0), deadline=10.0, clock=server.clock, sleep=server.sleep)
    assert len(server.exclusive_waits) < 10 and server.now <= 10.0 + 3.0


def _revert(server: FakeServer, path: Path) -> bool:
    journal = journals.load(path, kind=handover.KIND)
    document, refusals = handover.revert_plan(server.read_session, journal)
    assert refusals == []
    ack = journals.plan_sha256(document, domain=handover.REVERT_DOMAIN)
    return handover.revert(server.read_session, server.connect, journal, ack=ack, clock=_clock(server))


def test_revert_is_the_exact_inverse(tmp_path: Path) -> None:
    server = FakeServer()
    before = dict(server.owners)
    _apply(server, tmp_path / "j.json")
    assert server.owners != before
    assert _revert(server, tmp_path / "j.json")
    assert server.owners == before
    assert journals.load(tmp_path / "j.json", kind=handover.KIND).status == "reverted"


def test_revert_from_an_exhausted_journal_restores_every_prior_owner(tmp_path: Path) -> None:
    server = FakeServer()
    before = dict(server.owners)
    server.hold(CAMPAIGNS, 3600.0)
    journal = _apply(server, tmp_path / "j.json")
    assert journal.status == "exhausted" and journal.counts()["applied"] > 0
    server.held_until.clear()
    assert _revert(server, tmp_path / "j.json")
    assert server.owners == before


def test_revert_settles_an_object_already_back_with_its_prior_owner(tmp_path: Path) -> None:
    """An applied object put back by hand is a noop the revert digest names, not a crash."""

    server = FakeServer()
    before = dict(server.owners)
    _apply(server, tmp_path / "j.json")
    server.owners["TABLE:mip_app.approvals"] = LEGACY
    assert _revert(server, tmp_path / "j.json")
    assert server.owners == before
    journal = journals.load(tmp_path / "j.json", kind=handover.KIND)
    assert journal.status == "reverted" and journal.counts()["reverted"] == len(journal.entries)
    assert journal.entry("TABLE:mip_app.approvals")["reverted"] == "noop"


def test_revert_refuses_an_object_re_owned_outside_the_handover(tmp_path: Path) -> None:
    server = FakeServer()
    _apply(server, tmp_path / "j.json")
    server.owners["TABLE:mip_app.approvals"] = APP_ROLE
    after = dict(server.owners)
    journal = journals.load(tmp_path / "j.json", kind=handover.KIND)
    assert handover.revert_plan(server.read_session, journal)[1] == ["owner_changed"]
    with pytest.raises(sql.LakebaseHandoverRefused, match="nothing was reverted"):
        handover.revert(server.read_session, server.connect, journal, ack="0" * 64, clock=_clock(server))
    assert server.owners == after


def test_retire_check_refuses_while_a_job_runs_as_the_legacy_identity(capsys: pytest.CaptureFixture[str]) -> None:
    jobs = {1: {"creator_user_name": DEPLOYER, "run_as_user_name": LEGACY}, 2: {"creator_user_name": LEGACY, "run_as_user_name": DEPLOYER}}
    pipelines = [{"pipeline_id": "p-1", "run_as_user_name": LEGACY.upper(), "creator_user_name": DEPLOYER}]
    workspace = jobs_workspace(jobs, pipelines, me=LEGACY)
    assert retire_blockers(workspace, legacy_identity=LEGACY) == ["job:1", "pipeline:p-1"]
    session = SimpleNamespace(workspace=lambda: workspace)
    assert handover.main(["--retire-check"], session=lambda *a: session) == 2
    assert "legacy_identity_in_use" in capsys.readouterr().err
    jobs[1]["run_as_user_name"] = DEPLOYER
    pipelines.clear()
    assert retire_blockers(workspace, legacy_identity=LEGACY) == []
    assert handover.main(["--retire-check"], session=lambda *a: session) == 0


def test_the_s3_rehearsal_passes_on_the_fake_and_fails_without_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    server = throwaway()
    report = rehearsal.rehearse(
        server.connect,
        legacy=LEGACY,
        target=DEPLOYER,
        hold_s=8.0,
        concurrent=lambda _connect, hold: VirtualConcurrent(server, hold),
        clock=server.clock,
        sleep=server.sleep,
    )
    assert report.passed and report.change_attempts >= 2 and report.max_probe_wait_s <= 3.0
    assert report.probe_samples_during_change >= 1
    assert report.owner_after_change == DEPLOYER and report.owner_after_revert == LEGACY
    unbounded = throwaway()
    monkeypatch.setattr(sql, "LOCK_BOUNDS", ())
    failed = rehearsal.rehearse(
        unbounded.connect,
        legacy=LEGACY,
        target=DEPLOYER,
        hold_s=8.0,
        concurrent=lambda _connect, hold: VirtualConcurrent(unbounded, hold),
        clock=unbounded.clock,
        sleep=unbounded.sleep,
    )
    assert not failed.passed and failed.max_probe_wait_s == 8.0


class _SilentProbe(VirtualConcurrent):
    """The hold runs, but the probe recorded nothing (or only outside the change)."""

    def __init__(self, server: FakeServer, hold_s: float, samples: list[tuple[float, float]]) -> None:
        super().__init__(server, hold_s)
        self.samples = samples

    def join(self) -> list[tuple[float, float]]:
        return list(self.samples)


@pytest.mark.parametrize("samples", [[], [(-5.0, -4.9), (500.0, 500.1)]], ids=["no_sample", "outside_the_change"])
def test_a_rehearsal_whose_probe_saw_no_wait_fails(samples: list[tuple[float, float]]) -> None:
    """No probe query during the change's lock waits is no evidence: the run fails, never passes."""

    server = throwaway()
    report = rehearsal.rehearse(
        server.connect,
        legacy=LEGACY,
        target=DEPLOYER,
        hold_s=8.0,
        concurrent=lambda _connect, hold: _SilentProbe(server, hold, samples),
        clock=server.clock,
        sleep=server.sleep,
    )
    assert report.change_attempts >= 2 and report.owner_after_revert == LEGACY, "the change itself rehearsed fine"
    assert not report.passed and report.probe_samples_during_change == 0


class _LockOnly:
    """A connection whose one statement is the hold's LOCK TABLE."""

    def __init__(self) -> None:
        self.closed = False

    def transaction(self) -> Any:
        return contextlib.nullcontext()

    def cursor(self) -> Any:
        return contextlib.nullcontext(SimpleNamespace(execute=lambda _statement: None, fetchone=lambda: (0,)))

    def close(self) -> None:
        self.closed = True


def test_a_rehearsal_session_that_dies_voids_the_run() -> None:
    """ThreadedConcurrent re-raises a session's failure (by class) instead of returning no samples."""

    def never(*_args: object) -> Any:
        raise ConnectionError("no route to the throwaway")

    holdless = rehearsal.ThreadedConcurrent(never, 0.2)
    with pytest.raises(rehearsal.RehearsalSessionFailed, match=r"hold: ConnectionError") as refused:
        holdless.start()
    assert "no route" not in str(refused.value)
    opened: list[Any] = []

    def hold_then_fail() -> Any:
        if opened:
            raise ConnectionError("the probe cannot connect")
        opened.append(_LockOnly())
        return opened[0]

    probeless = rehearsal.ThreadedConcurrent(hold_then_fail, 0.2)
    with pytest.raises(rehearsal.RehearsalSessionFailed, match=r"probe: ConnectionError"):
        probeless.start()  # raises here when the probe already died, else at join
        probeless.join()
    assert opened[0].closed


def test_the_rehearsal_names_a_missing_precondition_before_any_change() -> None:
    server = FakeServer(objects=[], authority={"schema_create": False, "database_create": True, "createdb": True})
    report = rehearsal.rehearse(
        server.connect,
        legacy=LEGACY,
        target=DEPLOYER,
        hold_s=8.0,
        concurrent=lambda _connect, hold: VirtualConcurrent(server, hold),
        clock=server.clock,
        sleep=server.sleep,
    )
    assert not report.passed and report.missing_preconditions == ("target_needs_schema_create",)
    assert not any(text.startswith("ALTER ") for text in server.statements)


def test_the_rehearsal_never_runs_on_the_installs_own_instance(capsys: pytest.CaptureFixture[str]) -> None:
    session = SimpleNamespace(live_instance="mip-app-state")
    argv = ["--rehearse", "--rehearse-instance", "mip-app-state", "--rehearse-database", "scratch"]
    assert handover.main(argv, session=lambda *a: session) == 2
    assert "rehearse_live_instance" in capsys.readouterr().err


def test_the_rehearsal_names_a_membership_that_cannot_revert() -> None:
    server = FakeServer(objects=[], inherits=False)
    report = rehearsal.rehearse(
        server.connect,
        legacy=LEGACY,
        target=DEPLOYER,
        hold_s=8.0,
        concurrent=lambda _connect, hold: VirtualConcurrent(server, hold),
        clock=server.clock,
        sleep=server.sleep,
    )
    assert not report.passed and report.missing_preconditions == ("caller_cannot_revert",)
    assert not any(text.startswith("ALTER ") for text in server.statements)


def test_the_rehearsal_refuses_an_install_database_before_creating_anything() -> None:
    """A database that already holds mip_app is an install's: nothing is created there."""

    server = FakeServer()
    with pytest.raises(sql.LakebaseHandoverRefused, match="holds mip_app"):
        rehearsal.rehearse(
            server.connect,
            legacy=LEGACY,
            target=DEPLOYER,
            hold_s=8.0,
            concurrent=lambda _connect, hold: VirtualConcurrent(server, hold),
            clock=server.clock,
            sleep=server.sleep,
        )
    assert server.transactions == [["ROLLBACK"]], "the one transaction read, then rolled back"
    assert rehearsal.INSTALL_SCHEMA_SQL in server.statements
    assert not any(text in rehearsal.SETUP or text.startswith("ALTER ") for text in server.statements)


@pytest.mark.parametrize("override", handover.CONNECTION_OVERRIDES)
@pytest.mark.parametrize(
    "argv",
    [
        ["--plan"],
        ["--window-context"],
        ["--rehearse", "--rehearse-instance", "throwaway-rehearsal", "--rehearse-database", "scratch"],
    ],
)
def test_a_connection_override_refuses_before_the_profile_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], override: str, argv: list[str]
) -> None:
    """LAKEBASE_HOST (and kin) would point every mode at a server the profile does not name."""

    monkeypatch.setattr(handover, "REPO", tmp_path)  # no .env.local is read either way
    for name in handover.CONNECTION_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(override, "live-instance.database.example")
    assert handover.main(argv) == 2
    err = capsys.readouterr().err
    assert f"unset {override}:" in err and "live-instance" not in err
    handover.refuse_connection_overrides({})  # positive control: no override, no refusal


def test_rerunning_a_finished_apply_changes_nothing_and_never_suggests_a_revert(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = FakeServer()
    session = SimpleNamespace(read_session=server.read_session, connect=server.connect, workspace=None, target=DEPLOYER, live_instance="live")
    argv = ["--apply", "--ack-plan", _plan(server).sha256, "--journal", str(tmp_path / "j.json")]
    assert handover.main(argv, session=lambda *a: session, clock=_clock(server)) == 0
    writes = len(server.transactions)
    capsys.readouterr()
    assert handover.main(argv, session=lambda *a: session, clock=_clock(server)) == 2
    err = capsys.readouterr().err
    assert "already exists" in err and "part-way" not in err and "Revert with" not in err
    assert len(server.transactions) == writes
    assert journals.load(tmp_path / "j.json", kind=handover.KIND).status == "applied"


def test_a_failure_part_way_exits_three_and_names_the_revert(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server = FakeServer()
    digest = _plan(server).sha256
    server.fail_on.add("TABLE:mip_app.approvals")
    session = SimpleNamespace(read_session=server.read_session, connect=server.connect, workspace=None, target=DEPLOYER, live_instance="live")
    rc = handover.main(["--apply", "--ack-plan", digest, "--journal", str(tmp_path / "j.json")], session=lambda *a: session, clock=_clock(server))
    assert rc == 3
    err = capsys.readouterr().err
    assert "Revert with --revert --journal" in err and "injected" not in err
    journal = journals.load(tmp_path / "j.json", kind=handover.KIND)
    assert journal.status == "refused" and journal.entry("TABLE:mip_app.approvals")["state"] == "applying"
    server.fail_on.clear()
    before = {key: LEGACY for key in server.owners if key != "TABLE:mip_app.app_owned_scratch"}
    assert _revert(server, tmp_path / "j.json")
    assert all(server.owners[key] == owner for key, owner in before.items())
