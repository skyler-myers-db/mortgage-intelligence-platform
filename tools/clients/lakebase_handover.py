"""Hand the App database and its mip_app objects to the deployer's role, lock-bounded (M2-PR8).

Run as the legacy owner, at cutover step 8 (docs/deployment.md, "Ownership
handover"), after the deployer's Postgres role exists by spike S3's L-A
mechanism (``--preconditions`` prints the owner's SQL for it, read-only):

  python -m tools.clients.lakebase_handover --preconditions
  python -m tools.clients.lakebase_handover --plan
  python -m tools.clients.lakebase_handover --apply --ack-plan <sha256> --journal J
  python -m tools.clients.lakebase_handover --revert --journal J [--ack-plan <sha256>]
  python -m tools.clients.lakebase_handover --retire-check
  python -m tools.clients.lakebase_handover --rehearse --rehearse-instance <throwaway> ...

``--plan`` reads in a server-enforced read-only session and prints the plan's
sha256. ``--apply`` needs that exact digest (the owner attestation is
deferred), recomputes the plan immediately before its first change, and moves
one object per transaction under ``lock_timeout='3s'`` and
``statement_timeout='30s'`` (tools/clients/lakebase_handover_sql.py), retrying
a lock timeout at most 10 times per object and 10 minutes in all, journaled
mode 0600 before and after each change.

When the budget runs out the run stops (exit 4) with nothing half-applied and
offers exactly two exits:

* an announced **WR5** window: ``scripts/lakebase_handover_window.sh`` plans
  it read-only, stops the App, applies the rest of this journal inside it
  (``--in-window``), starts the App and proves the start; the stop and start
  are the window library's (tools/databricks/stop_primitive_map.py), never
  this tool's, and both window modes re-read the acknowledged plan
  (tools/clients/lakebase_handover_window.py); or
* ``--revert``: the exact inverse from the journal, by the same lock bounds.

Every mode reaches the profile's instance (``MIP_LAKEBASE_INSTANCE``) only: a
``LAKEBASE_HOST``, ``LAKEBASE_USER``, ``LAKEBASE_PASSWORD`` or ``LAKEBASE_PORT``
override, which the migration connection would prefer, refuses
(``CONNECTION_OVERRIDES``).

The tool never drops, creates or reassigns a role. ``--retire-check`` is the
read-only gate before the owner removes the legacy role by hand: it refuses
while any job or pipeline still runs as the legacy identity, or while the
legacy role still owns an extension, a default-ACL row or any other object,
holds a privilege, or granted a membership (tools/clients/lakebase_handover_roles.py).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from tools.clients import handover_journal as journals
from tools.clients.lakebase_handover_acl import (
    restore_statements,
    schemas_without_usage,
    target_grants,
)
from tools.clients.lakebase_handover_reach import acts_as_database_owner, unreachable_objects
from tools.clients.lakebase_handover_sql import (
    AUTHORITY_SQL,
    DEFAULT_BUDGET,
    INVENTORY_SQL,
    SCHEMA_NAME,
    BudgetExhausted,
    LakebaseHandoverRefused,
    LockBudget,
    owner_statement,
    parse_inventory,
    run_bounded,
    sort_changes,
)

REPO = Path(__file__).resolve().parents[2]
KIND = "lakebase"
SCHEMA = "mip.lakebase-ownership-handover.plan.v1"
DIGEST_DOMAIN = "mip-lakebase-ownership-handover-plan"
REVERT_DOMAIN = "mip-lakebase-ownership-handover-revert"
EXHAUSTED_EXIT = 4
_PREFIX = "[mip-lakebase-handover]"

ReadSession = Callable[[], AbstractContextManager[Any]]


@dataclass
class LakebasePlan:
    document: dict[str, Any]
    refusals: list[str] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        return journals.plan_sha256(self.document, domain=DIGEST_DOMAIN)

    def public(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for entry in self.document["changes"]:
            kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
        return {
            "plan_sha256": self.sha256,
            "changes": len(self.document["changes"]),
            "changes_by_kind": dict(sorted(kinds.items())),
            "left_alone": dict(self.document["left_alone"]),
            "refusals": sorted(self.refusals),
        }


def _inventory(cursor: Any) -> list[Any]:
    cursor.execute(INVENTORY_SQL)
    return parse_inventory(cursor.fetchall())


def compute(cursor: Any, *, target: str) -> LakebasePlan:
    """Read-only: the session's role is the legacy owner; plan each of its objects."""

    cursor.execute(AUTHORITY_SQL, {"target": target, "schema": SCHEMA_NAME})
    row = cursor.fetchone()
    if row is None:
        raise LakebaseHandoverRefused("the authority probe returned no row")
    legacy, target_exists, target_settable = str(row[0]), bool(row[1]), bool(row[2])
    schema_create, database_create, createdb = bool(row[3]), bool(row[4]), bool(row[5])
    target_inherited = bool(row[6])
    refusals: list[str] = []
    if target == legacy:
        refusals.append("target_is_legacy")
    if not target_exists:
        refusals.append("target_role_missing")
    else:
        if not target_settable:
            refusals.append("target_role_not_settable")
        if not target_inherited:
            # The exact inverse needs the target's privileges: refuse before the apply.
            refusals.append("caller_cannot_revert")
    changes, planned, other = [], {}, 0
    for item in _inventory(cursor):
        if item.owner != legacy:
            other += 1
            continue
        try:
            statement = owner_statement(item, target)
        except LakebaseHandoverRefused:
            refusals.append("unreviewed_object")
            continue
        planned[item.key] = item
        changes.append(
            {
                "key": item.key,
                "kind": item.kind,
                "phase": item.phase,
                "prior_owner": legacy,
                "new_owner": target,
                "statement": statement,
                # The exact inverse: the same object back to the legacy role.
                "revert_statement": owner_statement(replace(item, owner=target), legacy),
                "revert_grants": [],
            }
        )
    kinds = {entry["kind"] for entry in changes}
    if kinds - {"SCHEMA", "DATABASE"} and target_exists and not schema_create:
        refusals.append("target_needs_schema_create")
    if "SCHEMA" in kinds and not database_create:
        refusals.append("caller_needs_database_create")
    if "DATABASE" in kinds and not createdb:
        refusals.append("caller_needs_createdb")
    if target_exists:
        # The grants job the target runs next closes public as the database owner (PR #336, round 2) ...
        if not acts_as_database_owner(cursor, legacy=legacy, target=target):
            refusals.append("target_will_not_own_database")
        # ... and revokes in every non-system schema (S3, answer 4).
        refusals.extend("target_needs_schema_usage" for _schema in schemas_without_usage(cursor, target=target))
        # ... and on every object there, which it must hold a privilege on to REVOKE (PR #336 review).
        refusals.extend("target_needs_object_privilege" for _item in unreachable_objects(cursor, legacy=legacy, target=target))
        # The target's own entries the apply merges away, restored by the revert (S3, answer 3b).
        grants = target_grants(cursor, legacy=legacy, target=target, planned=planned)
        refusals.extend("target_grant_unrevertible" for _entry in range(grants.unrevertible))
        for entry in changes:
            entry["revert_grants"] = grants.restores.get(entry["key"], [])
    document = {
        "schema": SCHEMA,
        "legacy_role": legacy,
        "target_role": target,
        "changes": sort_changes(changes),
        "left_alone": {"other_owner": other},
    }
    return LakebasePlan(document, refusals)


