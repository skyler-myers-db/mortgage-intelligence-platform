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
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.fake_lakebase import APP_ROLE, DEPLOYER, LEGACY, FakeServer, VirtualConcurrent
from tools.clients import handover_journal as journals
from tools.clients import lakebase_handover as handover
from tools.clients import lakebase_handover_rehearse as rehearsal
from tools.clients import lakebase_handover_sql as sql
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
        ({"caller": DEPLOYER}, "target_is_legacy"),
    ],
)
def test_preconditions_refuse_before_any_change(tmp_path: Path, change: dict[str, Any], code: str) -> None:
    server = FakeServer(**change)
    assert code in _plan(server).refusals
    with pytest.raises(sql.LakebaseHandoverRefused, match="refuses"):
        _apply(server, tmp_path / "j.json")
    assert server.transactions == [] and not (tmp_path / "j.json").exists()


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


def test_the_wr5_window_applies_exactly_the_exhausted_remainder(tmp_path: Path) -> None:
    server = FakeServer()
    server.hold(CAMPAIGNS, 3600.0)
    journal = _apply(server, tmp_path / "j.json")
    remainder = handover.remainder_sha256(journal)
    with pytest.raises(sql.LakebaseHandoverRefused, match="not the exhausted handover"):
        handover.window_open(journal, window_sha256="w" * 64, handover_sha256="0" * 64)
    handover.window_open(journal, window_sha256="w" * 64, handover_sha256=remainder)
    with pytest.raises(sql.LakebaseHandoverRefused, match="no WR5 window is open"):
        handover.in_window(server.read_session, server.connect, journal, window_sha256="x" * 64, clock=_clock(server))
    server.held_until.clear()  # the window library stopped the App: no App transaction holds a lock
    assert handover.in_window(server.read_session, server.connect, journal, window_sha256="w" * 64, clock=_clock(server))
    handover.window_close(journal)
    assert journal.status == "applied" and journal.counts()["applied"] == len(journal.entries)
    assert server.owners[CAMPAIGNS] == DEPLOYER


def test_retire_check_refuses_while_a_job_runs_as_the_legacy_identity() -> None:
    jobs = [SimpleNamespace(job_id=1, run_as_user_name=LEGACY), SimpleNamespace(job_id=2, run_as_user_name=DEPLOYER)]
    pipelines = [SimpleNamespace(pipeline_id="p-1", run_as_user_name="", creator_user_name=LEGACY.upper())]
    workspace = SimpleNamespace(jobs=SimpleNamespace(list=lambda: jobs), pipelines=SimpleNamespace(list_pipelines=lambda: pipelines))
    assert handover.retire_blockers(workspace, legacy_identity=LEGACY) == ["job:1", "pipeline:p-1"]
    jobs[0].run_as_user_name = DEPLOYER
    pipelines.clear()
    assert handover.retire_blockers(workspace, legacy_identity=LEGACY) == []


def test_the_s3_rehearsal_passes_on_the_fake_and_fails_without_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeServer()
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
    assert report.owner_after_change == DEPLOYER and report.owner_after_revert == LEGACY
    unbounded = FakeServer()
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


def test_the_rehearsal_never_runs_on_the_installs_own_instance(capsys: pytest.CaptureFixture[str]) -> None:
    session = SimpleNamespace(live_instance="mip-app-state")
    argv = ["--rehearse", "--rehearse-instance", "mip-app-state", "--rehearse-database", "scratch"]
    assert handover.main(argv, session=lambda *a: session) == 2
    assert "rehearse_live_instance" in capsys.readouterr().err
