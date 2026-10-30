"""Before the lease, an App a signed first-install journal still owns is deferred, not refused.

An interrupted first install can leave the App created but unbound from
bundle state (a lost create response, a crash between the journal claim and
the bind, or a cleanup that unbound the App and then failed to delete it).
deploy.sh leaves that App and its signed journal for the next lease to
reconcile, so on the retry every non-App entry is in state and only
``apps.mip_app`` plans a create, which collides. The pre-lease gate reads the
journal (read-only) and defers that one collision when the journal reads
``unclaimed`` or ``recover``; anything else, and every pre-apply run, stays
strict.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import bundle_plan_gate as gate
from tools.databricks.bundle_plan_shape import parse_plan

APP_NAME = fx.names_of("apps", fx.APP_KEY)[0]
APP = f"resources.apps.{fx.APP_KEY}"


def _retry() -> dict[str, Any]:
    return fx.with_entries(
        fx.adopted(), {APP: fx.entry("create", value=fx.resolved_name("apps", fx.APP_KEY))}
    )


class _Journal:
    """A journal read that records each call; ``state`` may be an exception to raise."""

    def __init__(self, state: str | Exception) -> None:
        self.state = state
        self.reads: list[str] = []

    def __call__(self, _workspace: Any, app_name: str) -> str:
        self.reads.append(app_name)
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


def _evaluate(
    doc: dict[str, Any],
    journal: Callable[[Any, str], str] | None,
    *,
    position: str = "pre-lease",
    existing: dict[str, set[str]] | None = None,
) -> gate.PlanReport:
    workspace = fx.RecordingWorkspace({"apps": {APP_NAME}} if existing is None else existing)
    report = gate.evaluate(
        parse_plan(fx.dumps(doc)),
        position=position,
        app_key=fx.APP_KEY,
        acknowledgement="",
        workspace_factory=lambda: workspace,
        journal_state=journal,
    )
    assert workspace.mutating_calls() == []
    return report


@pytest.mark.parametrize("state", ("unclaimed", "recover"))
def test_a_journal_owned_app_is_deferred_before_the_lease(state: str) -> None:
    journal = _Journal(state)

    report = _evaluate(_retry(), journal)

    assert report.codes == () and not report.refused
    assert journal.reads == [APP_NAME]
    (deferral,) = report.deferrals
    assert (deferral.code, deferral.resource) == ("journal_owned", "apps.mip_app")
    assert deferral.detail == {"journal_state": state, "names": [APP_NAME]}
    (lookup,) = report.lookups
    assert lookup.outcome == f"journal-owned ({state})"
    public = report.public_summary()
    assert public["deferred"] == [{"code": "journal_owned", "resource": "apps.mip_app"}]
    assert APP_NAME not in json.dumps(public)


def test_without_the_journal_the_same_plan_is_not_adopted() -> None:
    # The reviewer's reproduction: before this change the retry refused here.
    report = _evaluate(_retry(), None)

    assert report.codes == ("not_adopted",)
    assert report.deferrals == ()


@pytest.mark.parametrize(
    ("state", "recorded"),
    (
        ("absent", "absent"),
        ("signed", "signed"),
        ("orphan_claimed", "orphan_claimed"),
        (RuntimeError("journal signature is invalid"), "failed: RuntimeError"),
    ),
    ids=("no-journal", "signed", "orphan", "unreadable"),
)
def test_any_other_journal_state_keeps_the_strict_refusal(
    state: str | Exception, recorded: str
) -> None:
    report = _evaluate(_retry(), _Journal(state))

    assert report.codes == ("not_adopted",) and report.deferrals == ()
    (collision,) = report.refusals[0].detail["collisions"]
    assert collision["journal_state"] == recorded


def test_the_pre_apply_gate_never_reads_the_journal() -> None:
    # Under the lease the recovery has proven the App absent; a create that
    # still collides there is refused whatever the journal says.
    journal = _Journal("recover")

    report = _evaluate(_retry(), journal, position="pre-apply")

    assert report.codes == ("not_adopted",)
    assert journal.reads == []


def test_the_journal_is_read_only_for_the_apps_own_collision() -> None:
    journal = _Journal("recover")

    absent = _evaluate(_retry(), journal, existing={})
    assert absent.codes == () and absent.deferrals == () and journal.reads == []

    stray = fx.with_entries(
        _retry(),
        {"resources.jobs.mip_ref_seed": fx.entry("create", value={"name": "synthetic-job"})},
    )
    report = _evaluate(stray, journal, existing={"apps": {APP_NAME}, "jobs": {"synthetic-job"}})
    # The App is deferred; the stray job is a plain collision, never folded.
    assert report.codes == ("collision",)
    assert report.refusals[0].resource == "jobs.mip_ref_seed"
    assert [d.resource for d in report.deferrals] == ["apps.mip_app"]
    assert journal.reads == [APP_NAME]


def test_the_reader_refuses_an_app_the_deploy_does_not_reconcile() -> None:
    read = gate.first_install_journal_reader("another-app", "scope", "instance")

    report = _evaluate(_retry(), read)

    assert report.codes == ("not_adopted",)
    (collision,) = report.refusals[0].detail["collisions"]
    assert collision["journal_state"] == "failed: ValueError"


# --- the command line -----------------------------------------------------------------


def _main(tmp_path: Path, *extra: str, position: str = "pre-lease") -> int:
    plan = tmp_path / "plan.json"
    plan.write_text(fx.dumps(_retry()), encoding="utf-8")
    return gate.main(
        ["check", "--plan", str(plan), "--position", position, *extra],
        workspace_factory=lambda: fx.RecordingWorkspace({"apps": {APP_NAME}}),
    )


JOURNAL_ARGS = (
    "--first-install-app-name",
    APP_NAME,
    "--rollback-scope",
    "scope",
    "--lakebase-instance",
    "instance",
)


def test_the_cli_prints_the_deferral_and_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def recovery_state(_workspace: Any, **kwargs: Any) -> str:
        calls.append(kwargs)
        return "recover"

    from tools.databricks import app_first_install_journal

    monkeypatch.setattr(app_first_install_journal, "recovery_state", recovery_state)

    assert _main(tmp_path, *JOURNAL_ARGS) == 0
    out = capsys.readouterr().out
    assert f"[mip-plan-gate] DEFERRED journal_owned apps.mip_app (recover): {gate.JOURNAL_OWNED_NOTE}" in out
    assert APP_NAME not in out
    assert calls == [
        {
            "app_name": APP_NAME,
            "rollback_scope": "scope",
            "expected_lakebase_instance": "instance",
        }
    ]


@pytest.mark.parametrize(
    ("extra", "position"),
    ((JOURNAL_ARGS, "pre-apply"), (JOURNAL_ARGS[:2], "pre-lease")),
    ids=("pre-apply", "partial"),
)
def test_the_cli_takes_the_journal_only_whole_and_only_before_the_lease(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
    position: str,
) -> None:
    with pytest.raises(SystemExit) as raised:
        _main(tmp_path, *extra, position=position)

    assert raised.value.code == 2
    assert "first-install journal" in capsys.readouterr().err
