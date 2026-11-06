#!/usr/bin/env python3
"""The governed data phase's job runner: digest, idempotency token, signed ledger (M1-PR6).

``./scripts/deploy.sh --phase data`` runs the data jobs only through this
tool, and only from ``scripts/lib/deploy_governed_phases_lifecycle.sh``. It never
calls the Apps API and never cancels a run.

* **plan** (read-only, before the lease): each job is the one this deployer's
  bundle state binds (``tools/databricks/bundle_state.py``); every job of its
  ``run_job_task`` closure and every pipeline it starts is bound there too,
  and each was deployed by this deployer's bundle (``deployment.kind`` BUNDLE,
  its metadata file under the client target's root). The **definition
  digest** is the sha256 of the closure's bound ids, live job settings and
  pipeline specs (``job_phase_definition``). It also classifies the closure (``touches_gold``: fail closed, any
  task not provably silver, ref or FRED-only touches gold or the UC
  functions) and admits the phase's budget (``phase_budget``). It writes the
  data plan, a local file.
* **begin** (under the lease): open a ledger generation
  (``job_phase_ledger``), or resume the open one. An open generation whose
  entry ran under another definition or job id is **stale**: only
  ``--ack-supersede``, the sha256 of its reset plan (printed by the refusal,
  purpose ``data_ledger_supersede``), retires it. One that must run again
  though nothing is stale (a smoke that fails after every job succeeded, a run
  no longer readable) is retired only by ``--ack-rerun``, the sha256 of its
  rerun plan (printed on every resume, purpose ``data_ledger_rerun``). Neither
  ack satisfies the other or retires a generation while any of its runs is
  active, and a resume refuses while an entry outside its plan still runs. An
  entry whose bound job was deleted out of band is ``absent``
  (``job_phase_activity``): not running, and named in the supersede plan.
* **run** (under the lease): recompute the definition digest (it must equal
  the plan's), then re-attach to a running run, skip a succeeded one, or run
  the next attempt of a failed one, with the token
  ``mip-data-<sha256(client, job, definition, generation, attempt)>`` written
  to the ledger before the run-now. A killed phase therefore re-attaches: a run
  id in the ledger is polled, and a token journaled without a run id is
  re-sent, which the Jobs API answers with the run it already started. Every
  run it reads must be a run of the planned job id, and no run-now is sent
  while another entry of the generation still runs. It waits until the run
  ends or ``MIP_JOB_DEADLINE_EPOCH`` minus a reserve: exit 0 succeeded or
  skipped, 1 the run failed, 4 still running (re-run the phase to re-attach),
  2 refused.
* **close** (under the lease): every planned job succeeded and no other entry
  still runs; the generation closes, and the next data phase opens a new one.
* **quiet** (read-only): a release's (or a no-phase run's) refusal, under its
  lease, while a job a data phase left at its deadline still runs; a deleted
  job is not running, so the ledger never refuses the release that rebinds it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.databricks import bundle_state, phase_budget
from tools.databricks import job_phase_ledger as ledger
from tools.databricks.job_phase_activity import activity, job_present, outcome
from tools.databricks.job_phase_definition import (
    PLAN_SCHEMA,
    Refused,
    build_plan,
    canonical_json,
    enum_value,
    live_definitions,
    profile_install,
)

REPO = Path(__file__).resolve().parents[2]

SUPERSEDE_PURPOSE = "data_ledger_supersede"
ACK_SUPERSEDE_ENV = "MIP_ACK_DATA_LEDGER_SUPERSEDE"
RERUN_PURPOSE = "data_ledger_rerun"
ACK_RERUN_ENV = "MIP_ACK_DATA_LEDGER_RERUN"
POLL_SECONDS = 30
# The deadline reserve: the reconcile, the verify, the smoke and the close.
WAIT_RESERVE_SECONDS = 10 * 60
MAX_READ_FAILURES = 5
EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_STILL_RUNNING = 4


def token(*, client_key: str, job_key: str, definition_sha256: str, generation: str, attempt: int) -> str:
    material = "\0".join((client_key, job_key, definition_sha256, generation, str(attempt)))
    return "mip-data-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]


def supersede_digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(b"mip-data-ledger-supersede\0" + canonical_json(body)).hexdigest()


def rerun_digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(b"mip-data-ledger-rerun\0" + canonical_json(body)).hexdigest()


def rerun_body(app_name: str, current: Mapping[str, Any]) -> dict[str, Any]:
    """The rerun plan of an open generation: every entry it would discard, exactly."""

    return {
        "purpose": RERUN_PURPOSE,
        "app_name": app_name,
        "generation": current["generation"],
        "jobs": {
            key: {field: entry[field] for field in ("job_id", "definition_sha256", "attempt", "status", "run_id")}
            for key, entry in sorted(current["jobs"].items())
        },
    }


@dataclass(frozen=True)
class Context:
    """One invocation's dependencies: the client, the held lease and the clock."""

    workspace: Any
    app_name: str
    lease_id: str
    source_git_sha: str
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.time


