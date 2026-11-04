"""The handover's read-only verify helpers: effective privileges, the next organic run (M2-PR8).

The stale App-triggered jobs are never run-now. ``privileges`` proves a named
principal still reaches every lifecycle target (a grant, ownership, or a
workspace-visible membership of the owning group; an invisible membership
refuses), and ``watch-next-run`` waits, bounded, for the next run that starts
on its own. Neither makes a mutating call: the fakes have none.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.handover_jobs_fake import PagedRunsApi, jobs_workspace, run, runs_workspace
from tools.clients import handover_verify as verify

LEGACY = "legacy-owner@example.com"
GROUP = "acme-data-owners"


class _Workspace:
    def __init__(self, *, owners: dict[str, str], grants: dict[tuple[str, str], list[str]], members: list[str] | None) -> None:
        self.calls: list[str] = []
        owner = lambda name: SimpleNamespace(owner=owners[name])  # noqa: E731
        self.catalogs = SimpleNamespace(get=owner)
        self.schemas = SimpleNamespace(get=owner)
        self.tables = SimpleNamespace(get=owner)

        def effective(kind: str, name: str, *, principal: str) -> Any:
            self.calls.append(f"get_effective {kind}")
            privileges = [SimpleNamespace(privilege=SimpleNamespace(value=p)) for p in grants.get((name, principal), [])]
            return SimpleNamespace(privilege_assignments=[SimpleNamespace(principal=principal, privileges=privileges)])

        self.grants = SimpleNamespace(get_effective=effective)
        groups = [] if members is None else [SimpleNamespace(display_name=GROUP, members=[SimpleNamespace(value=m) for m in members])]
        self.groups = SimpleNamespace(list=lambda filter: groups)  # noqa: A006
        self.users = SimpleNamespace(list=lambda filter: [SimpleNamespace(user_name=LEGACY, id="legacy-id")])
        self.service_principals = SimpleNamespace(list=lambda filter: [])


def _targets() -> list[verify.Target]:
    return [verify.Target.parse(text, catalog="mip") for text in verify.LIFECYCLE_TARGETS]


def _owners(owner: str) -> dict[str, str]:
    return {target.full_name: owner for target in _targets()}


def test_the_default_lifecycle_targets_render_with_the_governed_catalog() -> None:
    names = [target.full_name for target in _targets()]
    assert names == ["mip", "mip.gold", "mip.gold.borrower_360", "mip.gold.borrower_lifecycle_state", "mip.gold.funnel_snapshot_daily"]
    with pytest.raises(ValueError, match="KIND:FULL_NAME"):
        verify.Target.parse("VOLUME:mip.gold.x:READ_VOLUME", catalog="mip")


def test_ownership_a_grant_or_a_visible_group_membership_reaches_every_target() -> None:
    direct = verify.privilege_findings(_Workspace(owners=_owners(LEGACY), grants={}, members=[]), principals=[LEGACY], targets=_targets())
    assert {finding.verdict for finding in direct} == {"owner"}
    member = verify.privilege_findings(_Workspace(owners=_owners(GROUP), grants={}, members=["legacy-id"]), principals=[LEGACY], targets=_targets())
    assert {finding.verdict for finding in member} == {"owner_group_member"}
    grants = {(target.full_name, LEGACY): sorted(target.required) for target in _targets()}
    granted = verify.privilege_findings(_Workspace(owners=_owners(GROUP), grants=grants, members=[]), principals=[LEGACY], targets=_targets())
    assert {finding.verdict for finding in granted} == {"privileged"}


def test_a_missing_privilege_or_an_invisible_membership_refuses() -> None:
    missing = verify.privilege_findings(_Workspace(owners=_owners(GROUP), grants={}, members=["someone-else"]), principals=[LEGACY], targets=_targets())
    assert {finding.verdict for finding in missing} == {"missing"}
    assert next(f for f in missing if f.target == "mip.gold.borrower_lifecycle_state").missing == ("MODIFY", "SELECT")
    unseen = verify.privilege_findings(_Workspace(owners=_owners(GROUP), grants={}, members=None), principals=[LEGACY], targets=_targets())
    assert {finding.verdict for finding in unseen} == {"membership_unproven"}


def test_the_privileges_cli_prints_counts_never_principals(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    workspace = _Workspace(owners=_owners(GROUP), grants={}, members=None)
    rc = verify.main(["privileges", "--principal", LEGACY], workspace_factory=lambda: workspace, catalog=lambda: "mip")
    out = capsys.readouterr()
    assert rc == 2 and '"membership_unproven": 5' in out.out
    assert LEGACY not in out.out + out.err and "borrower" not in out.out + out.err


def _runs(*rows: tuple[int, str, str]) -> list[dict[str, Any]]:
    return [run(run_id, life, result) for run_id, life, result in rows]


def _watch(sequence: list[list[dict[str, Any]]], timeout_s: float = 300.0) -> tuple[str, str]:
    """The real ``JobsAPI`` pager over ``PagedRunsApi``; each poll's sleep moves to the next listing."""

    now = [0.0]
    api = PagedRunsApi(sequence[0])
    step = [0]

    def sleep(seconds: float) -> None:
        now[0] += seconds
        step[0] += 1
        api.runs = sequence[min(step[0], len(sequence) - 1)]

    return verify.watch_next_run(runs_workspace(api), job_id=7, timeout_s=timeout_s, interval_s=30.0, clock=lambda: now[0], sleep=sleep)


