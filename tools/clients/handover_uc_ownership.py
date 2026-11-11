"""Hand the install's Unity Catalog objects to the data-owner group: plan, apply, revert (M2-PR8).

Run as the legacy owner, at cutover step 7 (docs/deployment.md, "Ownership
handover"):

  python -m tools.clients.handover_uc_ownership --plan [--report-out F]
  python -m tools.clients.handover_uc_ownership --apply --ack-plan <sha256> --journal J
  python -m tools.clients.handover_uc_ownership --revert --journal J [--ack-plan <sha256>]
  python -m tools.clients.handover_uc_ownership --verify [--released]

``--plan`` is read-only (tools/clients/handover_uc_plan.py) and prints the
plan's sha256. The owner hardware-key operation attestation is deferred, so
``--apply`` needs that exact digest: it recomputes the plan immediately before
its first mutation and refuses, with nothing changed, unless the digests are
equal and the plan has no refusal. It then journals every object (mode 0600,
at the explicit ``--journal`` path, which must not exist) before and after
its one typed update with only ``owner`` set, after a compare-and-swap read of
the live owner. The first failure stops the apply (exit 3), and the journal
names exactly what moved.

``--revert`` is the exact inverse from that journal, also after a partial
failure: each object that may have moved goes back to its journaled prior
owner, catalog first, and only while its live owner is still the one the
handover gave it. Without ``--ack-plan`` it prints the revert plan's digest
and changes nothing.

``--verify`` is read-only: nothing the plan would move is still the legacy
owner's, every pipeline-managed dataset the legacy owner still owns has an
exact, unexpired interim owner pair in the client's profile, and every
pipeline-managed dataset is owned by its pipeline's live run_as and that
identity is approved: the jobs principal (``MIP_IDENTITY_IDS_JSON.jobs``), or
the legacy owner until the release (tools/clients/handover_pipeline_owner.py).
Spike S4: no ALTER can re-own a pipeline dataset; its owner follows the
pipeline's run_as, so after the release it is the jobs identity's for good.
``--verify --released``, after the release, no longer admits the legacy owner
even with a pair: its PASS proves the end state, every pipeline dataset in
the governed catalog owned by the jobs principal, which its pipeline runs as.

The metastore, Delta Sharing, foreign and system catalogs and System-user
objects are never touched. Printed lines carry codes, counts and digests;
names and owners go only to the private report and the journal.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from tools.clients import handover_journal as journals
from tools.clients.handover_pipeline_owner import owner_codes
from tools.clients.handover_uc_plan import (
    HandoverRefused,
    UcPlan,
    compute,
    reverse_order,
    suggested_expiry,
)
from tools.databricks.uc_interim_owner_pairs import MATCH, InterimOwnerPairs, InterimPairRefusal

REPO = Path(__file__).resolve().parents[2]
KIND = "uc"
REVERT_SCHEMA = "mip.uc-ownership-handover.revert.v1"
REVERT_DOMAIN = "mip-uc-ownership-handover-revert"
_PREFIX = "[mip-uc-handover]"

# Each change kind's read and its one owner-only update: nothing else mutates.
READERS: dict[str, Callable[[Any, str], object]] = {
    "CATALOG": lambda ws, name: ws.catalogs.get(name),
    "SCHEMA": lambda ws, name: ws.schemas.get(name),
    "TABLE": lambda ws, name: ws.tables.get(name),
    "VIEW": lambda ws, name: ws.tables.get(name),
    "VOLUME": lambda ws, name: ws.volumes.read(name),
    "FUNCTION": lambda ws, name: ws.functions.get(name),
    "REGISTERED_MODEL": lambda ws, name: ws.registered_models.get(name),
}
UPDATERS: dict[str, Callable[[Any, str, str], object]] = {
    "CATALOG": lambda ws, name, owner: ws.catalogs.update(name, owner=owner),
    "SCHEMA": lambda ws, name, owner: ws.schemas.update(name, owner=owner),
    "TABLE": lambda ws, name, owner: ws.tables.update(name, owner=owner),
    "VIEW": lambda ws, name, owner: ws.tables.update(name, owner=owner),
    "VOLUME": lambda ws, name, owner: ws.volumes.update(name, owner=owner),
    "FUNCTION": lambda ws, name, owner: ws.functions.update(name, owner=owner),
    "REGISTERED_MODEL": lambda ws, name, owner: ws.registered_models.update(name, owner=owner),
}


@dataclass(frozen=True)
class Install:
    """What the client's profile decides: the catalogs, the target group, the pairs, the jobs principal."""

    catalog: str
    sync_catalog: str | None
    target: str
    pairs: InterimOwnerPairs
    # MIP_IDENTITY_IDS_JSON.jobs: the one permanent owner of a pipeline dataset (spike S4).
    jobs_principal: str