def live_owners(cursor: Any) -> dict[str, str]:
    return {item.key: item.owner for item in _inventory(cursor)}


def plan(read_session: ReadSession, *, target: str) -> LakebasePlan:
    with read_session() as cursor:
        return compute(cursor, target=target)


@dataclass(frozen=True)
class Clock:
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep


SYSTEM_CLOCK = Clock()


def _apply_statements(entry: Mapping[str, Any]) -> list[str]:
    return [str(entry["statement"])]


def run_entries(
    connection: Any,
    journal: journals.Journal,
    entries: list[dict[str, Any]],
    *,
    budget: LockBudget,
    clock: Clock,
    statements: Callable[[Mapping[str, Any]], Sequence[str]] = _apply_statements,
    done: str = "applied",
) -> bool:
    """Run each entry's statements, one lock-bounded transaction per entry; False when the budget ran out."""

    deadline = clock.now() + budget.total_s
    for entry in entries:
        key = entry["key"]
        if clock.now() >= deadline:
            return False
        moving = "applying" if done == "applied" else "reverting"
        journal.mark(key, moving, attempts=journal.entry(key)["attempts"] + 1)
        try:
            attempts = run_bounded(
                connection,
                statements(entry),
                budget=budget,
                deadline=deadline,
                clock=clock.now,
                sleep=clock.sleep,
            )
        except BudgetExhausted:
            # Every attempt rolled back: nothing of this object landed.
            journal.mark(key, "pending" if done == "applied" else "reverting", error="lock_budget")
            return False
        journal.mark(key, done, lock_attempts=attempts)
    return True


