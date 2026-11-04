"""WR5's Python half: the window opens and applies only as the planned, acknowledged window (M2-PR8).

Neither ``--window-open`` nor ``--in-window`` takes the caller's word for the
window: each reads the private plan ``plan-wr5`` wrote and needs it passed,
intact, acknowledged for this run (``MIP_ACK_QUIESCE_WINDOW``) and pinning
this journal's remainder; ``--in-window`` also needs the planned App STOPPED
and the plan's PostgreSQL preconditions recomputed. Against
``tests/fixtures/fake_lakebase.FakeServer``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.fake_lakebase import DEPLOYER, LEGACY, FakeServer
from tools.clients import handover_journal as journals
from tools.clients import lakebase_handover as handover
from tools.clients import lakebase_handover_sql as sql
from tools.clients import lakebase_handover_window as window
from tools.databricks import quiesce_window_plan as release
from tools.databricks import quiesce_window_wr5 as wr5

CAMPAIGNS = "TABLE:mip_app.campaigns"
FACTS = wr5.HandoverWindowFacts(
    app_present=True,
    app_id="app-id-PLACEHOLDER",
    app_client_id="app-client-PLACEHOLDER",
    app_scim_id="app-scim-PLACEHOLDER",
    compute_state="ACTIVE",
    active_deployment_id="deployment-serving",
    active_source="/Workspace/Users/legacy-PLACEHOLDER/app-src/release",
    default_source="/Workspace/Users/legacy-PLACEHOLDER/app-src/release",
    handover_status="exhausted",
)


def _clock(server: FakeServer) -> handover.Clock:
    return handover.Clock(now=server.clock, sleep=server.sleep)


def _exhausted(tmp_path: Path) -> tuple[FakeServer, journals.Journal]:
    server = FakeServer()
    server.hold(CAMPAIGNS, 3600.0)
    digest = handover.plan(server.read_session, target=DEPLOYER).sha256
    journal = handover.apply(server.read_session, server.connect, target=DEPLOYER, ack=digest, journal_path=tmp_path / "j.json", clock=_clock(server))
    assert journal.status == "exhausted"
    return server, journal


def _plan(journal: journals.Journal, **change: Any) -> dict[str, Any]:
    pinned = {"handover_sha256": window.remainder_sha256(journal), "remainder": len(window.remainder(journal))}
    facts = replace(FACTS, **{**pinned, **change})
    digest = wr5.window_plan(facts, acknowledgement="")["public"]["window_sha256"]
    return wr5.window_plan(facts, acknowledgement=digest)


def _app(state: str = "STOPPED", app_id: str = FACTS.app_id) -> SimpleNamespace:
    return SimpleNamespace(id=app_id, compute_status=SimpleNamespace(state=SimpleNamespace(value=state)))


def _open(journal: journals.Journal, plan: dict[str, Any], *, ack: str | None = None, window_sha256: str | None = None) -> None:
    digest = window_sha256 if window_sha256 is not None else plan["public"]["window_sha256"]
    window.window_open(
        journal,
        window_sha256=digest,
        handover_sha256=window.remainder_sha256(journal),
        plan=plan,
        acknowledgement=digest if ack is None else ack,
    )


def _in_window(server: FakeServer, journal: journals.Journal, plan: dict[str, Any], *, ack: str | None = None, app: Any = None) -> bool:
    digest = plan["public"]["window_sha256"]
    return window.in_window(
        server.read_session,
        server.connect,
        journal,
        window_sha256=digest,
        plan=plan,
        acknowledgement=digest if ack is None else ack,
        app=_app() if app is None else app,
        clock=_clock(server),
    )


def test_the_wr5_window_applies_exactly_the_exhausted_remainder(tmp_path: Path) -> None:
    server, journal = _exhausted(tmp_path)
    plan = _plan(journal)
    with pytest.raises(sql.LakebaseHandoverRefused, match="not the exhausted handover"):
        window.window_open(journal, window_sha256=plan["public"]["window_sha256"], handover_sha256="0" * 64, plan=plan, acknowledgement=plan["public"]["window_sha256"])
    _open(journal, plan)
    server.held_until.clear()  # the window library stopped the App: no App transaction holds a lock
    assert _in_window(server, journal, plan)
    window.window_close(journal)
    assert journal.status == "applied" and journal.counts()["applied"] == len(journal.entries)
    assert server.owners[CAMPAIGNS] == DEPLOYER


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("ack_absent", "acknowledged for this run"),
        ("ack_mismatch", "acknowledged for this run"),
        ("another_window", "acknowledged for this run"),
        ("refused_plan", "passed, intact"),
        ("tampered_plan", "passed, intact"),
        ("another_remainder", "another remainder"),
        ("not_a_plan", "not a window plan"),
    ],
)
def test_the_window_opens_only_as_the_planned_acknowledged_window(tmp_path: Path, case: str, match: str) -> None:
    _server, journal = _exhausted(tmp_path)
    plan = _plan(journal)
    digest = plan["public"]["window_sha256"]
    kwargs: dict[str, Any] = {}
    if case == "ack_absent":
        kwargs["ack"] = ""
    elif case == "ack_mismatch":
        kwargs["ack"] = "0" * 64
    elif case == "another_window":
        kwargs["window_sha256"] = kwargs["ack"] = "f" * 64
    elif case == "refused_plan":
        plan = {**plan, "public": {**plan["public"], "code": "refused"}}
    elif case == "tampered_plan":
        plan = {**plan, "canonical": {**plan["canonical"], "app_id": "another-app"}}
    elif case == "another_remainder":
        plan = _plan(journal, handover_sha256="h" * 64)
        kwargs["ack"] = kwargs["window_sha256"] = plan["public"]["window_sha256"]
    else:
        plan = {"public": digest}
        kwargs["ack"] = kwargs["window_sha256"] = digest
    with pytest.raises(sql.LakebaseHandoverRefused, match=match):
        _open(journal, plan, **kwargs)
    assert journals.load(journal.path, kind=handover.KIND).status == "exhausted", "nothing was journaled"


@pytest.mark.parametrize(
    ("case", "match"),
    [
        ("app_serving", "not stopped"),
        ("another_app", "not stopped"),
        ("ack_absent", "acknowledged for this run"),
        ("precondition_lost", "preconditions refuse"),
    ],
)
def test_the_in_window_pass_rechecks_the_window_the_app_and_the_preconditions(tmp_path: Path, case: str, match: str) -> None:
    server, journal = _exhausted(tmp_path)
    plan = _plan(journal)
    _open(journal, plan)
    server.held_until.clear()
    kwargs: dict[str, Any] = {}
    if case == "app_serving":
        kwargs["app"] = _app(state="ACTIVE")
    elif case == "another_app":
        kwargs["app"] = _app(app_id="another-app")
    elif case == "ack_absent":
        kwargs["ack"] = ""
    else:
        server.settable = False
    before = len(server.transactions)
    with pytest.raises(sql.LakebaseHandoverRefused, match=match):
        _in_window(server, journal, plan, **kwargs)
    assert len(server.transactions) == before and server.owners[CAMPAIGNS] == LEGACY, "no ALTER was sent"


def _session(server: FakeServer, app: Any) -> SimpleNamespace:
    workspace = SimpleNamespace(apps=SimpleNamespace(get=lambda _name: app))
    return SimpleNamespace(read_session=server.read_session, connect=server.connect, workspace=lambda: workspace, target=DEPLOYER, live_instance="live", app_name="mip-app")


def test_an_unplanned_window_cannot_be_opened_or_applied_from_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The review's bypass: --remainder's digest plus any --window-sha256, no plan-wr5, no ack."""

    server, journal = _exhausted(tmp_path)
    server.held_until.clear()
    session = _session(server, _app(state="ACTIVE"))
    monkeypatch.delenv(release.ACK_ENV, raising=False)
    journal_arg = ["--journal", str(journal.path)]
    assert handover.main(["--remainder", *journal_arg], session=lambda *a: session, clock=_clock(server)) == 0
    remainder = window.remainder_sha256(journal)
    with pytest.raises(SystemExit) as usage:
        handover.main(["--window-open", *journal_arg, "--window-sha256", "not-a-digest", "--handover-sha256", remainder], session=lambda *a: session)
    assert usage.value.code == 2, "the window modes need the plan plan-wr5 wrote"
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps({"public": {"schema": wr5.PUBLIC_SCHEMA, "code": "window", "window_sha256": "not-a-digest"}, "canonical": {}}), encoding="utf-8")
    argv = ["--window-open", *journal_arg, "--window-plan", str(forged), "--window-sha256", "not-a-digest", "--handover-sha256", remainder]
    assert handover.main(argv, session=lambda *a: session, clock=_clock(server)) == 2
    planned = tmp_path / "plan.json"
    plan = _plan(journal)
    planned.write_text(json.dumps(plan), encoding="utf-8")
    digest = plan["public"]["window_sha256"]
    argv = ["--window-open", *journal_arg, "--window-plan", str(planned), "--window-sha256", digest, "--handover-sha256", remainder]
    assert handover.main(argv, session=lambda *a: session, clock=_clock(server)) == 2, "planned but not acknowledged"
    assert journals.load(journal.path, kind=handover.KIND).status == "exhausted"
    monkeypatch.setenv(release.ACK_ENV, digest)
    assert handover.main(argv, session=lambda *a: session, clock=_clock(server)) == 0
    in_window = ["--in-window", *journal_arg, "--window-plan", str(planned), "--window-sha256", digest]
    assert handover.main(in_window, session=lambda *a: session, clock=_clock(server)) == 2, "the App still serves"
    assert server.owners[CAMPAIGNS] == LEGACY
    assert "not stopped" in capsys.readouterr().err
    stopped = _session(server, _app())
    assert handover.main(in_window, session=lambda *a: stopped, clock=_clock(server)) == 0
    assert handover.main(["--window-close", *journal_arg], session=lambda *a: stopped) == 0
    assert journals.load(journal.path, kind=handover.KIND).status == "applied" and server.owners[CAMPAIGNS] == DEPLOYER