def live_owner(workspace: Any, kind: str, full_name: str) -> str:
    return str(getattr(READERS[kind](workspace, full_name), "owner", "") or "").strip()


def plan(workspace: Any, install: Install, *, interim_expires: date | None = None) -> UcPlan:
    legacy = str(getattr(workspace.current_user.me(), "user_name", "") or "").strip()
    if not legacy:
        raise HandoverRefused("the caller has no user name to hand objects over from")
    return compute(
        workspace,
        catalog=install.catalog,
        sync_catalog=install.sync_catalog,
        target=install.target,
        legacy_owner=legacy,
        pairs=install.pairs,
        interim_expires=interim_expires,
    )


def _expect(workspace: Any, entry: dict[str, Any], owner: str) -> None:
    """The compare of the compare-and-swap: nothing is sent unless the live owner is ``owner``."""

    if live_owner(workspace, entry["kind"], entry["full_name"]) != owner:
        raise HandoverRefused("an object's live owner is not the one the journal expects")


def _swap(workspace: Any, entry: dict[str, Any], owner: str) -> None:
    """The one owner-only update, then a read-back of exactly that owner."""

    UPDATERS[entry["kind"]](workspace, entry["full_name"], owner)
    if live_owner(workspace, entry["kind"], entry["full_name"]) != owner:
        raise HandoverRefused("an object's owner did not read back as the one it was given")


def apply(workspace: Any, install: Install, *, ack: str, journal_path: Path) -> journals.Journal:
    """Recompute, compare with ``ack``, then move each object; raise on the first failure."""

    journals.refuse_existing(journal_path)
    current = plan(workspace, install)
    if current.refusals:
        raise HandoverRefused(f"the recomputed plan refuses ({len(current.refusals)} refusals); nothing was changed")
    if current.sha256 != ack:
        raise HandoverRefused("the recomputed plan is not the acknowledged digest; nothing was changed")
    journal = journals.create(journal_path, kind=KIND, plan_sha256=current.sha256, entries=current.changes)
    for entry in journal.entries:
        journal.mark(entry["key"], "applying", attempts=entry["attempts"] + 1)
        try:
            _expect(workspace, entry, entry["prior_owner"])
        except Exception as exc:
            # Nothing was sent for this object.
            journal.mark(entry["key"], "pending", error=type(exc).__name__)
            journal.set_status("refused")
            raise
        try:
            _swap(workspace, entry, entry["new_owner"])
        except Exception:
            # The update may have landed: the entry stays ``applying`` for revert to judge live.
            journal.set_status("refused")
            raise
        journal.mark(entry["key"], "applied")
    journal.set_status("applied")
    return journal


def revert_plan(workspace: Any, journal: journals.Journal) -> tuple[dict[str, Any], list[str]]:
    """(the canonical revert document, refusal codes): read-only, from the journal and live owners."""

    moves, noops, refusals = [], [], []
    for entry in reverse_order(item for item in journal.entries if item["state"] in journals.MAY_HAVE_LANDED):
        owner = live_owner(workspace, entry["kind"], entry["full_name"])
        if owner == entry["new_owner"]:
            moves.append({"key": entry["key"], "from": owner, "to": entry["prior_owner"]})
        elif owner == entry["prior_owner"]:
            noops.append(entry["key"])
        else:
            refusals.append("owner_changed")
    document = {"schema": REVERT_SCHEMA, "plan_sha256": journal.plan_sha256, "moves": moves, "noops": sorted(noops)}
    return document, refusals


def revert(workspace: Any, journal: journals.Journal, *, ack: str) -> dict[str, int]:
    document, refusals = revert_plan(workspace, journal)
    if refusals:
        raise HandoverRefused("an object changed owner outside the handover; nothing was reverted")
    if journals.plan_sha256(document, domain=REVERT_DOMAIN) != ack:
        raise HandoverRefused("the recomputed revert plan is not the acknowledged digest; nothing was reverted")
    journal.set_status("reverting")
    for key in document["noops"]:
        journal.mark_unmoved(key)
    for move in document["moves"]:
        entry = journal.entry(move["key"])
        journal.mark(move["key"], "reverting")
        _expect(workspace, entry, entry["new_owner"])
        _swap(workspace, entry, entry["prior_owner"])
        journal.mark(move["key"], "reverted", reverted="moved")
    journal.set_status("reverted")
    return {"moved": len(document["moves"]), "noop": len(document["noops"])}


def verify(workspace: Any, install: Install, *, released: bool = False) -> list[str]:
    """Value-free refusal codes: nothing left to move, every pipeline dataset paired and owned as approved.

    ``released``: the release moved the pipeline's run_as, so the legacy owner
    is no longer an approved owner of a pipeline dataset, pair or not.
    """

    current = plan(workspace, install)
    codes = [refusal.code for refusal in current.refusals]
    codes += ["legacy_owned"] * len(current.changes)
    for row in current.document["pipeline_managed"]:
        if install.pairs.verdict(full_name=row["full_name"], owner=row["owner"]) != MATCH:
            codes.append("interim_pair_missing")
    interim = "" if released else str(current.document["legacy_owner"])
    return codes + owner_codes(
        workspace, current.pipeline_datasets, jobs_principal=install.jobs_principal, interim_owner=interim
    )