def apply(
    read_session: ReadSession,
    connect: Callable[[], Any],
    *,
    target: str,
    ack: str,
    journal_path: Path,
    budget: LockBudget = DEFAULT_BUDGET,
    clock: Clock = SYSTEM_CLOCK,
) -> journals.Journal:
    """Recompute, compare with ``ack``, journal, then move each object lock-bounded."""

    journals.refuse_existing(journal_path)
    current = plan(read_session, target=target)
    if current.refusals:
        raise LakebaseHandoverRefused(f"the recomputed plan refuses ({len(current.refusals)}); nothing was changed")
    if current.sha256 != ack:
        raise LakebaseHandoverRefused("the recomputed plan is not the acknowledged digest; nothing was changed")
    journal = journals.create(journal_path, kind=KIND, plan_sha256=current.sha256, entries=current.document["changes"])
    connection = connect()
    try:
        finished = run_entries(connection, journal, journal.entries, budget=budget, clock=clock)
    except Exception:
        journal.set_status("refused")
        raise
    finally:
        connection.close()
    journal.set_status("applied" if finished else "exhausted")
    return journal


def revert_plan(read_session: ReadSession, journal: journals.Journal) -> tuple[dict[str, Any], list[str]]:
    with read_session() as cursor:
        owners = live_owners(cursor)
    moves, noops, refusals = [], [], []
    landed = [entry for entry in journal.entries if entry["state"] in journals.MAY_HAVE_LANDED]
    for entry in sort_changes(landed, reverse=True):
        owner = owners.get(entry["key"])
        if owner == entry["new_owner"]:
            # The inverse and, in its transaction, the target's grants the apply merged away.
            moves.append({"key": entry["key"], "statement": entry["revert_statement"], "grants": list(entry.get("revert_grants") or [])})
        elif owner == entry["prior_owner"]:
            noops.append(entry["key"])
        else:
            refusals.append("owner_changed")
    document = {"plan_sha256": journal.plan_sha256, "moves": moves, "noops": sorted(noops)}
    return document, refusals


def revert(
    read_session: ReadSession,
    connect: Callable[[], Any],
    journal: journals.Journal,
    *,
    ack: str,
    budget: LockBudget = DEFAULT_BUDGET,
    clock: Clock = SYSTEM_CLOCK,
) -> bool:
    """The exact inverse, lock-bounded; False when the budget ran out (re-run resumes)."""

    document, refusals = revert_plan(read_session, journal)
    if refusals:
        raise LakebaseHandoverRefused("an object changed owner outside the handover; nothing was reverted")
    if journals.plan_sha256(document, domain=REVERT_DOMAIN) != ack:
        raise LakebaseHandoverRefused("the recomputed revert plan is not the acknowledged digest; nothing was reverted")
    journal.set_status("reverting")
    for key in document["noops"]:
        journal.mark_unmoved(key)
    moves = [{**journal.entry(move["key"]), "revert_statement": move["statement"], "revert_grants": move["grants"]} for move in document["moves"]]
    connection = connect()
    try:
        finished = run_entries(connection, journal, moves, budget=budget, clock=clock, statements=restore_statements, done="reverted")
    finally:
        connection.close()
    journal.set_status("reverted" if finished else "reverting")
    return finished


