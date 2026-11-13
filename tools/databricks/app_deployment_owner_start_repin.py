"""Re-pin an owner's break-glass start over an open window, under the next run's lease (wave 7c).

``app_deployment_rollback repin --from-owner-start`` (``app_core_rollback
repin --from-owner-start`` for a v7 core blue): a run ended inside a
restorable quiesce window, the outage could not wait for the dead-man, and the
owner started the App by hand (docs/deployment.md, "Owner break-glass"). The
raw start serves the App but proves nothing, and the window's journal stays
open. This is its one governed exit, run first in step 0a, before the
fail-closed arm, with the dead-man re-pin's own proofs and one thing more, the
owner's exact digest ack:

1. the journal verifies and is classified again under the lease
   (``quiesce_window_recovery.classify``): a restorable open window, a
   journaled baseline that holds blue, the App ACTIVE with nothing pending,
   and exactly one deployment nobody journaled or marked, which the App
   serves, the newest, SUCCEEDED, from blue's restore source, in the shape of
   a start. Anything else refuses with its code;
2. ``MIP_ACK_OWNER_START_REPIN`` equals the ``owner_start_repin`` digest of
   that plan (the window, its lease, its baseline, the dead-man's state and
   that deployment). The API shows a deployment's env NAMES only (spike S7),
   so no read can tell a start from a deploy of the same folder with another
   env value: the ack is what admits it, always;
3. blue's restore folder still has the journal's full-tree digest;
4. the tier's own re-pin (``repin_started_deployment`` or
   ``repin_core_start``): the record names blue (or already this start with
   predecessor blue: a resumed run), the start's ``/src/`` snapshot has that
   digest, its env-name set is the record's, it is current, the live bindings
   are the record's or exactly this run's bundle payload, health holds; then
   the record's next version under this lease, read back, and health again;
5. the journal closes (``closed_by: owner_start_repin``,
   ``repinned_deployment_id`` the owner's start). The journal's own
   ``start_deployment_id``, a governed start that was refused, is kept.

It starts, stops, deploys and updates nothing. A refusal leaves the App
serving the owner's start and the journal open; the run exits 2 before step
0a's signed-blue proof with its lease released. A health failure after the
record's save leaves the record re-pinned, and the same ack resumes at the
journal close. No message names an id, a path or a value; the digest is the
only thing printed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_release_contract import ReleaseContractError
from tools.databricks.app_start_deployment import tree_digest
from tools.databricks.operation_ack import AckRefused, require_ack
from tools.databricks.quiesce_window_recovery import ACK_ENV, PURPOSE, REFUSALS, classify

REFUSAL_CODES = frozenset(
    {
        *REFUSALS,
        "owner_start_usage",
        "owner_start_no_window",
        "owner_start_deadman",
        "owner_start_unacked",
        "owner_start_unproven",
    }
)


class OwnerStartRepinRefused(RuntimeError):
    """The owner's start is not re-pinned; the record and the journal are as they were."""

    def __init__(self, code: str, message: str) -> None:
        if code not in REFUSAL_CODES:
            raise ValueError("owner-start re-pin refusal code is not reviewed")
        super().__init__(message)
        self.code = code


_WHY = {
    "owner_start_contract_window": "a contract window is never restarted from blue",
    "owner_start_marker_unreadable": "the window's dead-man marker is not this window's or cannot be read",
    "owner_start_no_baseline": "the window journaled no deployment baseline that holds blue",
    "owner_start_not_running": "the App is not ACTIVE with an active deployment and nothing pending",
    "owner_start_none": "no deployment appeared since the window opened but the ones it journaled or marked",
    "owner_start_ambiguous": "more than one unexplained deployment appeared since the window opened",
    "owner_start_not_current": "the new deployment is not the App's newest, SUCCEEDED, from blue's restore source",
    "owner_start_not_a_start": "the new deployment does not have the shape of a start of its predecessor",
    "owner_start_no_window": "no window is open: there is nothing to re-pin",
    "owner_start_deadman": "the App serves the dead-man's own restart: that is repin --from-deadman's",
}


