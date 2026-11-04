"""Read-only checks around the ownership handovers (M2-PR8, cutover steps 7 to 13).

The stale App-triggered jobs keep running as the legacy identity until
release, so the handover is verified without running them (they are never
``run-now``):

  python -m tools.clients.handover_verify privileges --principal P [--target T ...]
  python -m tools.clients.handover_verify watch-next-run --job-id N [--timeout-seconds S]
  python -m tools.clients.handover_verify retire-check [--legacy-identity X]

* ``privileges``: each named principal's effective privileges
  (``grants.get_effective``) on every lifecycle target, or ownership of it,
  directly or through a workspace-visible membership of the owning group (the
  interim membership of ``<prefix>-data-owners``). The default targets are
  the App-triggered lifecycle job's (jobs/sync_lifecycle_state.py and the
  funnel snapshot it runs); ``--target KIND:FULL_NAME:PRIV+PRIV`` replaces
  them. A membership the workspace cannot see refuses; it is never assumed.
* ``watch-next-run``: wait, bounded, for the next run of one job that starts
  after the call, and report its result. It never starts, cancels or repairs
  a run.
* ``retire-check``: the gate before the owner removes the legacy identity's
  Lakebase role by hand: it refuses while any job or pipeline runs as it, or
  while one's run_as cannot be read (never judged by its creator).

Printed lines carry codes and counts; principals, objects and run ids go only
to ``--report-out`` (mode 0600). Exit codes: 0 pass, 2 refused, 3 the watched
run did not succeed, 4 the watch timed out.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from tools.clients.handover_journal import write_private

# KIND:{catalog}.schema.name:PRIV+PRIV, rendered with the governed catalog.
LIFECYCLE_TARGETS = (
    "CATALOG:{catalog}:USE_CATALOG",
    "SCHEMA:{catalog}.gold:USE_SCHEMA",
    "TABLE:{catalog}.gold.borrower_360:SELECT",
    "TABLE:{catalog}.gold.borrower_lifecycle_state:SELECT+MODIFY",
    "TABLE:{catalog}.gold.funnel_snapshot_daily:SELECT+MODIFY",
)
OWNER_GETTERS: dict[str, Callable[[Any, str], object]] = {
    "CATALOG": lambda ws, name: ws.catalogs.get(name),
    "SCHEMA": lambda ws, name: ws.schemas.get(name),
    "TABLE": lambda ws, name: ws.tables.get(name),
}
TERMINAL = frozenset({"TERMINATED", "SKIPPED", "INTERNAL_ERROR"})
MAX_WATCH_S = 12 * 3600
_PREFIX = "[mip-handover-verify]"


def _text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().split(".")[-1]


@dataclass(frozen=True)
class Target:
    kind: str
    full_name: str
    required: frozenset[str]

    @classmethod
    def parse(cls, text: str, *, catalog: str) -> Target:
        parts = text.format(catalog=catalog).split(":")
        if len(parts) != 3 or parts[0] not in OWNER_GETTERS or not parts[1] or not parts[2]:
            raise ValueError("a target is KIND:FULL_NAME:PRIV+PRIV with KIND CATALOG, SCHEMA or TABLE")
        return cls(parts[0], parts[1], frozenset(parts[2].split("+")))


@dataclass(frozen=True)
class Finding:
    principal: str
    target: str
    verdict: str  # owner, owner_group_member, privileged, missing, membership_unproven
    missing: tuple[str, ...] = ()


def _members(workspace: Any, group: str) -> set[str] | None:
    """The workspace-visible member ids of ``group``; None when it does not resolve once."""

    groups = [item for item in workspace.groups.list(filter=f'displayName eq "{group}"') if _text(getattr(item, "display_name", "")) == group]
    if len(groups) != 1:
        return None
    return {str(getattr(member, "value", "") or "") for member in getattr(groups[0], "members", None) or []}


def _principal_id(workspace: Any, principal: str) -> str:
    for listing, attribute, field in (
        (workspace.users.list, "userName", "user_name"),
        (workspace.service_principals.list, "applicationId", "application_id"),
    ):
        for item in listing(filter=f'{attribute} eq "{principal}"'):
            if str(getattr(item, field, "") or "") == principal:
                return str(getattr(item, "id", "") or "")
    return ""


def effective(workspace: Any, target: Target, principal: str) -> frozenset[str]:
    result = workspace.grants.get_effective(target.kind, target.full_name, principal=principal)
    found: set[str] = set()
    for assignment in getattr(result, "privilege_assignments", None) or []:
        if str(getattr(assignment, "principal", "") or "") != principal:
            continue
        for item in getattr(assignment, "privileges", None) or []:
            found.add(_text(getattr(item, "privilege", item)).upper())
    return frozenset(found)


def privilege_findings(workspace: Any, *, principals: Iterable[str], targets: Iterable[Target]) -> list[Finding]:
    findings: list[Finding] = []
    for target in targets:
        owner = str(getattr(OWNER_GETTERS[target.kind](workspace, target.full_name), "owner", "") or "")
        members = None
        for principal in principals:
            if owner == principal:
                findings.append(Finding(principal, target.full_name, "owner"))
                continue
            privileges = effective(workspace, target, principal)
            if "ALL_PRIVILEGES" in privileges or target.required <= privileges:
                findings.append(Finding(principal, target.full_name, "privileged"))
                continue
            members = _members(workspace, owner) if members is None else members
            principal_id = _principal_id(workspace, principal)
            if members is not None and principal_id and principal_id in members:
                findings.append(Finding(principal, target.full_name, "owner_group_member"))
            elif members is None:
                findings.append(Finding(principal, target.full_name, "membership_unproven", tuple(sorted(target.required - privileges))))
            else:
                findings.append(Finding(principal, target.full_name, "missing", tuple(sorted(target.required - privileges))))
    return findings


def _run_state(run: object) -> tuple[str, str]:
    state = getattr(run, "state", None)
    return _text(getattr(state, "life_cycle_state", None)).upper(), _text(getattr(state, "result_state", None)).upper()


def _newest_runs(workspace: Any, *, job_id: int, count: int) -> list[Any]:
    """The job's ``count`` newest runs: one page, one request.

    ``jobs.list_runs`` follows ``next_page_token`` to the end of the job's
    history (``limit`` is only the page size), so the iterator is cut at
    ``count`` and never asks for a second page.
    """

    return list(islice(workspace.jobs.list_runs(job_id=job_id, limit=count), count))


def watch_next_run(
    workspace: Any,
    *,
    job_id: int,
    timeout_s: float,
    interval_s: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    """(code, result state) of the job's next run after now: success, failed or timeout.

    Bounded in calls as in time: one request for the baseline and one per poll.
    """

    if not 0 < timeout_s <= MAX_WATCH_S or interval_s <= 0:
        raise ValueError("a watch is bounded: 1 second to 12 hours, polled at a positive interval")
    baseline = max((int(getattr(run, "run_id", 0) or 0) for run in _newest_runs(workspace, job_id=job_id, count=1)), default=0)
    deadline = clock() + timeout_s
    while True:
        runs = [run for run in _newest_runs(workspace, job_id=job_id, count=5) if int(getattr(run, "run_id", 0) or 0) > baseline]
        if runs:
            newest_first = sorted(runs, key=lambda run: int(getattr(run, "run_id", 0) or 0))
            life_cycle, result = _run_state(newest_first[0])
            if life_cycle in TERMINAL:
                return ("success" if result == "SUCCESS" else "failed"), result
        if clock() >= deadline:
            return "timeout", ""
        sleep(interval_s)


def _run_as(detail: object) -> str:
    """The identity a job or pipeline runs as: its effective ``run_as_user_name``, else its run_as setting.

    Never the creator: a job updated in place keeps its creator while its
    run_as moves, and one created by someone else can run as anybody.
    """

    effective_name = str(getattr(detail, "run_as_user_name", "") or "").strip()
    if effective_name:
        return effective_name
    setting = getattr(detail, "run_as", None) or getattr(getattr(detail, "settings", None), "run_as", None)
    return str(getattr(setting, "user_name", "") or getattr(setting, "service_principal_name", "") or "").strip()


def retire_blockers(workspace: Any, *, legacy_identity: str) -> list[str]:
    """Read-only: every job and pipeline that runs as the legacy identity, or whose run_as is unreadable.

    ``jobs.list`` returns ``BaseJob``, which carries no run_as, so each job is
    read once by id (``jobs.get``: the run_as setting, else the IS_OWNER); a
    pipeline's listing carries its run_as, and ``pipelines.get`` fills a blank.
    An identity that cannot be read blocks (``kind:id:run_as_unreadable``).
    """

    folded = legacy_identity.strip().casefold()
    if not folded:
        raise ValueError("retire-check needs the legacy identity")
    found: list[str] = []
    for job in workspace.jobs.list():
        job_id = getattr(job, "job_id", None)
        run_as = _run_as(workspace.jobs.get(job_id))
        if not run_as:
            found.append(f"job:{job_id}:run_as_unreadable")
        elif run_as.casefold() == folded:
            found.append(f"job:{job_id}")
    for pipeline in workspace.pipelines.list_pipelines():
        pipeline_id = getattr(pipeline, "pipeline_id", "")
        run_as = _run_as(pipeline) or _run_as(workspace.pipelines.get(pipeline_id))
        if not run_as:
            found.append(f"pipeline:{pipeline_id}:run_as_unreadable")
        elif run_as.casefold() == folded:
            found.append(f"pipeline:{pipeline_id}")
    return sorted(found)


def _workspace() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def _catalog() -> str:
    import os

    from tools.clients.environment import from_local, resolve

    repo = Path(__file__).resolve().parents[2]
    return resolve(from_local(os.environ, repo / ".env.local")).default_catalog


def main(argv: list[str] | None = None, *, workspace_factory: Callable[[], Any] = _workspace, catalog: Callable[[], str] = _catalog) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    privileges = sub.add_parser("privileges")
    privileges.add_argument("--principal", action="append", required=True)
    privileges.add_argument("--target", action="append", default=[])
    watch = sub.add_parser("watch-next-run")
    watch.add_argument("--job-id", type=int, required=True)
    watch.add_argument("--timeout-seconds", type=float, default=7200.0)
    watch.add_argument("--interval-seconds", type=float, default=30.0)
    retire = sub.add_parser("retire-check")
    retire.add_argument("--legacy-identity", default="")
    for mode in (privileges, watch, retire):
        mode.add_argument("--report-out", type=Path)
    args = parser.parse_args(argv)
    workspace = workspace_factory()
    if args.command == "privileges":
        targets = [Target.parse(text, catalog=catalog()) for text in (args.target or LIFECYCLE_TARGETS)]
        findings = privilege_findings(workspace, principals=args.principal, targets=targets)
        report: dict[str, Any] = {"findings": [asdict(finding) for finding in findings]}
        bad = [finding for finding in findings if finding.verdict in {"missing", "membership_unproven"}]
        counts = {verdict: sum(f.verdict == verdict for f in findings) for verdict in sorted({f.verdict for f in findings})}
        print(f"{_PREFIX} privileges: {json.dumps(counts, sort_keys=True)}")
        rc = 2 if bad else 0
    elif args.command == "watch-next-run":
        code, result = watch_next_run(workspace, job_id=args.job_id, timeout_s=args.timeout_seconds, interval_s=args.interval_seconds)
        report = {"code": code, "result_state": result}
        print(f"{_PREFIX} watch-next-run: {code} {result}".rstrip())
        rc = {"success": 0, "failed": 3, "timeout": 4}[code]
    else:
        legacy = (args.legacy_identity or str(getattr(workspace.current_user.me(), "user_name", "") or "")).strip()
        if not legacy:
            print(f"{_PREFIX} REFUSED: the legacy identity is unknown; pass --legacy-identity", file=sys.stderr)
            return 2
        blockers = retire_blockers(workspace, legacy_identity=legacy)
        report = {"runs_as_legacy": blockers}
        print(f"{_PREFIX} retire-check: {len(blockers)} job(s) or pipeline(s) still run as the legacy identity")
        rc = 2 if blockers else 0
    if args.report_out is not None:
        write_private(args.report_out, report)
    if rc == 2:
        print(f"{_PREFIX} REFUSED: see the private report", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