def _iter_lines(plan_: LakebasePlan) -> Iterator[str]:
    yield f"{_PREFIX} plan: {json.dumps(plan_.public(), sort_keys=True)}"
    for code in sorted(set(plan_.refusals)):
        yield f"{_PREFIX} REFUSED {code} x{plan_.refusals.count(code)}"


# jobs/lakebase_migration_connection.py prefers these over the named instance
# (LAKEBASE_HOST alone redirects even its SDK path, which still mints the named
# instance's credential), so a set one would point the plan, the apply, the
# window and the rehearsal at a server the profile does not name. Refused.
CONNECTION_OVERRIDES = ("LAKEBASE_HOST", "LAKEBASE_USER", "LAKEBASE_PASSWORD", "LAKEBASE_PORT")


def refuse_connection_overrides(environ: Mapping[str, str]) -> None:
    """Refuse a connection override: the handover reaches the profile's instance, or the named throwaway."""

    present = [name for name in CONNECTION_OVERRIDES if str(environ.get(name, "") or "").strip()]
    if present:
        raise LakebaseHandoverRefused(
            f"unset {', '.join(present)}: the handover connects only to the instance the profile "
            "(or --rehearse-instance) names, never to an override"
        )


@dataclass(frozen=True)
class Session:
    """How the CLI reaches Lakebase and the workspace (injected by tests)."""

    read_session: ReadSession
    connect: Callable[[], Any]
    workspace: Callable[[], Any]
    target: str
    live_instance: str
    app_name: str = ""
    rollback_scope: str = ""