def repin_from_owner_start(
    workspace: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    repin: Callable[..., dict[str, Any]],
    assert_lease_held: Callable[[], None],
    acknowledgement: str,
    applied_app_resources: list[dict[str, object]] | None = None,
    digest: Callable[[Any, str], str] = tree_digest,
) -> dict[str, Any]:
    """Classify, require the exact ack, prove and re-pin the owner's start, then close the journal."""

    if not deployment_lease_id or not deployment_source_git_sha:
        raise OwnerStartRepinRefused("owner_start_usage", "the re-pin runs only under this run's lease")
    current = journal.read(workspace, app_name=app_name)
    found = classify(workspace, app_name=app_name, current=current)
    if found.kind != "owner_start" or found.body is None or current is None:
        code = found.owner_start if found.owner_start in REFUSALS else "owner_start_no_window"
        code = "owner_start_deadman" if found.kind == "deadman" else code
        raise OwnerStartRepinRefused(code, _WHY[code])
    try:
        require_ack(PURPOSE, found.body, acknowledgement)
    except AckRefused as exc:
        raise OwnerStartRepinRefused(
            "owner_start_unacked",
            f"{exc.code}: this window's {PURPOSE} plan is {found.sha256}; review it, then dispatch "
            f"the release with operation_acks {PURPOSE}=<that sha256> (locally: {ACK_ENV}, for "
            "that run only)",
        ) from exc
    start_id, blue_id = str(found.body["start_deployment_id"]), str(current["blue_deployment_id"])
    tree = str(current["source_tree_sha256"])
    try:
        folder = digest(workspace, str(current["restore_source"]))
    except (NotFound, ResourceDoesNotExist, ReleaseContractError) as exc:
        raise OwnerStartRepinRefused(
            "owner_start_unproven", "blue's restore folder is gone or unreadable"
        ) from exc
    if not tree or folder != tree:
        raise OwnerStartRepinRefused(
            "owner_start_unproven", "blue's restore folder changed since the window opened"
        )
    record = repin(
        deployment_id=start_id,
        expected_rollback_deployment_id=blue_id,
        expected_tree_sha256=tree,
        applied_app_resources=applied_app_resources,
    )
    if record.get("deployment_id") != start_id:
        raise OwnerStartRepinRefused(
            "owner_start_unproven", "the re-pinned record does not name the owner's start"
        )
    journal.write(
        workspace,
        app_name=app_name,
        expected=current,
        changes={
            "state": "closed",
            "repinned_deployment_id": start_id,
            "closed_by": "owner_start_repin",
        },
        assert_lease_held=assert_lease_held,
    )
    return record


def repin_owner_start(
    workspace: Any,
    *,
    app_name: str,
    scope: str,
    base_url: str,
    bearer_token: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    acknowledgement: str,
    applied_app_resources: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    """The CLI's ``repin --from-owner-start``: the rollback's own re-pin, then the journal close."""

    from tools.databricks import app_deployment_rollback as rollback

    def repin(**proofs: Any) -> dict[str, Any]:
        return rollback.repin_started_deployment(
            workspace,
            app_name=app_name,
            scope=scope,
            base_url=base_url,
            bearer_token=bearer_token,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
            **proofs,
        )

    return repin_from_owner_start(
        workspace,
        app_name=app_name,
        deployment_lease_id=deployment_lease_id,
        deployment_source_git_sha=deployment_source_git_sha,
        repin=repin,
        assert_lease_held=rollback.held_assertion(
            workspace,
            app_name=app_name,
            lease_id=deployment_lease_id,
            source_git_sha=deployment_source_git_sha,
            operation="owner-start window close",
        ),
        acknowledgement=acknowledgement,
        applied_app_resources=applied_app_resources,
    )