def _held(ctx: Context) -> None:
    from tools.databricks.app_deployment_lease import assert_held

    assert_held(ctx.workspace, app_name=ctx.app_name, lease_id=ctx.lease_id, source_git_sha=ctx.source_git_sha)


def _write(ctx: Context, expected: dict[str, Any] | None, record: dict[str, Any]) -> dict[str, Any]:
    return ledger.write(
        ctx.workspace,
        app_name=ctx.app_name,
        expected=expected,
        record=record,
        lease_id=ctx.lease_id,
        source_git_sha=ctx.source_git_sha,
        assert_lease_held=lambda: _held(ctx),
    )


def _record(ctx: Context, current: dict[str, Any], key: str, entry: dict[str, Any]) -> dict[str, Any]:
    return _write(ctx, current, {**current, "jobs": {**current["jobs"], key: entry}})


def _still_running(ctx: Context, current: Mapping[str, Any], keys: Any) -> list[str]:
    return [key for key in sorted(keys) if activity(ctx.workspace, current["jobs"][key]) == "running"]


def _jobs(keys: Sequence[str]) -> str:
    return ", ".join(f"jobs.{key}" for key in keys)


def _retire(ctx: Context, current: dict[str, Any], fresh: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Supersede the open generation by an exact ack, never while any of its runs is still active."""

    busy = _still_running(ctx, current, current["jobs"])
    if busy:
        raise Refused(f"{reason}: {_jobs(busy)} of the open generation is still active; wait for it to end (it is never cancelled)")
    superseded = _write(ctx, current, {**current, "state": "superseded"})
    return _write(ctx, superseded, fresh)


def _assert_run_of(run: Any, entry: Mapping[str, Any], key: str) -> None:
    """A run the ledger names must be a run of the planned (bound) job id."""

    if str(getattr(run, "job_id", "") or "") != str(entry["job_id"]):
        raise Refused(f"the run the ledger names for jobs.{key} is not a run of its bound job; nothing was run")


def begin(ctx: Context, plan: Mapping[str, Any], *, ack: str, rerun_ack: str = "") -> dict[str, Any]:
    """Open a generation, resume the open one, or retire it only by the exact ack of its purpose."""

    current = ledger.read(ctx.workspace, app_name=ctx.app_name)
    fresh = ledger.new_generation(
        app_name=ctx.app_name, client_key=str(plan["client_key"]), source_git_sha=ctx.source_git_sha
    )
    if ledger.state_of(current) != "open":
        return _write(ctx, current, fresh)
    assert current is not None
    planned = {item["key"]: item for item in plan["jobs"]}
    stale = {
        key: entry
        for key, entry in sorted(current["jobs"].items())
        if key in planned
        and (
            entry["definition_sha256"] != planned[key]["definition_sha256"]
            or entry["job_id"] != str(planned[key]["job_id"])
        )
    }
    if not stale:
        if rerun_ack:
            digest = rerun_digest(rerun_body(ctx.app_name, current))
            if rerun_ack != digest:
                raise Refused(f"ledger_rerun: {ACK_RERUN_ENV} is not the open generation's rerun plan; it is now "
                              f"{digest}: review the generation, then re-run with it exported for one run")
            return _retire(ctx, current, fresh, reason="ledger_rerun")
        outside = _still_running(ctx, current, set(current["jobs"]) - set(planned))
        if outside:
            raise Refused(f"{_jobs(outside)} of the open generation is still running outside this phase's plan; "
                          "wait for it to end, or re-run --phase data with it planned to re-attach")
        return current
    body = {
        "purpose": SUPERSEDE_PURPOSE,
        "app_name": ctx.app_name,
        "generation": current["generation"],
        "stale": {
            key: {
                "status": entry["status"],
                "run_id": entry["run_id"],
                "ledger_definition_sha256": entry["definition_sha256"],
                "live_definition_sha256": planned[key]["definition_sha256"],
                "ledger_job_id": entry["job_id"],
                # False: the ledger's job was deleted, so its entry is judged by absence (the ack says so).
                "ledger_job_present": job_present(ctx.workspace, entry["job_id"]),
                "live_job_id": str(planned[key]["job_id"]),
            }
            for key, entry in stale.items()
        },
    }
    digest = supersede_digest(body)
    if ack != digest:
        raise Refused(
            f"stale_definition: the open data-phase generation ran {', '.join(stale)} under another "
            f"definition; review it, then re-run with {ACK_SUPERSEDE_ENV}={digest} exported for one run"
        )
    return _retire(ctx, current, fresh, reason="stale_definition")


def start_run(ctx: Context, entry: Mapping[str, Any]) -> str:
    """The data phase's only run-now: the journaled token, so a resend returns the run it started."""

    waiter = ctx.workspace.jobs.run_now(job_id=int(entry["job_id"]), idempotency_token=str(entry["token"]))
    return str(waiter.run_id)


def run_job(ctx: Context, plan: Mapping[str, Any], planned: Mapping[str, Any], *, deadline: int) -> tuple[int, str]:
    """Re-attach, skip or run the next attempt of one planned job; wait for its end."""

    current = ledger.read(ctx.workspace, app_name=ctx.app_name)
    if ledger.state_of(current) != "open":
        raise Refused("no open data-phase generation; begin did not run under this lease")
    assert current is not None
    key = str(planned["key"])
    entry = current["jobs"].get(key)
    if entry is not None and (
        entry["definition_sha256"] != planned["definition_sha256"] or entry["job_id"] != str(planned["job_id"])
    ):
        raise Refused(f"stale_definition: {key} ran under another definition or job id in this generation")
    if entry is not None and entry["status"] == "succeeded":
        return 0, "skipped (succeeded in this generation)"
    if entry is not None and entry["status"] == "running":
        try:
            run = ctx.workspace.jobs.get_run(int(entry["run_id"]))
        except Exception as exc:  # noqa: BLE001 - the class only; the ledger keeps the run id
            raise Refused(
                f"the run of {key} could not be read ({type(exc).__name__}); re-run --phase data to "
                f"re-attach, or, if it stays unreadable, retire the generation with {ACK_RERUN_ENV}"
            ) from None
        _assert_run_of(run, entry, key)
        state = outcome(run)
        if state == "running":
            return _wait(ctx, current, key, entry, deadline=deadline, label="re-attached")
        entry = {**entry, "status": state}
        current = _record(ctx, current, key, entry)
        if state == "succeeded":
            return 0, "skipped (succeeded while no runner watched)"
    # One job at a time: silver never refreshes under a running gold, nor gold under a running silver.
    busy = _still_running(ctx, current, set(current["jobs"]) - {key})
    if busy:
        raise Refused(f"{_jobs(busy)} of this generation is still running; {key} starts only after it ends "
                      "(re-run --phase data to re-attach to it)")
    if entry is None or entry["status"] == "failed":
        attempt = 1 if entry is None else int(entry["attempt"]) + 1
        entry = {
            "job_id": str(planned["job_id"]),
            "definition_sha256": str(planned["definition_sha256"]),
            "attempt": attempt,
            "token": token(
                client_key=str(plan["client_key"]),
                job_key=key,
                definition_sha256=str(planned["definition_sha256"]),
                generation=current["generation"],
                attempt=attempt,
            ),
            "run_id": "",
            "status": "submitted",
            "result": "",
        }
        current = _record(ctx, current, key, entry)
    # A token journaled without its run id is re-sent: the Jobs API returns the run it started.
    entry = {**entry, "run_id": start_run(ctx, entry), "status": "running"}
    current = _record(ctx, current, key, entry)
    return _wait(ctx, current, key, entry, deadline=deadline, label=f"attempt {entry['attempt']}")


def _wait(
    ctx: Context, current: dict[str, Any], key: str, entry: dict[str, Any], *, deadline: int, label: str
) -> tuple[int, str]:
    failures = 0
    while True:
        try:
            run = ctx.workspace.jobs.get_run(int(entry["run_id"]))
            failures = 0
        except Exception as exc:  # noqa: BLE001 - a transient read; the ledger keeps the run id
            failures += 1
            if failures >= MAX_READ_FAILURES:
                raise Refused(f"the run of {key} could not be read ({type(exc).__name__}); re-run to re-attach") from None
            ctx.sleep(POLL_SECONDS)
            continue
        _assert_run_of(run, entry, key)
        state = outcome(run)
        if state != "running":
            result = f"{enum_value(run.state.life_cycle_state)}/{enum_value(run.state.result_state)}"
            _record(ctx, current, key, {**entry, "status": state, "result": result})
            if state == "succeeded":
                return 0, f"succeeded ({label})"
            return EXIT_FAILED, f"failed ({label}, {result})"
        if ctx.now() + POLL_SECONDS >= deadline - WAIT_RESERVE_SECONDS:
            return EXIT_STILL_RUNNING, f"still running at the deadline ({label}); re-run --phase data to re-attach"
        ctx.sleep(POLL_SECONDS)


def close(ctx: Context, plan: Mapping[str, Any]) -> dict[str, Any]:
    current = ledger.read(ctx.workspace, app_name=ctx.app_name)
    if ledger.state_of(current) != "open":
        raise Refused("no open data-phase generation to close")
    assert current is not None
    for item in plan["jobs"]:
        entry = current["jobs"].get(item["key"])
        if (
            entry is None
            or entry["status"] != "succeeded"
            or (entry["definition_sha256"], entry["job_id"]) != (item["definition_sha256"], str(item["job_id"]))
        ):
            raise Refused(f"jobs.{item['key']} has not succeeded under its planned definition")
    busy = _still_running(ctx, current, set(current["jobs"]) - {item["key"] for item in plan["jobs"]})
    if busy:
        raise Refused(f"{_jobs(busy)} of this generation is still running; it closes only when none does")
    return _write(ctx, current, {**current, "state": "closed"})


def quiet(ctx: Context) -> tuple[list[str], list[str]]:
    """Read-only: an open generation's entries whose runs are still going, and those whose job was deleted."""

    current = ledger.read(ctx.workspace, app_name=ctx.app_name)
    if ledger.state_of(current) != "open":
        return [], []
    assert current is not None
    states = {key: activity(ctx.workspace, entry) for key, entry in sorted(current["jobs"].items())}
    return [key for key, s in states.items() if s == "running"], [key for key, s in states.items() if s == "absent"]


def _load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise Refused("the data plan is not a reviewed data plan")
    return plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--app-name", required=True)
    plan.add_argument("--skip-silver", choices=("0", "1"), default="0")
    plan.add_argument("--deadline-epoch", default="")
    plan.add_argument("--out", type=Path, required=True)
    sub.add_parser("quiet").add_argument("--app-name", required=True)
    for name in ("begin", "run", "close"):
        mode = sub.add_parser(name)
        mode.add_argument("--plan", type=Path, required=True)
        mode.add_argument("--lease-id", required=True)
        mode.add_argument("--source-git-sha", required=True)
    sub.choices["begin"].add_argument("--ack-supersede", default="")
    sub.choices["begin"].add_argument("--ack-rerun", default="")
    sub.choices["run"].add_argument("--job-key", required=True, choices=phase_budget.DATA_JOBS)
    sub.choices["run"].add_argument("--deadline-epoch", default="")
    sub.choices["run"].add_argument("--out-env", type=Path, required=True)
    return parser


def _default_client() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def _dispatch(args: argparse.Namespace, workspace: Any, environ: Mapping[str, str], env_file: Path, **clock: Any) -> int:
    if args.command == "plan":
        deployer, client_key = profile_install(environ, env_file)
        skip = args.skip_silver == "1"
        plan = build_plan(
            workspace, deployer=deployer, client_key=client_key, app_name=args.app_name,
            jobs=phase_budget.data_jobs(skip_silver=skip), skip_silver=skip,
        )
        phase_budget.require_admitted(plan["budget"], args.deadline_epoch, now=time.time())
        args.out.write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        args.out.chmod(0o600)
        for item in plan["jobs"]:
            print(
                f"[job-phase] plan: jobs.{item['key']} definition {item['definition_sha256'][:12]} "
                f"({len(item['closure'])} job(s), {len(item['pipelines'])} pipeline(s), touches gold: "
                f"{'yes' if item['touches_gold'] else 'no'})"
            )
        print(f"  {phase_budget.describe(plan['budget'], args.deadline_epoch, now=time.time())}")
        return 0
    if args.command == "quiet":
        busy, absent = quiet(Context(workspace, args.app_name, "", "", **clock))
        if busy:
            raise Refused(f"{_jobs(busy)} of the open data-phase generation is still running; wait for it, or re-run --phase data to re-attach")
        if absent:
            print(f"[job-phase] {_jobs(absent)} of the open data-phase generation names a deleted job with no active run; "
                  f"once a release rebinds it, --phase data retires the generation only by the {ACK_SUPERSEDE_ENV} it prints")
        print("[job-phase] no data-phase job is running")
        return 0
    plan = _load_plan(args.plan)
    ctx = Context(workspace, str(plan["app_name"]), args.lease_id, args.source_git_sha, **clock)
    if args.command == "begin":
        record = begin(ctx, plan, ack=args.ack_supersede.strip(), rerun_ack=args.ack_rerun.strip())
        print(f"[job-phase] generation {record['generation'][:8]} open ({len(record['jobs'])} recorded run(s))")
        if record["jobs"]:
            print(
                "  a succeeded run skips; to retire this generation and rerun every job, review it, then "
                f"export {ACK_RERUN_ENV}={rerun_digest(rerun_body(ctx.app_name, record))} for one run"
            )
        return 0
    if args.command == "close":
        close(ctx, plan)
        print("[job-phase] generation closed: every planned job succeeded")
        return 0
    deadline = phase_budget.parse_deadline(args.deadline_epoch)
    if deadline is None:
        raise Refused("run needs MIP_JOB_DEADLINE_EPOCH; a governed data phase always has one")
    planned = next(item for item in plan["jobs"] if item["key"] == args.job_key)
    # The classification first, so the shell reconciles after a refusal too (fail closed).
    args.out_env.write_text(f"JOB_PHASE_TOUCHES_GOLD={1 if planned['touches_gold'] else 0}\n", encoding="utf-8")
    deployer, client_key = profile_install(environ, env_file)
    (live,) = live_definitions(workspace, deployer=deployer, client_key=client_key, jobs=(args.job_key,))
    if (live.job_id, live.sha256) != (planned["job_id"], planned["definition_sha256"]):
        raise Refused(f"jobs.{args.job_key} changed since the data plan; re-plan the data phase")
    rc, label = run_job(ctx, plan, planned, deadline=deadline)
    with args.out_env.open("a", encoding="utf-8") as out:
        out.write(f"JOB_PHASE_OUTCOME={label.split()[0]}\n")
    print(f"[job-phase] jobs.{args.job_key}: {label}")
    return rc


def main(
    argv: Sequence[str] | None = None,
    *,
    client: Any = None,
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = None,
    **clock: Any,
) -> int:
    args = _parser().parse_args(argv)
    from tools.clients.errors import ProfileRefusal

    try:
        workspace = client if client is not None else _default_client()
        return _dispatch(args, workspace, os.environ if environ is None else environ, env_file or REPO / ".env.local", **clock)
    except (Refused, phase_budget.PhaseBudgetRefused, bundle_state.BundleStateError) as exc:
        print(f"[job-phase] refused: {exc}; nothing was run", file=sys.stderr)
        return EXIT_REFUSED
    except ProfileRefusal as refusal:
        print("[job-phase] client profile refused:", file=sys.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # the class only: an SDK message can carry a host or an id
        print(f"[job-phase] refused: {type(exc).__name__}; the ledger keeps every journaled run", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