def _profile_session(instance: str = "", database: str = "") -> Session:
    """The profile's install; the Lakebase credential is minted only on first use."""

    from functools import cache

    from jobs.lakebase_migration_connection import _resolve_connection
    from jobs.lakebase_migration_read_only import read_only_session
    from tools.clients.environment import from_local, resolve
    from tools.databricks.workspace_auth import deployment_workspace_client

    refuse_connection_overrides(os.environ)
    profile = resolve(from_local(os.environ, REPO / ".env.local"))

    @cache
    def kwargs() -> dict[str, Any]:
        return _resolve_connection(
            instance_name=instance or profile.lakebase_instance,
            database_name=database or profile.lakebase_database,
        )

    def connect() -> Any:
        import psycopg

        return psycopg.connect(**kwargs(), autocommit=True)

    return Session(
        read_session=lambda: read_only_session(kwargs()),
        connect=connect,
        workspace=deployment_workspace_client,
        target=profile.identity_ids.deployer,
        live_instance=profile.lakebase_instance,
        app_name=profile.app_name,
        rollback_scope=profile.app_rollback_secret_scope,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    modes = (
        "--plan",
        "--apply",
        "--revert",
        "--preconditions",
        "--retire-check",
        "--rehearse",
        "--window-context",
        "--window-open",
        "--in-window",
        "--window-close",
        "--remainder",
    )
    for flag in modes:
        mode.add_argument(flag, action="store_true")
    parser.add_argument("--ack-plan", default="")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--window-sha256", default="")
    parser.add_argument("--handover-sha256", default="")
    parser.add_argument("--window-plan", type=Path)
    parser.add_argument("--legacy-identity", default="")
    parser.add_argument("--rehearse-instance", default="")
    parser.add_argument("--rehearse-database", default="")
    parser.add_argument("--report-out", type=Path)
    return parser


def main(argv: list[str] | None = None, *, session: Callable[..., Session] = _profile_session, clock: Clock = SYSTEM_CLOCK) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    needs_journal = args.apply or args.revert or args.window_open or args.in_window or args.window_close or args.remainder
    if needs_journal and not args.journal:
        parser.error("this mode needs --journal <path>")
    if args.apply and not args.ack_plan:
        parser.error("--apply needs --ack-plan <sha256>")
    if (args.window_open or args.in_window) and not (args.window_sha256 and args.window_plan):
        parser.error("the window modes need --window-sha256 and --window-plan (the private plan plan-wr5 wrote)")
    # Only a journal this run creates can describe a partial apply.
    fresh_journal = bool(args.apply) and not os.path.lexists(args.journal)
    try:
        if args.rehearse:
            from tools.clients.lakebase_handover_rehearse import run_rehearsal

            return run_rehearsal(args, session=session, clock=clock)
        current = session()
        if args.window_context:
            # The WR5 entry's names (scripts/lakebase_handover_window.sh); no credential is minted.
            values = {"WR5_APP_NAME": current.app_name, "WR5_SCOPE": current.rollback_scope, "WR5_LAKEBASE_INSTANCE": current.live_instance}
            print("".join(f"{name}={shlex.quote(value)}\n" for name, value in values.items()), end="")
            return 0
        if args.preconditions or args.retire_check:
            from tools.clients.lakebase_handover_roles import run_preconditions, run_retire_check

            return run_preconditions(args, current) if args.preconditions else run_retire_check(args, current)
        if args.plan or args.apply:
            computed = plan(current.read_session, target=current.target)
            for line in _iter_lines(computed):
                print(line, file=sys.stderr if "REFUSED" in line else sys.stdout)
            if args.report_out is not None:
                journals.write_private(args.report_out, {**computed.document, "plan_sha256": computed.sha256, "refusals": computed.refusals})
            if computed.refusals:
                return 2
            if args.plan:
                print(f"{_PREFIX} read-only; to apply, re-run with --apply --ack-plan {computed.sha256} --journal <new path>")
                return 0
            journal = apply(current.read_session, current.connect, target=current.target, ack=args.ack_plan, journal_path=args.journal, clock=clock)
            return _report_outcome(journal)
        if not args.revert:
            from tools.clients.lakebase_handover_window import run_window_mode

            return run_window_mode(args, current, clock=clock)
        journal = journals.load(args.journal, kind=KIND)
        document, refusals = revert_plan(current.read_session, journal)
        digest = journals.plan_sha256(document, domain=REVERT_DOMAIN)
        print(f"{_PREFIX} revert plan: {len(document['moves'])} moves, {len(document['noops'])} unmoved, sha256 {digest}")
        if refusals:
            print(f"{_PREFIX} REFUSED owner_changed x{len(refusals)}: nothing was reverted", file=sys.stderr)
            return 2
        if not args.ack_plan:
            print(f"{_PREFIX} read-only; to revert, re-run with --ack-plan {digest}")
            return 0
        finished = revert(current.read_session, current.connect, journal, ack=args.ack_plan, clock=clock)
        print(f"{_PREFIX} revert: {json.dumps(journal.counts(), sort_keys=True)}")
        if not finished:
            print(f"{_PREFIX} the lock budget ran out; re-run --revert (it resumes from the journal)", file=sys.stderr)
        return 0 if finished else EXHAUSTED_EXIT
    except Exception as exc:  # noqa: BLE001 - the class only: a driver message can name a role
        known = (LakebaseHandoverRefused, journals.JournalRefused)
        print(f"{_PREFIX} REFUSED: {exc if isinstance(exc, known) else type(exc).__name__}", file=sys.stderr)
        if fresh_journal and _moved_any(args.journal):
            print(f"{_PREFIX} the apply stopped part-way; the journal names what moved. Revert with --revert --journal <path>", file=sys.stderr)
            return 3
        return 2


def _moved_any(path: Path) -> bool:
    try:
        journal = journals.load(path, kind=KIND)
    except journals.JournalRefused:
        return False
    return any(entry["state"] in journals.MAY_HAVE_LANDED for entry in journal.entries)


def _report_outcome(journal: journals.Journal) -> int:
    print(f"{_PREFIX} apply: {journal.status} {json.dumps(journal.counts(), sort_keys=True)}")
    if journal.status != "exhausted":
        return 0
    print(
        f"{_PREFIX} the lock budget ran out with nothing half-applied. Choose one:\n"
        f"  (a) an announced WR5 window: scripts/lakebase_handover_window.sh plans it read-only, "
        f"stops the App, applies the rest of this journal and starts the App (docs/deployment.md);\n"
        f"  (b) the exact inverse: --revert --journal <path>",
        file=sys.stderr,
    )
    return EXHAUSTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