def _profile_install() -> Install:
    from datetime import UTC, datetime

    from tools.clients.bootstrap_roster import data_owners_group
    from tools.clients.environment import from_local, resolve
    from tools.databricks.uc_interim_owner_pairs import build_pairs

    profile = resolve(from_local(os.environ, REPO / ".env.local"))
    return Install(
        catalog=profile.default_catalog,
        sync_catalog=profile.lakebase_sync_catalog,
        target=data_owners_group(profile.identity_prefix),
        pairs=build_pairs(profile.interim_owner_pairs, now=datetime.now(UTC)),
        jobs_principal=profile.identity_ids.jobs,
    )


def _workspace() -> Any:
    from tools.databricks.workspace_auth import deployment_workspace_client

    return deployment_workspace_client()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("--plan", "--apply", "--revert", "--verify"):
        mode.add_argument(flag, action="store_true")
    parser.add_argument("--ack-plan", default="")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--interim-days", type=int, default=14)
    parser.add_argument("--released", action="store_true", help="with --verify: the end state after the release")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    install: Callable[[], Install] = _profile_install,
    workspace_factory: Callable[[], Any] = _workspace,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.apply and not (args.ack_plan and args.journal):
        parser.error("--apply needs --ack-plan <sha256> and --journal <path>")
    if args.revert and not args.journal:
        parser.error("--revert needs --journal <path>")
    if args.released and not args.verify:
        parser.error("--released is a --verify mode")
    # Only a journal this run creates can describe a partial apply.
    fresh_journal = bool(args.apply) and not os.path.lexists(args.journal)
    try:
        workspace = workspace_factory()
        if args.revert:
            journal = journals.load(args.journal, kind=KIND)
            document, refusals = revert_plan(workspace, journal)
            digest = journals.plan_sha256(document, domain=REVERT_DOMAIN)
            print(f"{_PREFIX} revert plan: {len(document['moves'])} moves, {len(document['noops'])} unmoved, sha256 {digest}")
            if refusals:
                print(f"{_PREFIX} REFUSED owner_changed x{len(refusals)}: nothing was reverted", file=sys.stderr)
                return 2
            if not args.ack_plan:
                print(f"{_PREFIX} read-only; to revert, re-run with --ack-plan {digest}")
                return 0
            counts = revert(workspace, journal, ack=args.ack_plan)
            print(f"{_PREFIX} reverted: {counts['moved']} moved back, {counts['noop']} never moved")
            return 0
        settings = install()
        if args.verify:
            codes = verify(workspace, settings, released=args.released)
            for code in sorted(set(codes)):
                print(f"{_PREFIX} REFUSED {code} x{codes.count(code)}", file=sys.stderr)
            print(f"{_PREFIX} verify: {'PASS' if not codes else 'FAIL'}")
            return 2 if codes else 0
        current = plan(workspace, settings, interim_expires=suggested_expiry(args.interim_days))
        if args.report_out is not None:
            journals.write_private(args.report_out, current.report())
        print(f"{_PREFIX} plan: {json.dumps(current.public(), sort_keys=True)}")
        for refusal in current.refusals:
            print(f"{_PREFIX} REFUSED {refusal.code} {refusal.where}: {refusal.reason}", file=sys.stderr)
        if current.refusals:
            return 2
        if not args.apply:
            print(f"{_PREFIX} read-only; review the private report, then re-run with --apply --ack-plan {current.sha256} --journal <new path>")
            return 0
        journal = apply(workspace, settings, ack=args.ack_plan, journal_path=args.journal)
    except Exception as exc:  # noqa: BLE001 - the class only: an SDK message can name an object
        known = (HandoverRefused, journals.JournalRefused, InterimPairRefusal)
        detail = str(exc) if isinstance(exc, known) else type(exc).__name__
        print(f"{_PREFIX} REFUSED: {detail}", file=sys.stderr)
        partial = fresh_journal and _applied_any(args.journal)
        if partial:
            print(f"{_PREFIX} the apply stopped part-way; the journal names what moved. Revert with --revert --journal <path>", file=sys.stderr)
        return 3 if partial else 2
    print(f"{_PREFIX} applied: {json.dumps(journal.counts(), sort_keys=True)}")
    return 0


def _applied_any(path: Path) -> bool:
    try:
        journal = journals.load(path, kind=KIND)
    except journals.JournalRefused:
        return False
    return any(entry["state"] in journals.MAY_HAVE_LANDED for entry in journal.entries)


if __name__ == "__main__":
    raise SystemExit(main())