def test_watch_next_run_reports_the_next_runs_result_never_an_older_one() -> None:
    old = (10, "TERMINATED", "FAILED")
    sequence = [_runs(old), _runs(old), _runs(old, (11, "RUNNING", "")), _runs(old, (11, "TERMINATED", "SUCCESS"))]
    assert _watch(sequence) == ("success", "SUCCESS")
    failed = [_runs(old), _runs(old, (11, "TERMINATED", "FAILED"))]
    assert _watch(failed) == ("failed", "FAILED")


def test_watch_next_run_is_bounded() -> None:
    assert _watch([_runs((10, "TERMINATED", "SUCCESS"))], timeout_s=90.0) == ("timeout", "")
    with pytest.raises(ValueError, match="bounded"):
        _watch([[]], timeout_s=13 * 3600.0)


def test_watch_next_run_reads_one_page_per_poll_however_long_the_history() -> None:
    """The SDK pager walks every page; the watch asks for one: the baseline plus one per poll."""

    history = [run(run_id) for run_id in range(1, 301)]
    api = PagedRunsApi(history)
    now = [0.0]
    code = verify.watch_next_run(
        runs_workspace(api),
        job_id=7,
        timeout_s=60.0,
        interval_s=30.0,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert code == ("timeout", "")
    assert api.requests == 1 + 3, "one baseline page, then one page at 0, 30 and 60 s"
    assert len(list(runs_workspace(PagedRunsApi(history)).jobs.list_runs(job_id=7, limit=5))) == 300, "positive control: the pager walks the whole history"


def test_retire_check_judges_each_job_by_its_effective_run_as_never_its_creator() -> None:
    """``jobs.list`` yields ``BaseJob`` (no run_as): the run_as is read by id, the creator never counts."""

    workspace = jobs_workspace(
        {
            # Created by the deployer, runs as the legacy identity: blocks.
            3: {"creator_user_name": "deployer-sp-app-id", "run_as_user_name": LEGACY, "settings": {"run_as": {"user_name": LEGACY}}},
            # Created by the legacy identity, moved to the jobs SP in place: does not block.
            4: {"creator_user_name": LEGACY, "run_as_user_name": "jobs-sp-app-id", "settings": {"run_as": {"service_principal_name": "jobs-sp-app-id"}}},
            # Only the run_as setting is readable: it decides.
            5: {"creator_user_name": "deployer-sp-app-id", "settings": {"run_as": {"user_name": LEGACY}}},
            # Nothing readable: blocks, never assumed.
            6: {"creator_user_name": LEGACY},
        },
        pipelines=[
            {"pipeline_id": "p", "run_as_user_name": "", "creator_user_name": "deployer-sp-app-id"},
            {"pipeline_id": "q", "run_as_user_name": "jobs-sp-app-id", "creator_user_name": LEGACY},
            {"pipeline_id": "r", "creator_user_name": LEGACY},
        ],
        pipeline_details={"p": {"run_as_user_name": LEGACY}, "r": {}},
        me=LEGACY,
    )
    assert not hasattr(workspace.jobs.list()[0], "run_as_user_name"), "the listing type carries no run_as"
    assert verify.retire_blockers(workspace, legacy_identity=LEGACY.upper()) == [
        "job:3",
        "job:5",
        "job:6:run_as_unreadable",
        "pipeline:p",
        "pipeline:r:run_as_unreadable",
    ]
    assert "jobs.get 4" in workspace.reads and "pipelines.get q" not in workspace.reads
    assert verify.main(["retire-check"], workspace_factory=lambda: workspace, catalog=lambda: "mip") == 2


def test_retire_check_passes_when_nothing_runs_as_the_legacy_identity() -> None:
    workspace = jobs_workspace(
        {4: {"creator_user_name": LEGACY, "run_as_user_name": "jobs-sp-app-id"}},
        pipelines=[{"pipeline_id": "q", "run_as_user_name": "jobs-sp-app-id", "creator_user_name": LEGACY}],
        me=LEGACY,
    )
    assert verify.retire_blockers(workspace, legacy_identity=LEGACY) == []
    assert verify.main(["retire-check"], workspace_factory=lambda: workspace, catalog=lambda: "mip") == 0
    nameless = jobs_workspace({}, me="")
    assert verify.main(["retire-check"], workspace_factory=lambda: nameless, catalog=lambda: "mip") == 2
